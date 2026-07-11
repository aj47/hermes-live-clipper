from __future__ import annotations

from pathlib import Path

from .service import LiveClipperService
from .transcription import load_words


def backfill_transcripts(service: LiveClipperService, job_id: str | None = None) -> dict:
    jobs = [service.db.job(job_id)] if job_id else service.db.jobs()
    result = {"jobs": 0, "chunks": 0, "words": 0, "skipped": 0, "missing": 0}
    for job in jobs:
        result["jobs"] += 1
        chunks = service.db.execute(
            "SELECT * FROM chunks WHERE job_id=? ORDER BY sequence", (job["id"],)
        ).fetchall()
        for chunk in chunks:
            existing = service.db.execute(
                "SELECT COUNT(*) count FROM words WHERE chunk_id=?", (chunk["id"],)
            ).fetchone()["count"]
            if existing:
                result["skipped"] += 1
                continue
            transcript = (
                service.settings.root
                / "jobs"
                / job["id"]
                / "transcripts"
                / f"{chunk['sequence']:08d}"
                / "out.json"
            )
            if not transcript.exists():
                result["missing"] += 1
                continue
            words = load_words(Path(transcript))
            if not words:
                result["skipped"] += 1
                continue
            service.db.add_words(job["id"], chunk["id"], words, chunk["start_seconds"])
            result["chunks"] += 1
            result["words"] += len(words)
    return result
