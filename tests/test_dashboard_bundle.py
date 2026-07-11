from pathlib import Path


def test_dashboard_bundle_registers_with_host_and_uses_authenticated_fetch():
    bundle = (Path(__file__).parents[1] / "dashboard" / "dist" / "index.js").read_text()
    assert 'registry.register("hermes-live-clipper", LiveClipperApp)' in bundle
    assert "authedFetch(api + path" in bundle
    assert "document.body.appendChild" not in bundle
    assert '"Generated clips"' in bundle
    assert "previewClip" in bundle
    assert "saveClip" in bundle
    assert "passive && currentPlayer && !currentPlayer.paused" in bundle
    assert "sendToHermesPublisher" in bundle
    assert 'const publisherBoard = "live-clipper-publishing"' in bundle
    assert "authedFetch(`/api/plugins/kanban${path}`" in bundle
    assert "It may upload and publish this MP4" in bundle
    assert "techfren-review/qa-decision" not in bundle
    assert "CLIP SCORE" in bundle
    assert "WHY IT HOOKS" in bundle
    assert "oneSentence" in bundle
