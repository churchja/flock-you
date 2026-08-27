"""Where the export endpoints write files, versus where Flask serves them from.

`export_csv` and `export_kml` used to build their output path with
`os.path.join('exports', filename)`, which is relative to the **current working
directory**. They then handed that same relative path to `send_file`, which
Flask resolves against **`app.root_path`** — the directory containing
`flockyou.py`.

Those two agree only when the app is launched from inside `api/`. Launched from
the repo root (`python api/flockyou.py`), every export wrote to
`<root>/exports/` but was served from `<root>/api/exports/`, so the download
failed even though the file had been written successfully.

Both endpoints now anchor to `app.root_path`, so the launch directory no longer
matters. These tests hold that line.
"""

import pytest

DETECTION = {
    "mac_address": "82:6B:F2:11:22:33",
    "protocol": "wifi_2_4ghz",
    "detection_method": "wifi_oui_addr2",
    "rssi": -60,
    "detection_count": 1,
    "gps": {"latitude": 37.1, "longitude": -94.8, "fix_quality": 1},
}


@pytest.fixture
def launched_from_repo_root(app_module, tmp_path, monkeypatch):
    """Reproduce `python api/flockyou.py` run from the repo root.

    The CWD and `app.root_path` deliberately differ, which is exactly the
    arrangement that used to break exports.
    """
    launch_dir = tmp_path / "repo_root"
    app_root = launch_dir / "api"
    app_root.mkdir(parents=True)
    (launch_dir / "data").mkdir()

    monkeypatch.chdir(launch_dir)
    monkeypatch.setattr(app_module.app, "root_path", str(app_root))
    return launch_dir


def test_csv_export_works_when_launched_from_the_app_directory(client, app_module):
    """The supported layout: CWD and app root are the same directory."""
    app_module.detections.append(dict(DETECTION))

    response = client.get("/api/export/csv?type=session")

    assert response.status_code == 200
    assert b"82:6B:F2:11:22:33" in response.data


def test_csv_export_works_when_launched_from_the_repo_root(
    client, app_module, launched_from_repo_root
):
    app_module.detections.append(dict(DETECTION))

    response = client.get("/api/export/csv?type=session")

    assert response.status_code == 200
    assert b"82:6B:F2:11:22:33" in response.data


def test_kml_export_works_when_launched_from_the_repo_root(
    client, app_module, launched_from_repo_root
):
    """KML had the identical defect and takes the identical fix."""
    app_module.detections.append(dict(DETECTION))

    response = client.get("/api/export/kml?type=session")

    assert response.status_code == 200
    assert b"<kml" in response.data


def test_exports_land_under_the_app_root_not_the_launch_directory(
    client, app_module, launched_from_repo_root
):
    app_module.detections.append(dict(DETECTION))

    assert client.get("/api/export/csv?type=session").status_code == 200

    launch_dir = launched_from_repo_root
    assert list((launch_dir / "api" / "exports").glob("*.csv")), "expected under app root"
    assert not (launch_dir / "exports").exists(), "must not write beside the CWD"
