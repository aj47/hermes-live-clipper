from __future__ import annotations

import json
import shutil
import uuid
from pathlib import Path
from typing import Any

from .config import Settings
from .database import Database
from .models import CandidateState, JobState


class CleanupError(ValueError):
    pass


ACTIVE_JOB_STATES = {
    JobState.QUEUED,
    JobState.WAITING,
    JobState.CAPTURING,
    JobState.RECONNECTING,
    JobState.FINALIZING,
}
ACTIVE_CANDIDATE_STATES = {CandidateState.RENDER_QUEUED, CandidateState.RENDERING}
DELETE_TARGETS = frozenset(
    {
        ("publisher_handoffs", "render_id"),
        ("events", "id"),
        ("renders", "id"),
        ("candidates", "id"),
        ("jobs", "id"),
    }
)


def _rows_by_ids(db: Database, sql: str, ids: set[str]) -> list[dict[str, Any]]:
    if not ids:
        return []
    placeholders = ",".join("?" for _ in ids)
    return [dict(row) for row in db.execute(sql.format(ids=placeholders), tuple(ids)).fetchall()]


def _path_size(path: Path) -> int:
    if not path.exists():
        return 0
    if path.is_file():
        return path.stat().st_size
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())


class CleanupManager:
    def __init__(self, settings: Settings, db: Database):
        self.settings = settings
        self.db = db

    def plan(
        self,
        job_ids: list[str],
        candidate_ids: list[str],
        render_ids: list[str],
        force_publisher_assets: bool = False,
    ) -> dict[str, Any]:
        requested_jobs = set(job_ids)
        requested_candidates = set(candidate_ids)
        requested_renders = set(render_ids)
        if not (requested_jobs or requested_candidates or requested_renders):
            raise CleanupError("Select at least one stream, suggestion, or render")

        jobs = _rows_by_ids(self.db, "SELECT * FROM jobs WHERE id IN ({ids})", requested_jobs)
        candidates = _rows_by_ids(
            self.db,
            "SELECT * FROM candidates WHERE id IN ({ids})",
            requested_candidates,
        )
        renders = _rows_by_ids(
            self.db,
            "SELECT r.*,c.job_id FROM renders r JOIN candidates c ON c.id=r.candidate_id "
            "WHERE r.id IN ({ids})",
            requested_renders,
        )
        if len(jobs) != len(requested_jobs):
            raise CleanupError("One or more selected streams no longer exist")
        if len(candidates) != len(requested_candidates):
            raise CleanupError("One or more selected suggestions no longer exist")
        if len(renders) != len(requested_renders):
            raise CleanupError("One or more selected renders no longer exist")

        if requested_jobs:
            job_candidates = _rows_by_ids(
                self.db,
                "SELECT * FROM candidates WHERE job_id IN ({ids})",
                requested_jobs,
            )
            candidates = list({row["id"]: row for row in [*candidates, *job_candidates]}.values())
        all_candidate_ids = {row["id"] for row in candidates}
        if all_candidate_ids:
            candidate_renders = _rows_by_ids(
                self.db,
                "SELECT r.*,c.job_id FROM renders r JOIN candidates c ON c.id=r.candidate_id "
                "WHERE r.candidate_id IN ({ids})",
                all_candidate_ids,
            )
            renders = list({row["id"]: row for row in [*renders, *candidate_renders]}.values())
        all_render_ids = {row["id"] for row in renders}

        handoffs = _rows_by_ids(
            self.db,
            "SELECT * FROM publisher_handoffs WHERE render_id IN ({ids})",
            all_render_ids,
        )
        active_jobs = [row for row in jobs if row["state"] in ACTIVE_JOB_STATES]
        active_candidates = [row for row in candidates if row["state"] in ACTIVE_CANDIDATE_STATES]
        active_renders = [row for row in renders if row["state"] == "rendering"]
        blocked: list[dict[str, Any]] = []
        blocked.extend(
            {
                "kind": "active_job",
                "id": row["id"],
                "message": "Stop this stream before deleting it.",
            }
            for row in active_jobs
        )
        blocked.extend(
            {
                "kind": "active_candidate",
                "id": row["id"],
                "message": "Wait for this render to finish before deleting the suggestion.",
            }
            for row in active_candidates
        )
        blocked.extend(
            {"kind": "active_render", "id": row["id"], "message": "This render is still running."}
            for row in active_renders
        )
        if handoffs and not force_publisher_assets:
            blocked.extend(
                {
                    "kind": "publisher_asset",
                    "id": row["render_id"],
                    "message": "This render has a publisher handoff. Explicitly include publisher assets to delete it.",
                }
                for row in handoffs
            )

        root = self.settings.root.resolve()
        jobs_root = (root / "jobs").resolve()
        outbox_root = (root / "publisher_outbox").resolve()
        paths: set[Path] = set()
        for row in jobs:
            paths.add((jobs_root / row["id"]).resolve())
        for row in renders:
            if row["job_id"] not in requested_jobs:
                paths.add(Path(row["path"]).resolve())
            paths.add((outbox_root / row["id"]).resolve())
        for path in paths:
            if (
                path != jobs_root
                and path != outbox_root
                and not (jobs_root in path.parents or outbox_root in path.parents)
            ):
                raise CleanupError("Cleanup path escaped the Live Clipper workspace")
        ordered = sorted(paths, key=lambda path: len(path.parts))
        top_level: list[Path] = []
        for path in ordered:
            if not any(parent == path or parent in path.parents for parent in top_level):
                top_level.append(path)
        reclaimable_bytes = sum(_path_size(path) for path in top_level)

        candidate_ids_to_delete = {row["id"] for row in candidates}
        event_ids = []
        for row in self.db.execute("SELECT id,job_id,payload FROM events").fetchall():
            if row["job_id"] in requested_jobs:
                event_ids.append(row["id"])
                continue
            try:
                payload = json.loads(row["payload"])
            except (TypeError, json.JSONDecodeError):
                continue
            if payload.get("candidate_id") in candidate_ids_to_delete:
                event_ids.append(row["id"])

        warnings = []
        if handoffs:
            warnings.append(
                f"{len(handoffs)} selected render(s) have publisher handoffs or preserved outbox assets."
            )
        return {
            "selection": {
                "job_ids": sorted(requested_jobs),
                "candidate_ids": sorted(requested_candidates),
                "render_ids": sorted(requested_renders),
            },
            "counts": {
                "jobs": len(jobs),
                "candidates": len(candidates),
                "renders": len(renders),
                "publisher_handoffs": len(handoffs),
                "events": len(event_ids),
            },
            "reclaimable_bytes": reclaimable_bytes,
            "blocked": blocked,
            "warnings": warnings,
            "force_publisher_assets": force_publisher_assets,
            "_paths": top_level,
            "_job_ids": requested_jobs,
            "_candidate_ids": candidate_ids_to_delete,
            "_render_ids": all_render_ids,
            "_event_ids": set(event_ids),
            "_affected_candidate_ids": {row["candidate_id"] for row in renders}
            - candidate_ids_to_delete,
        }

    @staticmethod
    def public_plan(plan: dict[str, Any]) -> dict[str, Any]:
        return {key: value for key, value in plan.items() if not key.startswith("_")}

    def execute(
        self,
        job_ids: list[str],
        candidate_ids: list[str],
        render_ids: list[str],
        expected_bytes: int,
        force_publisher_assets: bool = False,
    ) -> dict[str, Any]:
        plan = self.plan(
            job_ids, candidate_ids, render_ids, force_publisher_assets=force_publisher_assets
        )
        if plan["blocked"]:
            raise CleanupError(plan["blocked"][0]["message"])
        if int(expected_bytes) != int(plan["reclaimable_bytes"]):
            raise CleanupError("Storage changed since preview; review the deletion again")

        trash_root = self.settings.root / ".trash" / uuid.uuid4().hex
        trash_root.mkdir(parents=True, exist_ok=False)
        staged: list[tuple[Path, Path]] = []
        connection = self.db.connection()
        try:
            for index, source in enumerate(plan["_paths"]):
                if not source.exists():
                    continue
                destination = trash_root / f"{index:04d}-{source.name}"
                source.rename(destination)
                staged.append((source, destination))

            connection.execute("BEGIN IMMEDIATE")
            self._delete_ids(connection, "publisher_handoffs", "render_id", plan["_render_ids"])
            self._delete_ids(connection, "events", "id", plan["_event_ids"])
            self._delete_ids(connection, "renders", "id", plan["_render_ids"])
            self._delete_ids(connection, "candidates", "id", plan["_candidate_ids"])
            self._delete_ids(connection, "jobs", "id", plan["_job_ids"])
            for candidate_id in plan["_affected_candidate_ids"]:
                remaining = connection.execute(
                    "SELECT COUNT(*) value FROM renders WHERE candidate_id=? AND state='ready'",
                    (candidate_id,),
                ).fetchone()["value"]
                if not remaining:
                    connection.execute(
                        "UPDATE candidates SET state=CASE WHEN state='draft_ready' THEN 'suggested' "
                        "ELSE state END,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                        (candidate_id,),
                    )
            connection.execute("COMMIT")
        except Exception:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            for source, staged_path in reversed(staged):
                if staged_path.exists() and not source.exists():
                    staged_path.rename(source)
            shutil.rmtree(trash_root, ignore_errors=True)
            raise

        shutil.rmtree(trash_root, ignore_errors=True)
        result = self.public_plan(plan)
        self.db.event(
            None, "cleanup.completed", {"counts": result["counts"], "bytes": expected_bytes}
        )
        return result

    @staticmethod
    def _delete_ids(connection, table: str, column: str, ids: set[Any]) -> None:
        if not ids:
            return
        if (table, column) not in DELETE_TARGETS:
            raise CleanupError("Unsupported cleanup target")
        placeholders = ",".join("?" for _ in ids)
        # Identifiers are restricted by DELETE_TARGETS; values remain parameterized.
        connection.execute(  # nosec B608
            f"DELETE FROM {table} WHERE {column} IN ({placeholders})", tuple(ids)
        )
