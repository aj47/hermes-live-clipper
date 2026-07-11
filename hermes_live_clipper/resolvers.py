from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from urllib.parse import urlparse

from .models import StreamIdentity


class ResolveError(RuntimeError):
    pass


@dataclass(frozen=True)
class ResolvedStream:
    identity: StreamIdentity
    title: str
    media_url: str
    is_live: bool
    live_status: str
    duration: float | None


def normalize_url(url: str) -> StreamIdentity:
    raw = url.strip()
    parsed = urlparse(raw if "://" in raw else f"https://{raw}")
    host = parsed.netloc.lower().removeprefix("www.")
    if host in {"youtube.com", "m.youtube.com", "youtu.be"}:
        if host == "youtu.be":
            video_id = parsed.path.strip("/").split("/")[0]
        else:
            match = re.search(r"(?:v=|/live/|/shorts/)([A-Za-z0-9_-]{6,})", raw)
            video_id = match.group(1) if match else ""
        if not video_id:
            raise ResolveError("A specific YouTube video or livestream URL is required")
        return StreamIdentity("youtube", f"https://www.youtube.com/watch?v={video_id}", video_id)
    if host == "twitch.tv" or host.endswith(".twitch.tv"):
        channel = parsed.path.strip("/").split("/")[0].lower()
        if not channel or channel in {"videos", "directory"}:
            raise ResolveError("A public Twitch channel URL is required")
        return StreamIdentity("twitch", f"https://www.twitch.tv/{channel}", channel)
    raise ResolveError("Only public YouTube and Twitch links are supported")


class YtDlpResolver:
    def resolve(self, url: str, *, from_start: bool = False) -> ResolvedStream:
        identity = normalize_url(url)
        command = [
            "yt-dlp",
            "--dump-single-json",
            "--no-warnings",
            "--format",
            "best[ext=mp4]/best",
        ]
        if from_start:
            command.append("--live-from-start")
        command.append(identity.canonical_url)
        result = subprocess.run(command, capture_output=True, text=True, timeout=45, check=False)
        if result.returncode:
            message = (
                result.stderr.strip().splitlines()[-1]
                if result.stderr.strip()
                else "stream unavailable"
            )
            raise ResolveError(_safe_error(message))
        data = json.loads(result.stdout)
        media_url = data.get("url")
        if not media_url:
            raise ResolveError("The stream is unavailable, private, DRM-protected, or unsupported")
        return ResolvedStream(
            identity=identity,
            title=data.get("title") or identity.external_id,
            media_url=media_url,
            is_live=bool(data.get("is_live")),
            live_status=data.get("live_status")
            or ("is_live" if data.get("is_live") else "not_live"),
            duration=data.get("duration"),
        )


def _safe_error(message: str) -> str:
    message = re.sub(r"https?://\S+", "[redacted-url]", message)
    return message[-500:]
