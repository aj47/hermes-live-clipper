from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
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
        "-i",
        str(source),
        "-ss",
        str(start),
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


def render_clip_from_chunks(
    chunks: list[dict], destination: Path, start: float, end: float
) -> dict:
    overlapping = [
        chunk
        for chunk in chunks
        if float(chunk["start_seconds"]) < end
        and float(chunk["start_seconds"]) + float(chunk["duration"] or 0) > start
    ]
    if not overlapping:
        raise RenderError("no captured chunks overlap the candidate")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp_root = Path(tempfile.mkdtemp(prefix=f".{destination.stem}-", dir=destination.parent))
    parts: list[Path] = []
    try:
        for index, chunk in enumerate(overlapping):
            chunk_start = float(chunk["start_seconds"])
            chunk_end = chunk_start + float(chunk["duration"] or 0)
            local_start = max(0.0, start - chunk_start)
            local_end = min(chunk_end, end) - chunk_start
            if local_end <= local_start:
                continue
            part = temp_root / f"part-{index:04d}.mp4"
            render_clip(Path(chunk["path"]), part, local_start, local_end)
            parts.append(part)
        if not parts:
            raise RenderError("candidate overlap produced no renderable chunk ranges")
        if len(parts) == 1:
            shutil.move(parts[0], destination)
        else:
            concat_file = temp_root / "concat.txt"
            concat_file.write_text(
                "".join(f"file '{part.as_posix()}'\n" for part in parts), encoding="utf-8"
            )
            result = subprocess.run(
                [
                    "ffmpeg",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-y",
                    "-f",
                    "concat",
                    "-safe",
                    "0",
                    "-i",
                    str(concat_file),
                    "-c",
                    "copy",
                    "-movflags",
                    "+faststart",
                    str(destination),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode:
                raise RenderError(result.stderr[-1500:])
        return probe(destination)
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


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
