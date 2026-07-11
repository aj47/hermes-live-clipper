from hermes_live_clipper.models import CandidateState


def test_one_job_active_then_next_is_queued(service):
    first = service.add_job("https://youtu.be/abcDEF_123")
    second = service.add_job("https://twitch.tv/example_streamer")
    assert first["state"] == "waiting_for_live"
    assert second["state"] == "queued"


def test_candidate_overlap_updates_instead_of_duplicates(service):
    job = service.add_job("https://youtu.be/abcDEF_123")
    first = {
        "start_seconds": 20,
        "end_seconds": 60,
        "title": "First",
        "confidence": 0.7,
        "standalone_value": 0.8,
    }
    improved = {
        "start_seconds": 22,
        "end_seconds": 62,
        "title": "Improved",
        "confidence": 0.9,
        "standalone_value": 0.9,
    }
    first_id = service.db.upsert_candidate(job["id"], first)
    second_id = service.db.upsert_candidate(job["id"], improved)
    assert first_id == second_id
    assert len(service.db.candidates(job["id"])) == 1
    assert service.db.candidate(first_id)["title"] == "Improved"


def test_candidate_action(service):
    job = service.add_job("https://youtu.be/abcDEF_123")
    candidate_id = service.db.upsert_candidate(
        job["id"],
        {
            "start_seconds": 20,
            "end_seconds": 50,
            "title": "Clip",
            "confidence": 0.8,
            "standalone_value": 0.8,
        },
    )
    updated = service.candidate_action(candidate_id, "render")
    assert updated["state"] == CandidateState.RENDER_QUEUED


def test_words_must_be_monotonic(service):
    job = service.add_job("https://youtu.be/abcDEF_123")
    cursor = service.db.execute(
        "INSERT INTO chunks(job_id,sequence,path,start_seconds,duration) VALUES(?,?,?,?,?)",
        (job["id"], 0, "chunk.ts", 0, 10),
    )
    service.db.add_words(job["id"], cursor.lastrowid, [{"text": "hello", "start": 1, "end": 2}], 0)
    try:
        service.db.add_words(
            job["id"], cursor.lastrowid, [{"text": "backwards", "start": 0, "end": 1}], 0
        )
    except ValueError as exc:
        assert "monotonic" in str(exc)
    else:
        raise AssertionError("expected monotonicity error")
