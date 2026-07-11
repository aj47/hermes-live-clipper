from __future__ import annotations

import shutil
import threading
from collections.abc import Callable
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
        self.llm_complete_structured: Callable[..., Any] | None = None
        self._lock = threading.RLock()
        self.reconcile()

    def set_llm(self, complete_structured: Callable[..., Any]) -> None:
        self.llm_complete_structured = complete_structured

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
        return {
            "jobs": jobs,
            "resources": usage,
            "llm_available": self.llm_complete_structured is not None,
        }

    def detail(self, job_id: str) -> dict[str, Any]:
        return {
            "job": self.db.job(job_id),
            "words": self.db.words(job_id),
            "candidates": self.db.candidates(job_id),
        }

    def reconcile(self) -> None:
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
