from pathlib import Path


def test_dashboard_bundle_registers_with_host_and_uses_authenticated_fetch():
    bundle = (Path(__file__).parents[1] / "dashboard" / "dist" / "index.js").read_text()
    assert 'registry.register("hermes-live-clipper", LiveClipperApp)' in bundle
    assert "authedFetch(api + path" in bundle
    assert "document.body.appendChild" not in bundle
    assert 'role:"Story Analyst"' in bundle
    assert 'role:"Video Editor"' in bundle
    assert 'role:"Publisher & Growth"' in bundle
    assert "previewClip" in bundle
    assert "saveClip" in bundle
    assert "passive && currentPlayer && !currentPlayer.paused" in bundle
    assert "sendToHermesPublisher" in bundle
    assert 'const publisherBoard = "live-clipper-publishing"' in bundle
    assert "authedFetch(`/api/plugins/kanban${path}`" in bundle
    assert 'kanbanRequest("/boards", {method:"POST"' in bundle
    assert 'slug:publisherBoard,name:"Live Clipper Publishing"' in bundle
    assert '"Retry with signed-in Chrome"' in bundle
    assert 'renderItem.publisher_result?.status' in bundle
    assert 'method:"PATCH",body:JSON.stringify({status:"ready"})' in bundle
    assert 'retryTaskId?"reopened":"queued"' in bundle
    assert "state.error = message" in bundle
    assert 'role:"alert"}, state.error' in bundle
    assert "It may upload and publish this MP4" in bundle
    assert "techfren-review/qa-decision" not in bundle
    assert "CLIP SCORE" in bundle
    assert "WHY IT HOOKS" in bundle
    assert "oneSentence" in bundle
    assert "CLIP ACTIVITY" in bundle
    assert "activityPanel" in bundle
    assert "latestReadyRender" in bundle
    assert "View rendered clip" in bundle
    assert "Render another version" not in bundle
    assert "publisherConsole" in bundle
    assert "publisher_progress" in bundle
    assert "/log?tail=100000" in bundle
    assert "/messages?limit=500" in bundle
    assert "Open Hermes session" in bundle
    assert "Open published post" in bundle
    assert "Worker stdout / stderr" in bundle
    assert "redactLog" in bundle
    assert "Review deletion" in bundle
    assert "Reject remains non-destructive" in bundle
    assert 'request("/cleanup/preview"' in bundle
    assert 'request("/cleanup/execute"' in bundle
    assert "Select stopped" in bundle
    assert "Select failed renders" in bundle
    assert "force_publisher_assets" in bundle
