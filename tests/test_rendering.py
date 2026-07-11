from types import SimpleNamespace

from hermes_live_clipper import rendering


def test_chunk_render_converts_global_times_to_local_ranges(tmp_path, monkeypatch):
    calls = []

    def fake_render(source, destination, start, end):
        calls.append((source.name, start, end))
        destination.write_bytes(b"part")
        return {"format": {"duration": str(end - start)}}

    def fake_run(command, **_kwargs):
        destination = command[-1]
        from pathlib import Path

        Path(destination).write_bytes(b"joined")
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr(rendering, "render_clip", fake_render)
    monkeypatch.setattr(rendering.subprocess, "run", fake_run)
    monkeypatch.setattr(
        rendering,
        "probe",
        lambda _path: {
            "streams": [{"codec_type": "video"}, {"codec_type": "audio"}],
            "format": {"duration": "20"},
        },
    )
    chunks = [
        {"path": str(tmp_path / "0001.ts"), "start_seconds": 40, "duration": 10},
        {"path": str(tmp_path / "0002.ts"), "start_seconds": 50, "duration": 10},
    ]
    destination = tmp_path / "clip.mp4"
    rendering.render_clip_from_chunks(chunks, destination, 45, 55)
    assert calls == [("0001.ts", 5, 10), ("0002.ts", 0, 5)]
    assert destination.read_bytes() == b"joined"
