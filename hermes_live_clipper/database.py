from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from pathlib import Path
from typing import Any

from .models import CandidateState, JobState

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS jobs (
 id TEXT PRIMARY KEY, source_url TEXT NOT NULL, canonical_url TEXT, provider TEXT,
 external_id TEXT, title TEXT, state TEXT NOT NULL, start_mode TEXT NOT NULL,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 started_at TEXT, ended_at TEXT, last_error TEXT, stop_requested INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS chunks (
 id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
 sequence INTEGER NOT NULL, path TEXT NOT NULL, start_seconds REAL NOT NULL, duration REAL,
 state TEXT NOT NULL DEFAULT 'captured', created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 UNIQUE(job_id, sequence)
);
CREATE TABLE IF NOT EXISTS words (
 id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
 chunk_id INTEGER REFERENCES chunks(id) ON DELETE CASCADE, start_seconds REAL NOT NULL,
 end_seconds REAL NOT NULL, text TEXT NOT NULL, confidence REAL, revision INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_words_job_time ON words(job_id, start_seconds);
CREATE TABLE IF NOT EXISTS analysis_windows (
 id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
 start_seconds REAL NOT NULL, end_seconds REAL NOT NULL, status TEXT NOT NULL,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, UNIQUE(job_id, start_seconds, end_seconds)
);
CREATE TABLE IF NOT EXISTS candidates (
 id TEXT PRIMARY KEY, job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
 start_seconds REAL NOT NULL, end_seconds REAL NOT NULL, start_word_id INTEGER, end_word_id INTEGER,
 title TEXT NOT NULL, hook TEXT, payoff TEXT, rationale TEXT, confidence REAL NOT NULL,
 standalone_value REAL, state TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_candidates_job ON candidates(job_id, state, confidence DESC);
CREATE TABLE IF NOT EXISTS renders (
 id TEXT PRIMARY KEY, candidate_id TEXT NOT NULL REFERENCES candidates(id) ON DELETE CASCADE,
 version INTEGER NOT NULL, path TEXT NOT NULL, duration REAL, state TEXT NOT NULL,
 error TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, UNIQUE(candidate_id, version)
);
CREATE TABLE IF NOT EXISTS publisher_handoffs (
 render_id TEXT PRIMARY KEY, outbox_path TEXT NOT NULL, status TEXT NOT NULL,
 task_id TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS events (
 id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT, kind TEXT NOT NULL, payload TEXT NOT NULL,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS runtime (
 component TEXT PRIMARY KEY, instance_id TEXT NOT NULL, heartbeat_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""


class Database:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._local = threading.local()
        self.connection().executescript(SCHEMA)

    def connection(self) -> sqlite3.Connection:
        connection = getattr(self._local, "connection", None)
        if connection is None:
            connection = sqlite3.connect(self.path, timeout=30, isolation_level=None)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys=ON")
            self._local.connection = connection
        return connection

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> sqlite3.Cursor:
        return self.connection().execute(sql, params)

    def create_job(self, source_url: str, start_mode: str) -> dict[str, Any]:
        job_id = uuid.uuid4().hex
        self.execute(
            "INSERT INTO jobs(id, source_url, state, start_mode) VALUES(?,?,?,?)",
            (job_id, source_url, JobState.QUEUED, start_mode),
        )
        self.event(job_id, "job.created", {"start_mode": start_mode})
        return self.job(job_id)

    def job(self, job_id: str) -> dict[str, Any]:
        row = self.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if not row:
            raise KeyError(job_id)
        return dict(row)

    def jobs(self) -> list[dict[str, Any]]:
        return [
            dict(row)
            for row in self.execute("SELECT * FROM jobs ORDER BY created_at DESC").fetchall()
        ]

    def set_job_state(self, job_id: str, state: JobState, error: str | None = None) -> None:
        self.execute(
            "UPDATE jobs SET state=?, last_error=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (state, error, job_id),
        )
        self.event(job_id, "job.state", {"state": state, "error": error})

    def words(self, job_id: str, start: float = 0, end: float = 1e15) -> list[dict[str, Any]]:
        rows = self.execute(
            "SELECT * FROM words WHERE job_id=? AND end_seconds>=? AND start_seconds<=? ORDER BY start_seconds,id",
            (job_id, start, end),
        ).fetchall()
        return [dict(row) for row in rows]

    def add_words(
        self, job_id: str, chunk_id: int, words: list[dict[str, Any]], offset: float
    ) -> None:
        previous = self.execute(
            "SELECT MAX(start_seconds) AS value FROM words WHERE job_id=?", (job_id,)
        ).fetchone()["value"]
        for word in words:
            start = offset + float(word["start"])
            end = offset + float(word["end"])
            if previous is not None and start < previous - 0.05:
                raise ValueError("transcript timestamps must be monotonic")
            self.execute(
                "INSERT INTO words(job_id,chunk_id,start_seconds,end_seconds,text,confidence) VALUES(?,?,?,?,?,?)",
                (job_id, chunk_id, start, end, str(word["text"]), word.get("confidence")),
            )
            previous = start

    def upsert_candidate(self, job_id: str, candidate: dict[str, Any]) -> str:
        start, end = float(candidate["start_seconds"]), float(candidate["end_seconds"])
        overlap = self.execute(
            "SELECT id,start_seconds,end_seconds,title,confidence FROM candidates WHERE job_id=? AND state != ? AND MIN(end_seconds,?) - MAX(start_seconds,?) > 0.5 * MIN(end_seconds-start_seconds,?-?) ORDER BY confidence DESC LIMIT 1",
            (job_id, CandidateState.DELETED, end, start, end, start),
        ).fetchone()
        if overlap:
            candidate_id = overlap["id"]
            if float(candidate["confidence"]) >= float(overlap["confidence"]):
                changed = (
                    abs(start - float(overlap["start_seconds"])) > 0.05
                    or abs(end - float(overlap["end_seconds"])) > 0.05
                    or candidate["title"] != overlap["title"]
                    or float(candidate["confidence"]) > float(overlap["confidence"]) + 0.001
                )
                self.execute(
                    "UPDATE candidates SET start_seconds=?,end_seconds=?,title=?,hook=?,payoff=?,rationale=?,confidence=?,standalone_value=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                    (
                        start,
                        end,
                        candidate["title"],
                        candidate.get("hook"),
                        candidate.get("payoff"),
                        candidate.get("rationale"),
                        candidate["confidence"],
                        candidate.get("standalone_value"),
                        candidate_id,
                    ),
                )
                if changed:
                    self.clip_activity(
                        job_id,
                        candidate_id,
                        "story_analyst",
                        "suggestion_refined",
                        "completed",
                        "Refined the clip boundaries and score after reviewing additional context.",
                        {"confidence": float(candidate["confidence"])},
                    )
            return candidate_id
        candidate_id = uuid.uuid4().hex
        self.execute(
            "INSERT INTO candidates(id,job_id,start_seconds,end_seconds,title,hook,payoff,rationale,confidence,standalone_value,state) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (
                candidate_id,
                job_id,
                start,
                end,
                candidate["title"],
                candidate.get("hook"),
                candidate.get("payoff"),
                candidate.get("rationale"),
                candidate["confidence"],
                candidate.get("standalone_value"),
                CandidateState.SUGGESTED,
            ),
        )
        self.event(job_id, "candidate.created", {"candidate_id": candidate_id})
        self.clip_activity(
            job_id,
            candidate_id,
            "story_analyst",
            "clip_suggested",
            "completed",
            "Identified and scored this moment as a standalone clip opportunity.",
            {"confidence": float(candidate["confidence"])},
        )
        return candidate_id

    def candidates(self, job_id: str) -> list[dict[str, Any]]:
        rows = self.execute(
            "SELECT * FROM candidates WHERE job_id=? AND state != ? ORDER BY confidence DESC, created_at",
            (job_id, CandidateState.DELETED),
        ).fetchall()
        return [dict(row) for row in rows]

    def candidate(self, candidate_id: str) -> dict[str, Any]:
        row = self.execute("SELECT * FROM candidates WHERE id=?", (candidate_id,)).fetchone()
        if not row:
            raise KeyError(candidate_id)
        return dict(row)

    def set_candidate_state(self, candidate_id: str, state: CandidateState) -> None:
        self.execute(
            "UPDATE candidates SET state=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (state, candidate_id),
        )

    def event(self, job_id: str | None, kind: str, payload: dict[str, Any]) -> None:
        self.execute(
            "INSERT INTO events(job_id,kind,payload) VALUES(?,?,?)",
            (job_id, kind, json.dumps(payload, separators=(",", ":"))),
        )

    def clip_activity(
        self,
        job_id: str,
        candidate_id: str,
        role: str,
        action: str,
        status: str,
        message: str,
        details: dict[str, Any] | None = None,
        render_id: str | None = None,
    ) -> None:
        self.event(
            job_id,
            "clip.activity",
            {
                "candidate_id": candidate_id,
                "render_id": render_id,
                "role": role,
                "action": action,
                "status": status,
                "message": message,
                "details": details or {},
            },
        )
