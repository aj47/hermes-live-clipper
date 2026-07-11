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
