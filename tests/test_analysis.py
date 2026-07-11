from hermes_live_clipper.analysis import validate_candidates


def test_analysis_validation_rejects_out_of_range_and_bad_duration():
    items = [
        {
            "start_seconds": 10,
            "end_seconds": 45,
            "title": "Good",
            "hook": "h",
            "payoff": "p",
            "rationale": "r",
            "confidence": 0.9,
            "standalone_value": 0.8,
        },
        {
            "start_seconds": -1,
            "end_seconds": 30,
            "title": "Outside",
            "confidence": 0.9,
            "standalone_value": 0.8,
        },
        {
            "start_seconds": 50,
            "end_seconds": 54,
            "title": "Short",
            "confidence": 0.9,
            "standalone_value": 0.8,
        },
    ]
    assert [item["title"] for item in validate_candidates(items, 0, 100)] == ["Good"]
