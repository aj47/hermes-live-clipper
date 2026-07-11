from __future__ import annotations

import json
import subprocess
from pathlib import Path


class RenderError(RuntimeError):
    pass


def render_clip(source: Path, destination: Path, start: float, end: float) -> dict:
    destination.parent.mkdir(parents=True, exist_ok=True)
    duration = end - start
    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-ss",
        str(start),
        "-i",
        str(source),
        "-t",
        str(duration),
        "-c:v",
        "h264_videotoolbox",
        "-b:v",
        "8M",
        "-c:a",
        "aac",
        "-movflags",
        "+faststart",
        str(destination),
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode:
        raise RenderError(result.stderr[-1500:])
    return probe(destination)


def probe(path: Path) -> dict:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        raise RenderError(f"ffprobe verification failed: {result.stderr[-500:]}")
    data = json.loads(result.stdout)
    streams = data.get("streams", [])
    if not any(stream.get("codec_type") == "video" for stream in streams) or not any(
        stream.get("codec_type") == "audio" for stream in streams
    ):
        raise RenderError("render must contain video and audio")
    return data
