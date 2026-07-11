from pathlib import Path

import pytest

from hermes_live_clipper.cleanup import CleanupError


def add_ready_render(service, job_id: str, candidate_id: str, render_id: str) -> Path:
    path = service.settings.root / "jobs" / job_id / "renders" / f"{render_id}.mp4"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"render-bytes")
    service.db.execute(
        "INSERT INTO renders(id,candidate_id,version,path,duration,state) VALUES(?,?,?,?,?,?)",
        (render_id, candidate_id, 1, str(path), 30, "ready"),
    )
    return path


def add_candidate(service, job_id: str, title: str = "Cleanup candidate") -> str:
    return service.db.upsert_candidate(
        job_id,
        {
            "start_seconds": 20,
            "end_seconds": 50,
            "title": title,
            "confidence": 0.9,
            "standalone_value": 0.8,
        },
    )


def test_reject_is_non_destructive_and_candidate_cleanup_reclaims_render(service):
    job = service.add_job("https://twitch.tv/example_streamer")
    candidate_id = add_candidate(service, job["id"])
    render_path = add_ready_render(service, job["id"], candidate_id, "render-cleanup")

    service.candidate_action(candidate_id, "reject")
    assert render_path.exists()
    assert service.db.execute("SELECT COUNT(*) value FROM renders").fetchone()["value"] == 1

    preview = service.cleanup_preview([], [candidate_id], [])
    assert preview["counts"]["candidates"] == 1
    assert preview["counts"]["renders"] == 1
    assert preview["reclaimable_bytes"] == len(b"render-bytes")
    assert preview["blocked"] == []

    service.cleanup_execute([], [candidate_id], [], preview["reclaimable_bytes"])
    assert not render_path.exists()
    assert service.db.execute("SELECT COUNT(*) value FROM candidates").fetchone()["value"] == 0
    assert service.db.execute("SELECT COUNT(*) value FROM renders").fetchone()["value"] == 0


def test_cleanup_blocks_active_job(service):
    job = service.add_job("https://twitch.tv/example_streamer")
    preview = service.cleanup_preview([job["id"]], [], [])
    assert preview["blocked"][0]["kind"] == "active_job"
    with pytest.raises(CleanupError, match="Stop this stream"):
        service.cleanup_execute([job["id"]], [], [], preview["reclaimable_bytes"])


def test_cleanup_requires_explicit_force_for_publisher_assets(service):
    job = service.add_job("https://twitch.tv/example_streamer")
    candidate_id = add_candidate(service, job["id"])
    render_path = add_ready_render(service, job["id"], candidate_id, "render-publisher")
    outbox = service.settings.root / "publisher_outbox" / "render-publisher"
    outbox.mkdir(parents=True)
    outbox.joinpath("clip.mp4").write_bytes(b"preserved")
    service.db.execute(
        "INSERT INTO publisher_handoffs(render_id,outbox_path,status,task_id) VALUES(?,?,?,?)",
        ("render-publisher", str(outbox / "clip.mp4"), "queued", "task-1"),
    )

    protected = service.cleanup_preview([], [], ["render-publisher"])
    assert protected["blocked"][0]["kind"] == "publisher_asset"

    forced = service.cleanup_preview([], [], ["render-publisher"], True)
    assert forced["blocked"] == []
    assert forced["reclaimable_bytes"] == len(b"render-bytes") + len(b"preserved")
    service.cleanup_execute([], [], ["render-publisher"], forced["reclaimable_bytes"], True)
    assert not render_path.exists()
    assert not outbox.exists()
    assert (
        service.db.execute("SELECT COUNT(*) value FROM publisher_handoffs").fetchone()["value"] == 0
    )


def test_cleanup_job_removes_job_media_and_outbox(service):
    job = service.add_job("https://twitch.tv/example_streamer")
    service.stop_job(job["id"])
    candidate_id = add_candidate(service, job["id"])
    render_path = add_ready_render(service, job["id"], candidate_id, "render-job")
    source = service.settings.root / "jobs" / job["id"] / "source.ts"
    source.write_bytes(b"source")

    preview = service.cleanup_preview([job["id"]], [], [])
    assert preview["counts"]["jobs"] == 1
    assert preview["reclaimable_bytes"] == len(b"render-bytes") + len(b"source")
    service.cleanup_execute([job["id"]], [], [], preview["reclaimable_bytes"])
    assert not render_path.parent.parent.exists()
    assert service.db.execute("SELECT COUNT(*) value FROM jobs").fetchone()["value"] == 0
