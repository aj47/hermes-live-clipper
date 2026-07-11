from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any


class TranscriptionError(RuntimeError):
    pass


def transcribe_chunk(path: Path, output_dir: Path) -> list[dict[str, Any]]:
    output_dir.mkdir(parents=True, exist_ok=True)
    command = ["transcribe-anything", str(path), "--output_dir", str(output_dir)]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode:
        raise TranscriptionError((result.stderr or result.stdout)[-1000:])
    candidates = [output_dir / "out.json", *output_dir.glob("**/out.json")]
    transcript_path = next((candidate for candidate in candidates if candidate.exists()), None)
    if not transcript_path:
        raise TranscriptionError("transcribe-anything completed without producing out.json")
    return normalize_words(json.loads(transcript_path.read_text()))


def normalize_words(payload: Any) -> list[dict[str, Any]]:
    raw_words: list[dict[str, Any]] = []
    if isinstance(payload, dict):
        if isinstance(payload.get("words"), list):
            raw_words = payload["words"]
        elif isinstance(payload.get("segments"), list):
            for segment in payload["segments"]:
                raw_words.extend(segment.get("words") or [])
    elif isinstance(payload, list):
        raw_words = payload
    normalized = []
    for word in raw_words:
        text = str(word.get("word", word.get("text", ""))).strip()
        start = word.get("start", word.get("start_time"))
        end = word.get("end", word.get("end_time"))
        if text and start is not None and end is not None:
            normalized.append(
                {
                    "text": text,
                    "start": float(start),
                    "end": float(end),
                    "confidence": word.get("confidence", word.get("probability")),
                }
            )
    normalized.sort(key=lambda item: (item["start"], item["end"]))
    return normalized
