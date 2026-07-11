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
    task = handoff["task"]
    assert task["assignee"] == "default"
    assert task["idempotency_key"] == "live-clipper-publisher:render-publisher"
    assert task["skills"] == ["youtube-upload"]
    assert str(outbox) in task["body"]
    assert "signed-in local TikTok and YouTube accounts" in task["body"]
    assert "Set status=published only when both" in task["body"]

    completed = client.post(
        "/renders/render-publisher/publisher-handoff/complete",
        json={"task_id": "hermes-task-123"},
    )
    assert completed.status_code == 200
    detail = client.get(f"/jobs/{job['id']}").json()
    assert detail["renders"][0]["publisher_status"] == "queued"
    assert detail["renders"][0]["publisher_task_id"] == "hermes-task-123"
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
        '{"platform":"youtube","status":"published","receipt":"yt-123"},'
        '{"platform":"tiktok","status":"published","receipt":"tt-123"}]}'
    )
    detail = client.get(f"/jobs/{job['id']}").json()
    assert detail["renders"][0]["publisher_result"]["status"] == "published"
    assert detail["renders"][0]["publisher_result"]["summary"] == "Both verified"
