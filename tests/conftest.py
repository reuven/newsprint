"""Keep the suite out of the real home directory.

newsprint writes two things outside a run's own temp directory: the run
log, and - since packets have to outlive the run that made them - the
packets themselves. Both default to somewhere under ~/.local/state, which
is right in use and wrong in a test: a suite that writes there leaves the
user's own packets mixed up with fixtures, and a sweep test could delete
something real.

Autouse, so it applies to every test whether or not the test knows these
directories exist. A test that wants to look at what was written points
at its own tmp_path explicitly, as several do.
"""

from pathlib import Path

import pytest

from newsprint import runlog
from newsprint.config import DEFAULTS


@pytest.fixture(autouse=True)
def _state_stays_in_tmp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runlog, "DEFAULT_STATE_DIR", tmp_path / "state" / "runs")
    monkeypatch.setitem(
        DEFAULTS["output"], "directory", str(tmp_path / "state" / "packets")
    )
