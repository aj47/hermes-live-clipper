from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class JobState(StrEnum):
    QUEUED = "queued"
    WAITING = "waiting_for_live"
    CAPTURING = "capturing"
    RECONNECTING = "reconnecting"
    FINALIZING = "finalizing"
    COMPLETED = "completed"
    NEEDS_ATTENTION = "needs_attention"
    FAILED = "failed"
    STOPPED = "stopped"


class CandidateState(StrEnum):
    SUGGESTED = "suggested"
    RENDER_QUEUED = "render_queued"
    RENDERING = "rendering"
    DRAFT_READY = "draft_ready"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    FAILED = "failed"
    DELETED = "deleted"


@dataclass(frozen=True)
class StreamIdentity:
    provider: str
    canonical_url: str
    external_id: str
