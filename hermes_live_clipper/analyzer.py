from __future__ import annotations

import threading
import uuid
from collections.abc import Callable
from typing import Any

from .analysis import analyze_window
from .service import LiveClipperService


class PluginAnalyzer:
    """Runs transcript analysis inside the Hermes process where ctx.llm is available."""

    def __init__(self, service: LiveClipperService, complete_structured: Callable[..., Any]):
        self.service = service
        self.complete_structured = complete_structured
        self.instance_id = uuid.uuid4().hex
        self.stop_event = threading.Event()

    def run_forever(self) -> None:
        while not self.stop_event.is_set():
            self._heartbeat()
            try:
                self.run_once()
            except Exception as exc:
                self.service.db.event(None, "analyzer.failed", {"error": str(exc)[-1000:]})
            self.stop_event.wait(10)

    def run_once(self) -> int:
        analyzed = 0
        settings = self.service.settings
        for job in self.service.db.jobs():
            latest_row = self.service.db.execute(
                "SELECT MAX(end_seconds) value FROM words WHERE job_id=?", (job["id"],)
            ).fetchone()
            latest = latest_row["value"] or 0
            if latest < settings.analysis_start_seconds:
                continue
            previous_row = self.service.db.execute(
                "SELECT MAX(end_seconds) value FROM analysis_windows "
                "WHERE job_id=? AND status='complete'",
                (job["id"],),
            ).fetchone()
            previous = previous_row["value"]
            if (
                previous is not None
                and latest - previous
                < settings.analysis_window_seconds - settings.analysis_overlap_seconds
            ):
                continue
            start = max(0, latest - settings.analysis_window_seconds)
            cursor = self.service.db.execute(
                "INSERT OR IGNORE INTO analysis_windows(job_id,start_seconds,end_seconds,status) "
                "VALUES(?,?,?,'running')",
                (job["id"], start, latest),
            )
            if cursor.rowcount != 1:
                continue
            try:
                words = self.service.db.words(job["id"], start, latest)
                for candidate in analyze_window(self.complete_structured, words):
                    self.service.db.upsert_candidate(job["id"], candidate)
                self.service.db.execute(
                    "UPDATE analysis_windows SET status='complete' "
                    "WHERE job_id=? AND start_seconds=? AND end_seconds=?",
                    (job["id"], start, latest),
                )
                analyzed += 1
            except Exception:
                self.service.db.execute(
                    "DELETE FROM analysis_windows WHERE job_id=? AND start_seconds=? AND end_seconds=?",
                    (job["id"], start, latest),
                )
                raise
        return analyzed

    def _heartbeat(self) -> None:
        self.service.db.execute(
            "INSERT INTO runtime(component,instance_id,heartbeat_at) VALUES('analyzer',?,CURRENT_TIMESTAMP) "
            "ON CONFLICT(component) DO UPDATE SET instance_id=excluded.instance_id,"
            "heartbeat_at=CURRENT_TIMESTAMP",
            (self.instance_id,),
        )


_ANALYZER_THREAD: threading.Thread | None = None


def start_plugin_analyzer(
    service: LiveClipperService, complete_structured: Callable[..., Any]
) -> None:
    global _ANALYZER_THREAD
    if _ANALYZER_THREAD and _ANALYZER_THREAD.is_alive():
        return
    analyzer = PluginAnalyzer(service, complete_structured)
    _ANALYZER_THREAD = threading.Thread(
        target=analyzer.run_forever, name="hermes-live-clipper-analyzer", daemon=True
    )
    _ANALYZER_THREAD.start()
