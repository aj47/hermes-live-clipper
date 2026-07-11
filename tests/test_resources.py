from hermes_live_clipper.config import Settings
from hermes_live_clipper.resources import can_render


def test_resource_admission(tmp_path):
    settings = Settings(
        root=tmp_path, max_cpu_percent=80, max_memory_percent=80, max_transcript_lag_seconds=120
    )
    healthy = {"cpu_percent": 40, "memory_percent": 40, "disk_free_bytes": 10 * 1024**3}
    hot = {**healthy, "cpu_percent": 90}
    assert can_render(settings, 10, healthy)
    assert not can_render(settings, 10, hot)
    assert not can_render(settings, 200, healthy)
