from __future__ import annotations

import pytest

from hermes_live_clipper.config import Settings
from hermes_live_clipper.service import LiveClipperService


@pytest.fixture
def service(tmp_path):
    return LiveClipperService(Settings(root=tmp_path, analysis_start_seconds=5))
