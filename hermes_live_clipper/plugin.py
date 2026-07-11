from __future__ import annotations

import json

from .analyzer import start_plugin_analyzer
from .service import get_service


def register(ctx):
    service = get_service()
    start_plugin_analyzer(service, ctx.llm.complete_structured)

    def add(params, **_kwargs):
        return json.dumps(service.add_job(params["url"], params.get("start_mode", "live_edge")))

    def status(params, **_kwargs):
        job_id = params.get("job_id")
        return json.dumps(service.detail(job_id) if job_id else service.status(), default=str)

    def stop(params, **_kwargs):
        return json.dumps(service.stop_job(params["job_id"]))

    def action(params, **_kwargs):
        return json.dumps(service.candidate_action(params["candidate_id"], params["action"]))

    ctx.register_tool(
        name="live_clipper_add",
        toolset="live_clipper",
        schema={
            "name": "live_clipper_add",
            "description": "Monitor a public YouTube or Twitch livestream and create clip drafts.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string"},
                    "start_mode": {"type": "string", "enum": ["live_edge", "from_start"]},
                },
                "required": ["url"],
            },
        },
        handler=add,
        description="Start monitoring a public livestream.",
    )
    ctx.register_tool(
        name="live_clipper_status",
        toolset="live_clipper",
        schema={
            "name": "live_clipper_status",
            "description": "Read live clipper jobs, transcripts, and candidates.",
            "parameters": {"type": "object", "properties": {"job_id": {"type": "string"}}},
        },
        handler=status,
        description="Read live clipper status.",
    )
    ctx.register_tool(
        name="live_clipper_stop",
        toolset="live_clipper",
        schema={
            "name": "live_clipper_stop",
            "description": "Stop a monitored livestream job.",
            "parameters": {
                "type": "object",
                "properties": {"job_id": {"type": "string"}},
                "required": ["job_id"],
            },
        },
        handler=stop,
        description="Stop a live clipper job.",
    )
    ctx.register_tool(
        name="live_clipper_candidate_action",
        toolset="live_clipper",
        schema={
            "name": "live_clipper_candidate_action",
            "description": "Accept, reject, render, or delete a clip candidate.",
            "parameters": {
                "type": "object",
                "properties": {
                    "candidate_id": {"type": "string"},
                    "action": {"type": "string", "enum": ["accept", "reject", "render", "delete"]},
                },
                "required": ["candidate_id", "action"],
            },
        },
        handler=action,
        description="Update a clip candidate.",
    )

    def clipper_command(raw: str) -> str:
        url = raw.strip()
        if not url:
            return "Usage: /clipper <public YouTube or Twitch URL>"
        job = service.add_job(url)
        return f"Live clipper job `{job['id']}` queued for {job['canonical_url']}."

    ctx.register_command(
        name="clipper",
        handler=clipper_command,
        description="Monitor a public livestream",
        args_hint="<url>",
    )
