from hermes_live_clipper.transcription import normalize_words


def test_normalize_segment_words():
    result = normalize_words(
        {
            "segments": [
                {
                    "words": [
                        {"word": " world", "start": 1.5, "end": 2, "probability": 0.9},
                        {"word": "Hello", "start": 0, "end": 0.5},
                    ]
                }
            ]
        }
    )
    assert [word["text"] for word in result] == ["Hello", "world"]
    assert result[1]["confidence"] == 0.9


def test_normalize_parakeet_sentence_words():
    result = normalize_words(
        {
            "text": "Hello world",
            "sentences": [
                {
                    "text": "Hello world",
                    "start": 0,
                    "end": 1,
                    "words": [
                        {"word": "Hello", "start": 0, "end": 0.4},
                        {"word": " world", "start": 0.5, "end": 1},
                    ],
                }
            ],
        }
    )
    assert [word["text"] for word in result] == ["Hello", "world"]
