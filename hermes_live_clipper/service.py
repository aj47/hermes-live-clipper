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
            renders.append(item)
        return renders

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
            asset_id = f"live-clipper-{render_id}"
            summary = (
                f"Live Clipper draft from {item['provider']}; "
                f"{float(item['start_seconds']):.1f}-{float(item['end_seconds']):.1f}s, "
                f"confidence {float(item['confidence']):.0%}."
            )
            note = (
                "Live Clipper editor/publisher handoff. "
                f"Use the preserved source MP4 at {clip_path}. "
                "This queues editorial work only; do not mark it uploaded or published "
                "without an actual platform receipt."
            )
            metadata = {
                "asset_id": asset_id,
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

        return {
            "render_id": render_id,
            "outbox_path": str(clip_path),
            "payload": {
                "assetId": asset_id,
                "decision": "continue",
                "asset": {
                    "id": asset_id,
                    "title": item["title"],
                    "type": "shortform",
                    "status": "local MP4 ready for editor/publisher handoff",
                    "risk": "unknown",
                    "summary": summary,
                    "video": str(clip_path),
                    "localPath": str(clip_path),
                    "renderId": render_id,
                    "sourceUrl": item["canonical_url"],
                    "next": [
                        f"Use the local MP4 at {clip_path} as the source artifact.",
                        "Edit and package it for the appropriate channel.",
                        "Do not claim publication until an actual platform receipt exists.",
                    ],
                },
                "record": {
                    "decision": "continue",
                    "note": note,
                    "updatedAt": now,
                    "checks": {},
                },
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
