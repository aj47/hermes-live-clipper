from __future__ import annotations

import json
import os
import shutil
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .cleanup import CleanupManager
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
        preview = self.cleanup_preview([job_id], [], [])
        self.cleanup_execute([job_id], [], [], preview["reclaimable_bytes"])

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
        self.db.clip_activity(
            candidate["job_id"],
            candidate_id,
            "review_desk",
            "boundaries_adjusted",
            "completed",
            "Adjusted the clip to transcript word boundaries.",
            {"start_seconds": start, "end_seconds": end},
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
        candidate = self.db.candidate(candidate_id)
        self.db.set_candidate_state(candidate_id, states[action])
        activity = {
            "accept": ("clip_accepted", "Accepted the suggestion for editorial use."),
            "reject": ("clip_rejected", "Rejected the suggestion from the working set."),
            "render": ("render_requested", "Requested a new draft from the Video Editor."),
            "delete": ("clip_deleted", "Deleted the suggestion."),
        }
        event_action, message = activity[action]
        self.db.clip_activity(
            candidate["job_id"],
            candidate_id,
            "review_desk",
            event_action,
            "queued" if action == "render" else "completed",
            message,
        )
        return self.db.candidate(candidate_id)

    def cleanup_preview(
        self,
        job_ids: list[str],
        candidate_ids: list[str],
        render_ids: list[str],
        force_publisher_assets: bool = False,
    ) -> dict[str, Any]:
        manager = CleanupManager(self.settings, self.db)
        return manager.public_plan(
            manager.plan(
                job_ids,
                candidate_ids,
                render_ids,
                force_publisher_assets=force_publisher_assets,
            )
        )

    def cleanup_execute(
        self,
        job_ids: list[str],
        candidate_ids: list[str],
        render_ids: list[str],
        expected_bytes: int,
        force_publisher_assets: bool = False,
    ) -> dict[str, Any]:
        return CleanupManager(self.settings, self.db).execute(
            job_ids,
            candidate_ids,
            render_ids,
            expected_bytes,
            force_publisher_assets=force_publisher_assets,
        )

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
            "activity": self.clip_activity(job_id),
        }

    def clip_activity(self, job_id: str) -> list[dict[str, Any]]:
        candidates = {item["id"]: item for item in self.db.candidates(job_id)}
        activity: list[dict[str, Any]] = []
        seen: set[tuple[str, str, str | None]] = set()
        rows = self.db.execute(
            "SELECT id,payload,created_at FROM events WHERE job_id=? AND kind='clip.activity' "
            "ORDER BY id",
            (job_id,),
        ).fetchall()
        for row in rows:
            try:
                payload = json.loads(row["payload"])
            except (TypeError, json.JSONDecodeError):
                continue
            candidate_id = payload.get("candidate_id")
            if candidate_id not in candidates:
                continue
            entry = {
                "id": f"event-{row['id']}",
                "candidate_id": candidate_id,
                "render_id": payload.get("render_id"),
                "role": payload.get("role", "system"),
                "action": payload.get("action", "updated"),
                "status": payload.get("status", "completed"),
                "message": payload.get("message", "Clip activity recorded."),
                "details": payload.get("details") or {},
                "created_at": row["created_at"],
            }
            activity.append(entry)
            seen.add((candidate_id, entry["action"], entry["render_id"]))

        for candidate in candidates.values():
            key = (candidate["id"], "clip_suggested", None)
            if key not in seen:
                activity.append(
                    {
                        "id": f"candidate-{candidate['id']}",
                        "candidate_id": candidate["id"],
                        "render_id": None,
                        "role": "story_analyst",
                        "action": "clip_suggested",
                        "status": "completed",
                        "message": "Identified and scored this moment as a standalone clip opportunity.",
                        "details": {"confidence": candidate["confidence"]},
                        "created_at": candidate["created_at"],
                    }
                )

        render_rows = self.db.execute(
            "SELECT r.*,c.job_id FROM renders r JOIN candidates c ON c.id=r.candidate_id "
            "WHERE c.job_id=? ORDER BY r.created_at,r.version",
            (job_id,),
        ).fetchall()
        for row in render_rows:
            action = (
                "render_ready"
                if row["state"] == "ready"
                else "render_failed"
                if row["state"] == "failed"
                else "render_started"
            )
            key = (row["candidate_id"], action, row["id"])
            if key in seen:
                continue
            message = (
                f"Completed render version {row['version']} and verified its audio and video."
                if row["state"] == "ready"
                else f"Render version {row['version']} failed: {row['error'] or 'unknown error'}."
                if row["state"] == "failed"
                else f"Started render version {row['version']}."
            )
            activity.append(
                {
                    "id": f"render-{row['id']}-{row['state']}",
                    "candidate_id": row["candidate_id"],
                    "render_id": row["id"],
                    "role": "video_editor",
                    "action": action,
                    "status": row["state"],
                    "message": message,
                    "details": {"version": row["version"], "duration": row["duration"]},
                    "created_at": row["created_at"],
                }
            )

        handoffs = self.db.execute(
            "SELECT h.*,r.candidate_id FROM publisher_handoffs h "
            "JOIN renders r ON r.id=h.render_id JOIN candidates c ON c.id=r.candidate_id "
            "WHERE c.job_id=?",
            (job_id,),
        ).fetchall()
        for row in handoffs:
            action = "publisher_task_queued" if row["status"] == "queued" else "handoff_prepared"
            key = (row["candidate_id"], action, row["render_id"])
            if key in seen:
                continue
            activity.append(
                {
                    "id": f"handoff-{row['render_id']}-{row['status']}",
                    "candidate_id": row["candidate_id"],
                    "render_id": row["render_id"],
                    "role": "publisher_growth",
                    "action": action,
                    "status": row["status"],
                    "message": "Queued a Hermes publishing task."
                    if row["status"] == "queued"
                    else "Preserved the MP4 for publishing.",
                    "details": {"task_id": row["task_id"]} if row["task_id"] else {},
                    "created_at": row["updated_at"],
                }
            )
            result_path = (
                self.settings.root / "publisher_outbox" / row["render_id"] / "publisher-result.json"
            )
            result = self._read_publisher_result(result_path)
            if result:
                completed_at = result.get("completed_at")
                if not isinstance(completed_at, str):
                    completed_at = datetime.fromtimestamp(
                        result_path.stat().st_mtime, UTC
                    ).isoformat()
                activity.append(
                    {
                        "id": f"publisher-result-{row['render_id']}",
                        "candidate_id": row["candidate_id"],
                        "render_id": row["render_id"],
                        "role": "publisher_growth",
                        "action": "publishing_finished",
                        "status": result["status"],
                        "message": result.get("summary") or "Publishing task finished.",
                        "details": {"platforms": result.get("platforms") or []},
                        "created_at": completed_at,
                    }
                )
        return sorted(activity, key=lambda item: (item["created_at"], item["id"]), reverse=True)

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
            progress_path = (
                self.settings.root / "publisher_outbox" / item["id"] / "publisher-progress.json"
            )
            item["publisher_result"] = self._read_publisher_result(result_path)
            item["publisher_progress"] = self._read_publisher_progress(progress_path)
            renders.append(item)
        return renders

    @staticmethod
    def _read_publisher_progress(path: Path) -> dict[str, Any] | None:
        if not path.is_file():
            return None
        try:
            progress = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            return {
                "status": "invalid",
                "phase": "invalid",
                "percent": 0,
                "message": "Publisher progress could not be read",
            }
        if not isinstance(progress, dict):
            return {
                "status": "invalid",
                "phase": "invalid",
                "percent": 0,
                "message": "Publisher progress is malformed",
            }
        try:
            percent = max(0, min(100, int(progress.get("percent", 0))))
        except (TypeError, ValueError):
            percent = 0
        return {
            "status": str(progress.get("status") or "working")[:40],
            "phase": str(progress.get("phase") or "working")[:60],
            "percent": percent,
            "message": str(progress.get("message") or "Hermes publisher is working.")[:500],
            "platforms": progress.get("platforms")
            if isinstance(progress.get("platforms"), list)
            else [],
            "updated_at": progress.get("updated_at"),
        }

    @staticmethod
    def _write_publisher_progress(path: Path, progress: dict[str, Any]) -> None:
        temporary = path.with_name(f".{path.name}.tmp")
        temporary.write_text(json.dumps(progress, indent=2) + "\n")
        os.replace(temporary, path)

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

        existing_handoff = self.db.execute(
            "SELECT status,task_id FROM publisher_handoffs WHERE render_id=?", (render_id,)
        ).fetchone()
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
            progress_path = outbox / "publisher-progress.json"
            attempt_id = now.replace(":", "-").replace("+", "-")
            for current in (result_path, progress_path):
                if current.exists():
                    archived = outbox / f"{current.stem}-attempt-{attempt_id}{current.suffix}"
                    os.replace(current, archived)
            metadata = {
                "asset_id": f"live-clipper-{render_id}",
                "attempt_id": attempt_id,
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
            self._write_publisher_progress(
                progress_path,
                {
                    "status": "prepared",
                    "phase": "asset_prepared",
                    "percent": 5,
                    "message": "Verified and preserved the MP4 for the Hermes publisher.",
                    "platforms": [
                        {"platform": "youtube", "status": "waiting"},
                        {"platform": "tiktok", "status": "waiting"},
                    ],
                    "updated_at": now,
                },
            )
            self.db.execute(
                "INSERT INTO publisher_handoffs(render_id,outbox_path,status) VALUES(?,?,'prepared') "
                "ON CONFLICT(render_id) DO UPDATE SET outbox_path=excluded.outbox_path,"
                "status='prepared',updated_at=CURRENT_TIMESTAMP",
                (render_id, str(clip_path)),
            )
            if not existing_handoff:
                self.db.clip_activity(
                    item["job_id"],
                    item["candidate_id"],
                    "publisher_growth",
                    "handoff_prepared",
                    "completed",
                    "Preserved the verified MP4 and prepared its publishing brief.",
                    {"version": item["version"]},
                    render_id,
                )

        task_body = f"""Publish this specific Live Clipper render to the signed-in local TikTok and YouTube accounts.

This task was created only after the user confirmed a dashboard warning that the Hermes publisher may upload and publish this MP4 to both platforms.

Immutable source MP4: {clip_path}
Handoff metadata: {outbox / "handoff.json"}
Publisher result file: {result_path}
Publisher progress file: {progress_path}
Render id: {render_id}
Source URL: {item["canonical_url"]}
Suggested title: {item["title"]}

Execution requirements:
1. Inspect the MP4 with ffprobe before uploading. Use it as-is; do not crop or overwrite it.
2. Create concise platform-appropriate titles/descriptions. Do not invent claims beyond the clip.
3. For YouTube, prefer the installed youtube-upload workflow when its OAuth credentials are configured. If API OAuth client secrets or tokens are absent, DO NOT block for that reason: use the computer_use tool against the existing Google Chrome app/window and upload through the already signed-in YouTube Studio session. Verify the resulting video ID, URL, and visibility.
4. For TikTok, you MUST use the computer_use tool against the existing Google Chrome app/window and its signed-in human profile. Do not use browser_navigate, Playwright, or another isolated browser context for an authenticated upload. Those browser tools may only be used for unauthenticated public verification. Stop as blocked only if the existing Chrome session itself requires login, 2FA, account selection, or a final human decision.
5. Never claim success from an upload attempt or process exit alone. Verify each real platform receipt.
6. Do not expose credentials, cookies, or temporary upload tokens in logs or artifacts.
7. Keep {progress_path} current throughout the run. Atomically replace it after every milestone using this schema:
   {{"status":"working|blocked|failed|published","phase":"planning|youtube_uploading|youtube_published|tiktok_uploading|tiktok_published|verifying|complete","percent":0-100,"message":"plain-language current action","platforms":[{{"platform":"youtube|tiktok","status":"waiting|uploading|published|blocked|failed","url":"verified URL when available"}}],"updated_at":"ISO-8601"}}
   Use approximately 15% for planning, 30% for YouTube upload, 50% after verified YouTube publication, 65% for TikTok upload, 85% after verified TikTok publication, 95% while verifying receipts, and 100% only after the final result is written.
8. Atomically write {result_path} as JSON before completing. Use a temporary sibling file and rename it. Schema:
   {{"status":"published|partial|blocked|upload_ready|failed","summary":"...","platforms":[{{"platform":"youtube|tiktok","status":"published|blocked|failed","url":"verified public or studio URL when available","receipt":"video/post id or concise verification"}}],"completed_at":"ISO-8601"}}
9. Set status=published only when both YouTube and TikTok have verified receipts. Use partial when exactly one succeeded.

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
                "skills": ["youtube-upload", "computer-use"],
                "goal_mode": True,
                "goal_max_turns": 12,
            },
        }

    def complete_publisher_handoff(
        self, render_id: str, task_id: str | None = None
    ) -> dict[str, Any]:
        row = self.db.execute(
            "SELECT h.render_id,h.status,h.task_id,r.candidate_id,c.job_id "
            "FROM publisher_handoffs h JOIN renders r ON r.id=h.render_id "
            "JOIN candidates c ON c.id=r.candidate_id WHERE h.render_id=?",
            (render_id,),
        ).fetchone()
        if not row:
            raise KeyError(render_id)
        self.db.execute(
            "UPDATE publisher_handoffs SET status='queued',task_id=?,"
            "updated_at=CURRENT_TIMESTAMP WHERE render_id=?",
            (task_id, render_id),
        )
        progress_path = (
            self.settings.root / "publisher_outbox" / render_id / "publisher-progress.json"
        )
        self._write_publisher_progress(
            progress_path,
            {
                "status": "queued",
                "phase": "queued",
                "percent": 10,
                "message": "Hermes publishing task is queued and waiting for a worker.",
                "platforms": [
                    {"platform": "youtube", "status": "waiting"},
                    {"platform": "tiktok", "status": "waiting"},
                ],
                "updated_at": datetime.now(UTC).isoformat(),
            },
        )
        if row["status"] != "queued" or row["task_id"] != task_id:
            self.db.clip_activity(
                row["job_id"],
                row["candidate_id"],
                "publisher_growth",
                "publisher_task_queued",
                "queued",
                "Started a durable Hermes Publisher & Growth task.",
                {"task_id": task_id} if task_id else {},
                render_id,
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
