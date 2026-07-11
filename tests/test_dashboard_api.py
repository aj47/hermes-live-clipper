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
