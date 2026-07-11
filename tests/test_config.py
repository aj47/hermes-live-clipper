from pathlib import Path

from hermes_live_clipper.config import Settings


def test_publisher_profile_can_be_overridden(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_LIVE_CLIPPER_HOME", str(tmp_path))
    monkeypatch.setenv("HLC_PUBLISHER_PROFILE", "publisher-test")

    assert Settings.load().publisher_profile == "publisher-test"


def test_install_script_provisions_scoped_publisher_profile():
    script = (Path(__file__).parents[1] / "scripts" / "install_macos.sh").read_text()

    assert 'publisher_profile="${HLC_PUBLISHER_PROFILE:-live-clipper-publisher}"' in script
    assert 'profile create "$publisher_profile"' in script
    assert 'tools enable computer_use --platform cli' in script
