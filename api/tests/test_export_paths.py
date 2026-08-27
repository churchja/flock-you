"""Where the export endpoints write files, versus where Flask serves them from.

`export_csv` builds its path with `os.path.join('exports', filename)`, which is
relative to the **current working directory**. It then hands that same relative
path to `send_file`, which Flask resolves against **`app.root_path`** — the
directory containing `flockyou.py`.

Those two agree only when the app is launched from inside `api/`. Launch it from
the repo root (`python api/flockyou.py`) and every CSV/KML export writes to
`<root>/exports/` but is served from `<root>/api/exports/`, so the download
fails even though the file was written successfully.
"""

import pytest

DETECTION = {
    "mac_address": "82:6B:F2:11:22:33",
    "protocol": "wifi",
    "detection_method": "wifi_oui_addr2",
    "rssi": -60,
    "detection_count": 1,
}


def test_export_works_when_launched_from_the_app_directory(client, app_module):
    """The supported layout: CWD and app root are the same directory."""
    app_module.detections.append(dict(DETECTION))

    response = client.get("/api/export/csv?type=session")

    assert response.status_code == 200
    assert b"82:6B:F2:11:22:33" in response.data


@pytest.mark.xfail(
    strict=True,
    reason="export_csv writes to exports/ relative to the CWD but send_file "
    "resolves against app.root_path, so exporting breaks whenever the app is "
    "launched from anywhere other than api/",
)
def test_export_works_when_launched_from_the_repo_root(
    client, app_module, tmp_path, monkeypatch
):
    """Reproduces `python api/flockyou.py` run from the repo root."""
    launch_dir = tmp_path / "repo_root"
    app_root = launch_dir / "api"
    app_root.mkdir(parents=True)
    (launch_dir / "data").mkdir()

    monkeypatch.chdir(launch_dir)
    monkeypatch.setattr(app_module.app, "root_path", str(app_root))

    app_module.detections.append(dict(DETECTION))

    response = client.get("/api/export/csv?type=session")

    assert response.status_code == 200
