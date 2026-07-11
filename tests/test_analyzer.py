from types import SimpleNamespace

from hermes_live_clipper.analyzer import PluginAnalyzer


def test_plugin_analyzer_uses_hermes_llm_and_persists_candidate(service):
    job = service.add_job("https://youtu.be/abcDEF_123")
    cursor = service.db.execute(
        "INSERT INTO chunks(job_id,sequence,path,start_seconds,duration) VALUES(?,?,?,?,?)",
        (job["id"], 0, "chunk.ts", 0, 20),
    )
    words = [
        {"text": f"word-{index}", "start": float(index), "end": float(index) + 0.5}
        for index in range(16)
    ]
    service.db.add_words(job["id"], cursor.lastrowid, words, 0)

    def complete_structured(**_kwargs):
        return SimpleNamespace(
            parsed={
                "candidates": [
                    {
                        "start_seconds": 0,
                        "end_seconds": 15.5,
                        "title": "Hermes candidate",
                        "hook": "Opening",
                        "payoff": "Payoff",
                        "rationale": "Complete thought",
                        "confidence": 0.9,
                        "standalone_value": 0.8,
                    }
                ]
            }
        )

    analyzer = PluginAnalyzer(service, complete_structured)
    assert analyzer.run_once() == 1
    assert service.db.candidates(job["id"])[0]["title"] == "Hermes candidate"
    assert analyzer.run_once() == 0
