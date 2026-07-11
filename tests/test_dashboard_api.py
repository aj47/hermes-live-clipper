import importlib.util
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient


def load_router(monkeypatch, service):
    path = Path(__file__).parents[1] / "dashboard" / "plugin_api.py"
    spec = importlib.util.spec_from_file_location("test_plugin_api", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "get_service", lambda: service)
    app = FastAPI()
    app.include_router(module.router)
    return TestClient(app)


def test_create_and_read_job(monkeypatch, service):
    client = load_router(monkeypatch, service)
    response = client.post("/jobs", json={"url": "https://twitch.tv/example_streamer"})
    assert response.status_code == 200
    job_id = response.json()["id"]
    detail = client.get(f"/jobs/{job_id}")
    assert detail.status_code == 200
    assert detail.json()["job"]["provider"] == "twitch"
    assert detail.json()["renders"] == []


def test_job_detail_includes_generated_render_versions(monkeypatch, service):
    job = service.add_job("https://twitch.tv/example_streamer")
    candidate_id = service.db.upsert_candidate(
        job["id"],
        {
            "start_seconds": 20,
            "end_seconds": 50,
            "title": "Generated clip",
            "confidence": 0.9,
            "standalone_value": 0.8,
        },
    )
    service.db.execute(
        "INSERT INTO renders(id,candidate_id,version,path,duration,state) VALUES(?,?,?,?,?,?)",
        ("render-v1", candidate_id, 1, "/tmp/clip.mp4", 30, "ready"),
    )
    detail = load_router(monkeypatch, service).get(f"/jobs/{job['id']}").json()
    assert detail["renders"][0]["id"] == "render-v1"
    assert detail["renders"][0]["title"] == "Generated clip"
    assert detail["renders"][0]["candidate_state"] == "suggested"
    assert detail["renders"][0]["publisher_status"] is None
    roles = {entry["role"] for entry in detail["activity"]}
    assert roles == {"story_analyst", "video_editor"}
    assert {entry["action"] for entry in detail["activity"]} >= {
        "clip_suggested",
        "render_ready",
    }


def test_publisher_handoff_preserves_render_and_tracks_queue(monkeypatch, service):
    job = service.add_job("https://twitch.tv/example_streamer")
    candidate_id = service.db.upsert_candidate(
        job["id"],
        {
            "start_seconds": 20,
            "end_seconds": 50,
            "title": "Publisher-ready clip",
            "confidence": 0.9,
            "standalone_value": 0.8,
        },
    )
    source = service.settings.root / "jobs" / job["id"] / "renders" / "clip.mp4"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"fake-mp4")
    service.db.execute(
        "INSERT INTO renders(id,candidate_id,version,path,duration,state) VALUES(?,?,?,?,?,?)",
        ("render-publisher", candidate_id, 1, str(source), 30, "ready"),
    )
    client = load_router(monkeypatch, service)

    prepared = client.post("/renders/render-publisher/publisher-handoff")
    assert prepared.status_code == 200
    handoff = prepared.json()
    outbox = Path(handoff["outbox_path"])
    assert outbox.read_bytes() == b"fake-mp4"
    assert outbox.parent.joinpath("handoff.json").exists()
    progress_path = outbox.parent / "publisher-progress.json"
    assert progress_path.exists()
    progress = service._read_publisher_progress(progress_path)
    assert progress["phase"] == "asset_prepared"
    assert progress["percent"] == 5
    task = handoff["task"]
    assert task["assignee"] == "default"
    assert task["idempotency_key"] == "live-clipper-publisher:render-publisher"
    assert task["skills"] == ["youtube-upload", "computer-use"]
    assert str(outbox) in task["body"]
    assert "signed-in local TikTok and YouTube accounts" in task["body"]
    assert "Set status=published only when both" in task["body"]
    assert str(progress_path) in task["body"]
    assert "youtube_uploading" in task["body"]
    assert "existing Google Chrome app/window" in task["body"]
    assert "Do not use browser_navigate" in task["body"]
    assert "DO NOT block" in task["body"]

    completed = client.post(
        "/renders/render-publisher/publisher-handoff/complete",
        json={"task_id": "hermes-task-123"},
    )
    assert completed.status_code == 200
    detail = client.get(f"/jobs/{job['id']}").json()
    assert detail["renders"][0]["publisher_status"] == "queued"
    assert detail["renders"][0]["publisher_task_id"] == "hermes-task-123"
    assert detail["renders"][0]["publisher_progress"]["phase"] == "queued"
    assert detail["renders"][0]["publisher_progress"]["percent"] == 10
    publisher_actions = {
        entry["action"] for entry in detail["activity"] if entry["role"] == "publisher_growth"
    }
    assert publisher_actions >= {"handoff_prepared", "publisher_task_queued"}

    outbox.parent.joinpath("publisher-result.json").write_text(
        '{"status":"published","summary":"Unverified","platforms":[]}'
    )
    detail = client.get(f"/jobs/{job['id']}").json()
    assert detail["renders"][0]["publisher_result"]["status"] == "invalid"

    outbox.parent.joinpath("publisher-result.json").write_text(
        '{"status":"published","summary":"Both verified","platforms":['
        '{"platform":"youtube","status":"published","receipt":"yt-123","url":"https://youtube.com/watch?v=yt-123"},'
        '{"platform":"tiktok","status":"published","receipt":"tt-123","url":"https://tiktok.com/@techfren/video/tt-123"}]}'
    )
    detail = client.get(f"/jobs/{job['id']}").json()
    assert detail["renders"][0]["publisher_result"]["status"] == "published"
    assert detail["renders"][0]["publisher_result"]["summary"] == "Both verified"


def test_publisher_retry_archives_blocked_attempt_and_resets_progress(monkeypatch, service):
    job = service.add_job("https://twitch.tv/techfren")
    candidate_id = service.db.upsert_candidate(
        job["id"],
        {
            "start_seconds": 20,
            "end_seconds": 50,
            "title": "Retryable clip",
            "confidence": 0.9,
            "standalone_value": 0.8,
        },
    )
    source = service.settings.root / "jobs" / job["id"] / "renders" / "clip.mp4"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"fake-mp4")
    service.db.execute(
        "INSERT INTO renders(id,candidate_id,version,path,duration,state) VALUES(?,?,?,?,?,?)",
        ("render-retry", candidate_id, 1, str(source), 30, "ready"),
    )
    client = load_router(monkeypatch, service)
    first = client.post("/renders/render-retry/publisher-handoff").json()
    client.post(
        "/renders/render-retry/publisher-handoff/complete",
        json={"task_id": "hermes-task-retry"},
    )
    outbox = Path(first["outbox_path"]).parent
    (outbox / "publisher-progress.json").write_text(
        '{"status":"blocked","phase":"verifying","percent":95,"platforms":[]}'
    )
    (outbox / "publisher-result.json").write_text(
        '{"status":"blocked","summary":"Needs signed-in Chrome","platforms":[]}'
    )

    retried = client.post("/renders/render-retry/publisher-handoff")
    assert retried.status_code == 200
    assert list(outbox.glob("publisher-result-attempt-*.json"))
    assert list(outbox.glob("publisher-progress-attempt-*.json"))
    assert not (outbox / "publisher-result.json").exists()
    prepared = service._read_publisher_progress(outbox / "publisher-progress.json")
    assert prepared["status"] == "prepared"
    assert prepared["percent"] == 5
    assert all(item["status"] == "waiting" for item in prepared["platforms"])

    completed = client.post(
        "/renders/render-retry/publisher-handoff/complete",
        json={"task_id": "hermes-task-retry"},
    )
    assert completed.status_code == 200
    queued = service._read_publisher_progress(outbox / "publisher-progress.json")
    assert queued["status"] == "queued"
    assert queued["percent"] == 10
    assert all(item["status"] == "waiting" for item in queued["platforms"])


def test_cleanup_preview_and_execute_routes(monkeypatch, service):
    job = service.add_job("https://twitch.tv/example_streamer")
    service.stop_job(job["id"])
    client = load_router(monkeypatch, service)
    selection = {"job_ids": [job["id"]], "candidate_ids": [], "render_ids": []}

    preview = client.post("/cleanup/preview", json=selection)
    assert preview.status_code == 200
    assert preview.json()["counts"]["jobs"] == 1
    assert preview.json()["blocked"] == []

    execute = client.post(
        "/cleanup/execute",
        json={**selection, "expected_bytes": preview.json()["reclaimable_bytes"]},
    )
    assert execute.status_code == 200
    assert service.db.execute("SELECT COUNT(*) value FROM jobs").fetchone()["value"] == 0


def test_cleanup_execute_blocks_active_stream(monkeypatch, service):
    job = service.add_job("https://twitch.tv/example_streamer")
    client = load_router(monkeypatch, service)
    selection = {"job_ids": [job["id"]], "candidate_ids": [], "render_ids": []}
    preview = client.post("/cleanup/preview", json=selection).json()
    response = client.post(
        "/cleanup/execute",
        json={**selection, "expected_bytes": preview["reclaimable_bytes"]},
    )
    assert response.status_code == 409
    assert "Stop this stream" in response.json()["detail"]
