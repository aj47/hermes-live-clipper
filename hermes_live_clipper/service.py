from __future__ import annotations

import json
import os
import shutil
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .config import Settings
from .database import Database
from .models import CandidateState, JobState
from .resolvers import normalize_url
from .resources import metrics


class LiveClipperService:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or Settings.load()
        self.settings.ensure()
        self.db = Database(self.settings.root / "clipper.db")
        self._lock = threading.RLock()

    def add_job(self, url: str, start_mode: str = "live_edge") -> dict[str, Any]:
        identity = normalize_url(url)
        if start_mode not in {"live_edge", "from_start"}:
            raise ValueError("start_mode must be live_edge or from_start")
        with self._lock:
            active = [
                job
                for job in self.db.jobs()
                if job["state"]
                in {
                    JobState.WAITING,
                    JobState.CAPTURING,
                    JobState.RECONNECTING,
                    JobState.FINALIZING,
                }
            ]
            job = self.db.create_job(identity.canonical_url, start_mode)
            self.db.execute(
                "UPDATE jobs SET canonical_url=?,provider=?,external_id=? WHERE id=?",
                (identity.canonical_url, identity.provider, identity.external_id, job["id"]),
            )
            if not active:
                self.db.set_job_state(job["id"], JobState.WAITING)
            return self.db.job(job["id"])

    def stop_job(self, job_id: str) -> dict[str, Any]:
        self.db.execute("UPDATE jobs SET stop_requested=1 WHERE id=?", (job_id,))
        self.db.set_job_state(job_id, JobState.STOPPED)
        return self.db.job(job_id)

    def delete_job(self, job_id: str) -> None:
        self.db.job(job_id)
        shutil.rmtree(self.settings.root / "jobs" / job_id, ignore_errors=True)
        self.db.execute("DELETE FROM jobs WHERE id=?", (job_id,))

    def update_candidate(
        self, candidate_id: str, start_word_id: int, end_word_id: int
    ) -> dict[str, Any]:
        candidate = self.db.candidate(candidate_id)
        rows = self.db.execute(
            "SELECT id,start_seconds,end_seconds FROM words WHERE job_id=? AND id IN (?,?)",
            (candidate["job_id"], start_word_id, end_word_id),
        ).fetchall()
        lookup = {row["id"]: row for row in rows}
        if start_word_id not in lookup or end_word_id not in lookup:
            raise ValueError("word boundary is not part of this job")
        start, end = lookup[start_word_id]["start_seconds"], lookup[end_word_id]["end_seconds"]
        if end <= start:
            raise ValueError("end boundary must follow start boundary")
        self.db.execute(
            "UPDATE candidates SET start_seconds=?,end_seconds=?,start_word_id=?,end_word_id=?,state=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (start, end, start_word_id, end_word_id, CandidateState.SUGGESTED, candidate_id),
        )
        return self.db.candidate(candidate_id)

    def candidate_action(self, candidate_id: str, action: str) -> dict[str, Any]:
        states = {
            "accept": CandidateState.ACCEPTED,
            "reject": CandidateState.REJECTED,
            "render": CandidateState.RENDER_QUEUED,
            "delete": CandidateState.DELETED,
        }
        if action not in states:
            raise ValueError("unknown candidate action")
        self.db.set_candidate_state(candidate_id, states[action])
        return self.db.candidate(candidate_id)

    def status(self) -> dict[str, Any]:
        jobs = self.db.jobs()
        usage = metrics(str(self.settings.root))
        usage["workspace_bytes"] = sum(
            path.stat().st_size for path in self.settings.root.rglob("*") if path.is_file()
        )
        analyzer = self.db.execute(
            "SELECT heartbeat_at >= datetime('now','-60 seconds') available "
            "FROM runtime WHERE component='analyzer'"
        ).fetchone()
        return {
            "jobs": jobs,
            "resources": usage,
            "llm_available": bool(analyzer and analyzer["available"]),
        }

    def detail(self, job_id: str) -> dict[str, Any]:
        return {
            "job": self.db.job(job_id),
            "words": self.db.words(job_id),
            "candidates": self.db.candidates(job_id),
            "renders": self.renders(job_id),
        }

    def renders(self, job_id: str) -> list[dict[str, Any]]:
        rows = self.db.execute(
            "SELECT r.*,c.job_id,c.title,c.start_seconds,c.end_seconds,c.confidence,"
            "c.state candidate_state,h.status publisher_status,h.task_id publisher_task_id "
            "FROM renders r JOIN candidates c ON c.id=r.candidate_id "
            "LEFT JOIN publisher_handoffs h ON h.render_id=r.id "
            "WHERE c.job_id=? ORDER BY r.created_at DESC,r.version DESC",
            (job_id,),
        ).fetchall()
        renders = []
        for row in rows:
            item = dict(row)
            path = Path(item.pop("path"))
            item["size_bytes"] = path.stat().st_size if path.exists() else None
            result_path = (
                self.settings.root / "publisher_outbox" / item["id"] / "publisher-result.json"
            )
            item["publisher_result"] = self._read_publisher_result(result_path)
            renders.append(item)
        return renders

    @staticmethod
    def _read_publisher_result(path: Path) -> dict[str, Any] | None:
        if not path.is_file():
            return None
        try:
            result = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            return {"status": "invalid", "summary": "Publisher result could not be read"}
        if not isinstance(result, dict):
            return {"status": "invalid", "summary": "Publisher result is malformed"}
        allowed = {"published", "partial", "blocked", "upload_ready", "failed"}
        if result.get("status") not in allowed:
            return {"status": "invalid", "summary": "Publisher result has an unknown status"}
        if result["status"] == "published":
            platforms = result.get("platforms")
            if not isinstance(platforms, list):
                return {"status": "invalid", "summary": "Published result has no receipts"}
            receipts = {
                entry.get("platform"): entry
                for entry in platforms
                if isinstance(entry, dict)
                and entry.get("status") == "published"
                and (entry.get("url") or entry.get("receipt"))
            }
            if not {"youtube", "tiktok"}.issubset(receipts):
                return {
                    "status": "invalid",
                    "summary": "Published result is missing a TikTok or YouTube receipt",
                }
        return result

    def prepare_publisher_handoff(self, render_id: str) -> dict[str, Any]:
        row = self.db.execute(
            "SELECT r.*,c.job_id,c.title,c.start_seconds,c.end_seconds,c.confidence,"
            "j.canonical_url,j.provider FROM renders r "
            "JOIN candidates c ON c.id=r.candidate_id JOIN jobs j ON j.id=c.job_id "
            "WHERE r.id=? AND r.state='ready'",
            (render_id,),
        ).fetchone()
        if not row:
            raise KeyError(render_id)

        item = dict(row)
        source = Path(item["path"]).resolve()
        root = self.settings.root.resolve()
        if root not in source.parents or not source.is_file():
            raise FileNotFoundError("Render file is missing")

        with self._lock:
            outbox = self.settings.root / "publisher_outbox" / render_id
            outbox.mkdir(parents=True, exist_ok=True)
            clip_path = outbox / "clip.mp4"
            if not clip_path.exists() or clip_path.stat().st_size != source.stat().st_size:
                temporary = outbox / ".clip.mp4.tmp"
                shutil.copy2(source, temporary)
                os.replace(temporary, clip_path)

            now = datetime.now(UTC).isoformat()
            result_path = outbox / "publisher-result.json"
            metadata = {
                "asset_id": f"live-clipper-{render_id}",
                "render_id": render_id,
                "job_id": item["job_id"],
                "title": item["title"],
                "version": item["version"],
                "duration": item["duration"],
                "source_url": item["canonical_url"],
                "clip_path": str(clip_path),
                "prepared_at": now,
            }
            metadata_tmp = outbox / ".handoff.json.tmp"
            metadata_tmp.write_text(json.dumps(metadata, indent=2) + "\n")
            os.replace(metadata_tmp, outbox / "handoff.json")
            self.db.execute(
                "INSERT INTO publisher_handoffs(render_id,outbox_path,status) VALUES(?,?,'prepared') "
                "ON CONFLICT(render_id) DO UPDATE SET outbox_path=excluded.outbox_path,"
                "updated_at=CURRENT_TIMESTAMP",
                (render_id, str(clip_path)),
            )

        task_body = f"""Publish this specific Live Clipper render to the signed-in local TikTok and YouTube accounts.

This task was created only after the user confirmed a dashboard warning that the Hermes publisher may upload and publish this MP4 to both platforms.

Immutable source MP4: {clip_path}
Handoff metadata: {outbox / "handoff.json"}
Publisher result file: {result_path}
Render id: {render_id}
Source URL: {item["canonical_url"]}
Suggested title: {item["title"]}

Execution requirements:
1. Inspect the MP4 with ffprobe before uploading. Use it as-is; do not crop or overwrite it.
2. Create concise platform-appropriate titles/descriptions. Do not invent claims beyond the clip.
3. For YouTube, use the installed youtube-upload workflow when configured. Verify the resulting video ID, URL, and visibility.
4. For TikTok, use browser/computer-use with the already signed-in local Chrome account. Stop as blocked if login, 2FA, account selection, or a final human decision is required.
5. Never claim success from an upload attempt or process exit alone. Verify each real platform receipt.
6. Do not expose credentials, cookies, or temporary upload tokens in logs or artifacts.
7. Atomically write {result_path} as JSON before completing. Use a temporary sibling file and rename it. Schema:
   {{"status":"published|partial|blocked|upload_ready|failed","summary":"...","platforms":[{{"platform":"youtube|tiktok","status":"published|blocked|failed","url":"verified public or studio URL when available","receipt":"video/post id or concise verification"}}],"completed_at":"ISO-8601"}}
8. Set status=published only when both YouTube and TikTok have verified receipts. Use partial when exactly one succeeded.

Final response must repeat the truthful status, receipts, and any action the user must take."""

        return {
            "render_id": render_id,
            "outbox_path": str(clip_path),
            "task": {
                "title": f"Publish Live Clipper render: {item['title']}",
                "body": task_body,
                "assignee": "default",
                "workspace_kind": "scratch",
                "tenant": "live-clipper",
                "priority": 100,
                "idempotency_key": f"live-clipper-publisher:{render_id}",
                "max_runtime_seconds": 3600,
                "skills": ["youtube-upload"],
                "goal_mode": True,
                "goal_max_turns": 12,
            },
        }

    def complete_publisher_handoff(
        self, render_id: str, task_id: str | None = None
    ) -> dict[str, Any]:
        row = self.db.execute(
            "SELECT render_id FROM publisher_handoffs WHERE render_id=?", (render_id,)
        ).fetchone()
        if not row:
            raise KeyError(render_id)
        self.db.execute(
            "UPDATE publisher_handoffs SET status='queued',task_id=?,"
            "updated_at=CURRENT_TIMESTAMP WHERE render_id=?",
            (task_id, render_id),
        )
        return {"render_id": render_id, "status": "queued", "task_id": task_id}

    def reconcile_for_worker_start(self) -> None:
        """Recover jobs only when a newly-exclusive worker actually starts."""
        for job in self.db.jobs():
            if job["state"] in {JobState.CAPTURING, JobState.RECONNECTING, JobState.FINALIZING}:
                self.db.set_job_state(
                    job["id"], JobState.NEEDS_ATTENTION, "Worker stopped; resume required"
                )


_SERVICE: LiveClipperService | None = None


def get_service() -> LiveClipperService:
    global _SERVICE
    if _SERVICE is None:
        _SERVICE = LiveClipperService()
    return _SERVICE
