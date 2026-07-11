from __future__ import annotations

import json
import os
import signal
import subprocess
import threading
import time
from pathlib import Path

from .models import CandidateState, JobState
from .rendering import RenderError, render_clip
from .resolvers import ResolveError, YtDlpResolver
from .resources import can_render
from .service import LiveClipperService
from .transcription import TranscriptionError, transcribe_chunk


class Worker:
    def __init__(self, service: LiveClipperService):
        self.service = service
        self.settings = service.settings
        self.resolver = YtDlpResolver()
        self.stop_event = threading.Event()
        self.capture: subprocess.Popen | None = None

    def run_forever(self) -> None:
        lock = self._acquire_singleton()
        try:
            self.service.reconcile_for_worker_start()
            signal.signal(signal.SIGTERM, self._request_shutdown)
            signal.signal(signal.SIGINT, self._request_shutdown)
            while not self.stop_event.is_set():
                job = self._next_job()
                if not job:
                    time.sleep(2)
                    continue
                self._run_job(job)
        finally:
            lock.unlink(missing_ok=True)

    def _request_shutdown(self, *_args) -> None:
        self.stop_event.set()
        if self.capture and self.capture.poll() is None:
            try:
                os.killpg(self.capture.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass

    def _next_job(self) -> dict | None:
        for job in reversed(self.service.db.jobs()):
            if (
                job["state"] in {JobState.QUEUED, JobState.WAITING, JobState.NEEDS_ATTENTION}
                and not job["stop_requested"]
            ):
                return job
        return None

    def _run_job(self, job: dict) -> None:
        job_id = job["id"]
        job_root = self.settings.root / "jobs" / job_id
        chunks_dir = job_root / "chunks"
        chunks_dir.mkdir(parents=True, exist_ok=True)
        while not self.stop_event.is_set() and not self.service.db.job(job_id)["stop_requested"]:
            try:
                resolved = self.resolver.resolve(
                    job["canonical_url"], from_start=job["start_mode"] == "from_start"
                )
                if resolved.live_status in {"is_upcoming", "not_live"} and not resolved.is_live:
                    self.service.db.set_job_state(job_id, JobState.WAITING)
                    time.sleep(20)
                    continue
                self.service.db.execute(
                    "UPDATE jobs SET title=?,started_at=COALESCE(started_at,CURRENT_TIMESTAMP) WHERE id=?",
                    (resolved.title, job_id),
                )
                self.service.db.set_job_state(job_id, JobState.CAPTURING)
                self._capture_and_process(job_id, resolved.media_url, chunks_dir, job_root)
                if self.capture and self.capture.returncode not in {0, 255, None}:
                    raise RuntimeError(f"capture exited with status {self.capture.returncode}")
                break
            except ResolveError as exc:
                self.service.db.set_job_state(job_id, JobState.RECONNECTING, str(exc))
                time.sleep(15)
            except Exception as exc:
                self.service.db.set_job_state(job_id, JobState.RECONNECTING, _safe_message(exc))
                time.sleep(10)
        current = self.service.db.job(job_id)
        if current["stop_requested"]:
            self.service.db.set_job_state(job_id, JobState.STOPPED)
        elif current["state"] not in {JobState.FAILED, JobState.STOPPED}:
            self.service.db.set_job_state(job_id, JobState.FINALIZING)
            self._process_ready_chunks(job_id, chunks_dir, job_root)
            self._render_ready(job_id, job_root)
            self.service.db.execute(
                "UPDATE jobs SET ended_at=CURRENT_TIMESTAMP WHERE id=?", (job_id,)
            )
            self.service.db.set_job_state(job_id, JobState.COMPLETED)

    def _capture_and_process(
        self, job_id: str, media_url: str, chunks_dir: Path, job_root: Path
    ) -> None:
        start_number = self.service.db.execute(
            "SELECT COALESCE(MAX(sequence)+1,0) value FROM chunks WHERE job_id=?", (job_id,)
        ).fetchone()["value"]
        pattern = chunks_dir / "%08d.ts"
        command = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "warning",
            "-y",
            "-i",
            media_url,
            "-map",
            "0:v:0",
            "-map",
            "0:a:0",
            "-c",
            "copy",
            "-f",
            "segment",
            "-segment_time",
            str(self.settings.chunk_seconds),
            "-reset_timestamps",
            "1",
            "-segment_start_number",
            str(start_number),
            str(pattern),
        ]
        log = (job_root / "capture.log").open("a")
        self.capture = subprocess.Popen(command, stdout=log, stderr=log, start_new_session=True)
        try:
            while self.capture.poll() is None and not self.stop_event.is_set():
                if self.service.db.job(job_id)["stop_requested"]:
                    os.killpg(self.capture.pid, signal.SIGTERM)
                    break
                self._process_ready_chunks(job_id, chunks_dir, job_root)
                self._render_ready(job_id, job_root)
                time.sleep(2)
            self.capture.wait(timeout=15)
        finally:
            log.close()

    def _process_ready_chunks(self, job_id: str, chunks_dir: Path, job_root: Path) -> None:
        files = sorted(chunks_dir.glob("*.ts"))
        # The newest segment may still be open while capture is active.
        ready = files[:-1] if self.capture and self.capture.poll() is None else files
        master = job_root / "source.ts"
        for path in ready:
            sequence = int(path.stem)
            exists = self.service.db.execute(
                "SELECT id FROM chunks WHERE job_id=? AND sequence=?", (job_id, sequence)
            ).fetchone()
            if exists:
                continue
            offset = self.service.db.execute(
                "SELECT COALESCE(SUM(duration),0) value FROM chunks WHERE job_id=?", (job_id,)
            ).fetchone()["value"]
            duration = _probe_duration(path)
            cursor = self.service.db.execute(
                "INSERT INTO chunks(job_id,sequence,path,start_seconds,duration,state) VALUES(?,?,?,?,?,'transcribing')",
                (job_id, sequence, str(path), offset, duration),
            )
            with master.open("ab") as output, path.open("rb") as source:
                output.write(source.read())
            try:
                words = transcribe_chunk(path, job_root / "transcripts" / f"{sequence:08d}")
                self.service.db.add_words(job_id, cursor.lastrowid, words, offset)
                self.service.db.execute(
                    "UPDATE chunks SET state='transcribed' WHERE id=?", (cursor.lastrowid,)
                )
            except (TranscriptionError, ValueError) as exc:
                self.service.db.execute(
                    "UPDATE chunks SET state='transcription_failed' WHERE id=?", (cursor.lastrowid,)
                )
                self.service.db.event(
                    job_id,
                    "transcription.failed",
                    {"sequence": sequence, "error": _safe_message(exc)},
                )

    def _render_ready(self, job_id: str, job_root: Path) -> None:
        latest_word = (
            self.service.db.execute(
                "SELECT MAX(end_seconds) value FROM words WHERE job_id=?", (job_id,)
            ).fetchone()["value"]
            or 0
        )
        captured = (
            self.service.db.execute(
                "SELECT COALESCE(SUM(duration),0) value FROM chunks WHERE job_id=?", (job_id,)
            ).fetchone()["value"]
            or 0
        )
        if not can_render(self.settings, max(0, captured - latest_word)):
            return
        candidate = self.service.db.execute(
            "SELECT * FROM candidates WHERE job_id=? AND state IN (?,?) AND confidence>=? ORDER BY CASE state WHEN ? THEN 0 ELSE 1 END,confidence DESC LIMIT 1",
            (
                job_id,
                CandidateState.RENDER_QUEUED,
                CandidateState.SUGGESTED,
                self.settings.min_candidate_confidence,
                CandidateState.RENDER_QUEUED,
            ),
        ).fetchone()
        source = job_root / "source.ts"
        if not candidate or not source.exists() or candidate["end_seconds"] > captured:
            return
        candidate_id = candidate["id"]
        self.service.db.set_candidate_state(candidate_id, CandidateState.RENDERING)
        version = self.service.db.execute(
            "SELECT COALESCE(MAX(version)+1,1) value FROM renders WHERE candidate_id=?",
            (candidate_id,),
        ).fetchone()["value"]
        render_id = f"{candidate_id}-v{version}"
        destination = job_root / "renders" / f"{render_id}.mp4"
        self.service.db.execute(
            "INSERT INTO renders(id,candidate_id,version,path,state) VALUES(?,?,?,?,?)",
            (render_id, candidate_id, version, str(destination), "rendering"),
        )
        try:
            info = render_clip(
                source, destination, candidate["start_seconds"], candidate["end_seconds"]
            )
            duration = float(info["format"]["duration"])
            self.service.db.execute(
                "UPDATE renders SET state='ready',duration=? WHERE id=?", (duration, render_id)
            )
            self.service.db.set_candidate_state(candidate_id, CandidateState.DRAFT_READY)
        except RenderError as exc:
            self.service.db.execute(
                "UPDATE renders SET state='failed',error=? WHERE id=?",
                (_safe_message(exc), render_id),
            )
            self.service.db.set_candidate_state(candidate_id, CandidateState.FAILED)

    def _acquire_singleton(self) -> Path:
        lock = self.settings.root / "run" / "worker.lock"
        try:
            descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.write(descriptor, str(os.getpid()).encode())
            os.close(descriptor)
        except FileExistsError as exc:
            raise RuntimeError("another live-clipper worker is already running") from exc
        return lock


def _probe_duration(path: Path) -> float:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path)],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        return 0.0
    return float(json.loads(result.stdout).get("format", {}).get("duration") or 0)


def _safe_message(error: Exception) -> str:
    return str(error).replace("http://", "[url]").replace("https://", "[url]")[-1000:]
