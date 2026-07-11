from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    root: Path
    chunk_seconds: int = 45
    analysis_start_seconds: int = 300
    analysis_window_seconds: int = 420
    analysis_overlap_seconds: int = 90
    max_cpu_percent: float = 82.0
    max_memory_percent: float = 85.0
    max_transcript_lag_seconds: int = 120
    min_candidate_confidence: float = 0.72
    publisher_profile: str = "live-clipper-publisher"

    @classmethod
    def load(cls) -> Settings:
        root = Path(
            os.environ.get("HERMES_LIVE_CLIPPER_HOME", "~/.hermes/live-clipper-v2")
        ).expanduser()
        return cls(
            root=root,
            chunk_seconds=int(os.environ.get("HLC_CHUNK_SECONDS", "45")),
            analysis_start_seconds=int(os.environ.get("HLC_ANALYSIS_START_SECONDS", "300")),
            max_cpu_percent=float(os.environ.get("HLC_MAX_CPU", "82")),
            max_memory_percent=float(os.environ.get("HLC_MAX_MEMORY", "85")),
            publisher_profile=os.environ.get(
                "HLC_PUBLISHER_PROFILE", "live-clipper-publisher"
            ),
        )

    def ensure(self) -> None:
        for path in (
            self.root,
            self.root / "jobs",
            self.root / "logs",
            self.root / "run",
            self.root / "publisher_outbox",
        ):
            path.mkdir(parents=True, exist_ok=True)
