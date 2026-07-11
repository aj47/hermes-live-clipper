from __future__ import annotations

import psutil

from .config import Settings


def metrics(root_path: str) -> dict[str, float | bool]:
    cpu = psutil.cpu_percent(interval=0.1)
    memory = psutil.virtual_memory().percent
    disk = psutil.disk_usage(root_path)
    return {
        "cpu_percent": cpu,
        "memory_percent": memory,
        "disk_percent": disk.percent,
        "disk_free_bytes": disk.free,
    }


def can_render(settings: Settings, transcript_lag: float, current: dict | None = None) -> bool:
    current = current or metrics(str(settings.root))
    return bool(
        current["cpu_percent"] < settings.max_cpu_percent
        and current["memory_percent"] < settings.max_memory_percent
        and transcript_lag < settings.max_transcript_lag_seconds
        and current["disk_free_bytes"] > 2 * 1024**3
    )
