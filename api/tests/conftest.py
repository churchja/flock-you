"""Shared fixtures for the dashboard tests.

`flockyou.py` is a single module that keeps its state in module-level globals and
resolves its write paths (`data/`, `exports/`) against the current working
directory, while Flask serves files back relative to `app.root_path`. Those two
agree only when the app runs from inside `api/`, which is the configuration
these fixtures reproduce: each test gets a throwaway directory that is both the
CWD and the app root, so a run never writes into the repo.

Importing the module also mkdirs `data/` once, relative to wherever pytest was
started; that stray directory is gitignored.
"""

import sys
from pathlib import Path

import pytest

API_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(API_DIR))

import flockyou  # noqa: E402  (path must be set up first)


@pytest.fixture(autouse=True)
def sandbox(tmp_path, monkeypatch):
    """Point the CWD and the app root at the same throwaway directory."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(flockyou.app, "root_path", str(tmp_path))
    (tmp_path / "data").mkdir(exist_ok=True)
    yield tmp_path


@pytest.fixture(autouse=True)
def reset_state():
    """Clear the module globals that accumulate detections."""
    flockyou.detections.clear()
    flockyou.cumulative_detections.clear()
    flockyou.gps_history.clear()
    flockyou.gps_data = None
    flockyou.next_detection_id = 1
    yield
    flockyou.detections.clear()
    flockyou.cumulative_detections.clear()


@pytest.fixture
def client():
    flockyou.app.config["TESTING"] = True
    with flockyou.app.test_client() as test_client:
        yield test_client


@pytest.fixture
def app_module():
    return flockyou
