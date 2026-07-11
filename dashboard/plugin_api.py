from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from hermes_live_clipper.cleanup import CleanupError
from hermes_live_clipper.service import get_service

router = APIRouter()


class JobRequest(BaseModel):
    url: str = Field(min_length=8, max_length=2048)
    start_mode: str = "live_edge"


class TrimRequest(BaseModel):
    start_word_id: int
    end_word_id: int


class ActionRequest(BaseModel):
    action: str


class HandoffCompleteRequest(BaseModel):
    task_id: str | None = None


class CleanupRequest(BaseModel):
    job_ids: list[str] = Field(default_factory=list, max_length=500)
    candidate_ids: list[str] = Field(default_factory=list, max_length=500)
    render_ids: list[str] = Field(default_factory=list, max_length=500)
    force_publisher_assets: bool = False


class CleanupExecuteRequest(CleanupRequest):
    expected_bytes: int = Field(ge=0)


def _not_found(exc: KeyError) -> HTTPException:
    return HTTPException(404, f"Unknown resource: {exc.args[0]}")


@router.get("/status")
def status():
    return get_service().status()


@router.post("/jobs")
def create_job(request: JobRequest):
    try:
        return get_service().add_job(request.url, request.start_mode)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/jobs/{job_id}")
def job(job_id: str):
    try:
        return get_service().detail(job_id)
    except KeyError as exc:
        raise _not_found(exc) from exc


@router.post("/jobs/{job_id}/stop")
def stop(job_id: str):
    try:
        return get_service().stop_job(job_id)
    except KeyError as exc:
        raise _not_found(exc) from exc


@router.delete("/jobs/{job_id}", status_code=204)
def delete(job_id: str):
    try:
        get_service().delete_job(job_id)
    except KeyError as exc:
        raise _not_found(exc) from exc
    except CleanupError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.put("/candidates/{candidate_id}/trim")
def trim(candidate_id: str, request: TrimRequest):
    try:
        return get_service().update_candidate(
            candidate_id, request.start_word_id, request.end_word_id
        )
    except KeyError as exc:
        raise _not_found(exc) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/candidates/{candidate_id}/action")
def candidate_action(candidate_id: str, request: ActionRequest):
    try:
        return get_service().candidate_action(candidate_id, request.action)
    except KeyError as exc:
        raise _not_found(exc) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/renders/{render_id}/media")
def render_media(render_id: str):
    service = get_service()
    row = service.db.execute(
        "SELECT path FROM renders WHERE id=? AND state='ready'", (render_id,)
    ).fetchone()
    if not row:
        raise HTTPException(404, "Render not found")
    path = Path(row["path"]).resolve()
    root = service.settings.root.resolve()
    if root not in path.parents or not path.exists():
        raise HTTPException(404, "Render file not found")
    return FileResponse(path, media_type="video/mp4", filename=path.name)


@router.post("/renders/{render_id}/publisher-handoff")
def prepare_publisher_handoff(render_id: str):
    try:
        return get_service().prepare_publisher_handoff(render_id)
    except KeyError as exc:
        raise _not_found(exc) from exc
    except FileNotFoundError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/renders/{render_id}/publisher-handoff/complete")
def complete_publisher_handoff(render_id: str, request: HandoffCompleteRequest):
    try:
        return get_service().complete_publisher_handoff(render_id, request.task_id)
    except KeyError as exc:
        raise _not_found(exc) from exc


@router.post("/cleanup/preview")
def cleanup_preview(request: CleanupRequest):
    try:
        return get_service().cleanup_preview(
            request.job_ids,
            request.candidate_ids,
            request.render_ids,
            request.force_publisher_assets,
        )
    except CleanupError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/cleanup/execute")
def cleanup_execute(request: CleanupExecuteRequest):
    try:
        return get_service().cleanup_execute(
            request.job_ids,
            request.candidate_ids,
            request.render_ids,
            request.expected_bytes,
            request.force_publisher_assets,
        )
    except CleanupError as exc:
        raise HTTPException(409, str(exc)) from exc
