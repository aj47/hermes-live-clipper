import json

from hermes_live_clipper.maintenance import backfill_transcripts, retry_failed_renders


def test_backfill_existing_parakeet_transcript(service):
    job = service.add_job("https://youtu.be/abcDEF_123")
    cursor = service.db.execute(
        "INSERT INTO chunks(job_id,sequence,path,start_seconds,duration,state) "
        "VALUES(?,?,?,?,?,'transcribed')",
        (job["id"], 0, "chunk.ts", 12, 10),
    )
    output = service.settings.root / "jobs" / job["id"] / "transcripts" / "00000000"
    output.mkdir(parents=True)
    (output / "out.json").write_text(
        json.dumps(
            {
                "text": "Hello world",
                "sentences": [
                    {
                        "words": [
                            {"word": "Hello", "start": 0, "end": 0.4},
                            {"word": "world", "start": 0.5, "end": 1},
                        ]
                    }
                ],
            }
        )
    )
    result = backfill_transcripts(service, job["id"])
    assert result["words"] == 2
    words = service.db.words(job["id"])
    assert words[0]["chunk_id"] == cursor.lastrowid
    assert words[0]["start_seconds"] == 12
    assert words[1]["end_seconds"] == 13
    assert backfill_transcripts(service, job["id"])["skipped"] == 1


def test_service_initialization_does_not_change_active_job_state(service):
    job = service.add_job("https://youtu.be/abcDEF_123")
    service.db.set_job_state(job["id"], "capturing")
    from hermes_live_clipper.service import LiveClipperService

    second_process = LiveClipperService(service.settings)
    assert second_process.db.job(job["id"])["state"] == "capturing"


def test_retry_failed_renders_queues_candidates(service):
    job = service.add_job("https://youtu.be/abcDEF_123")
    candidate_id = service.db.upsert_candidate(
        job["id"],
        {
            "start_seconds": 20,
            "end_seconds": 50,
            "title": "Clip",
            "confidence": 0.9,
            "standalone_value": 0.8,
        },
    )
    service.db.set_candidate_state(candidate_id, "failed")
    assert retry_failed_renders(service, job["id"])["queued"] == 1
    assert service.db.candidate(candidate_id)["state"] == "render_queued"
