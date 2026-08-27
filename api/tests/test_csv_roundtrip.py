"""Export -> import round-trip for the CSV surface.

`/api/export/csv` writes 25 columns; `/api/import/csv` reads back a subset. These
tests pin down which fields actually survive a round-trip and which are silently
dropped or rewritten.

The dropped ones are recorded as strict xfails rather than left untested: if
someone fixes the import side, the xfail turns into an XPASS and fails the run,
which is the prompt to promote it to a real assertion.
"""

import io

import pytest

# A WiFi detection shaped the way the firmware emits them.
DETECTION = {
    "mac_address": "82:6B:F2:11:22:33",
    "protocol": "wifi",
    "detection_method": "wifi_oui_addr2",
    "ssid": "FlockSafety-Cam",
    "device_name": "flock-cam",
    "rssi": -67,
    "channel": 6,
    "detection_count": 5,
    "manufacturer": "Flock Safety",
    "alias": "",
    "timestamp": "2026-08-27T03:00:00",
    "detection_time": "2026-08-27 03:00:00",
    "server_timestamp": "2026-08-27T03:00:00",
    "timestamp_source": "system",
    "gps": {
        "latitude": 37.12345678,
        "longitude": -94.87654321,
        "altitude": 315.5,
        "timestamp": "030000",
        "satellites": 9,
        "fix_quality": 1,
        "time_diff": 0.4,
        "match_quality": "temporal",
    },
}


@pytest.fixture
def reimported(client, app_module):
    """Seed one detection, export it to CSV, then import that CSV back.

    The seeded detection is cleared before the import so the import creates a
    fresh record instead of being deduplicated onto the original by MAC.
    """
    app_module.detections.append(dict(DETECTION))

    exported = client.get("/api/export/csv?type=session")
    assert exported.status_code == 200, "export should succeed with one detection"
    csv_bytes = exported.data

    app_module.detections.clear()
    app_module.cumulative_detections.clear()

    response = client.post(
        "/api/import/csv",
        data={"file": (io.BytesIO(csv_bytes), "roundtrip.csv")},
        content_type="multipart/form-data",
    )
    assert response.status_code == 200, response.data
    assert response.get_json()["count"] == 1

    assert len(app_module.detections) == 1
    return app_module.detections[0]


def test_export_refuses_when_there_is_nothing_to_export(client):
    response = client.get("/api/export/csv?type=session")

    assert response.status_code == 400
    assert response.get_json()["status"] == "error"


# --- fields that survive the round-trip ---------------------------------------


def test_mac_address_survives(reimported):
    assert reimported["mac_address"] == DETECTION["mac_address"]


def test_device_name_survives(reimported):
    assert reimported["device_name"] == DETECTION["device_name"]


def test_detection_method_survives(reimported):
    """The method distinguishes a wildcard-probe hit from a plain OUI match."""
    assert reimported["detection_method"] == DETECTION["detection_method"]


def test_rssi_survives_as_an_int(reimported):
    assert reimported["rssi"] == DETECTION["rssi"]


def test_gps_coordinates_survive(reimported):
    assert reimported["gps"]["latitude"] == pytest.approx(DETECTION["gps"]["latitude"])
    assert reimported["gps"]["longitude"] == pytest.approx(DETECTION["gps"]["longitude"])


# --- fields that do not survive ----------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason="import_csv hardcodes protocol='bluetooth_le', so WiFi detections "
    "come back mislabelled as BLE",
)
def test_protocol_survives(reimported):
    assert reimported["protocol"] == "wifi"


@pytest.mark.xfail(
    strict=True,
    reason="export writes an ssid column but import never reads it, so the SSID "
    "is lost on re-import",
)
def test_ssid_survives(reimported):
    assert reimported.get("ssid") == DETECTION["ssid"]


@pytest.mark.xfail(
    strict=True,
    reason="add_detection_from_serial resets detection_count to 1 for any new "
    "record, discarding the count parsed from the CSV",
)
def test_detection_count_survives(reimported):
    assert reimported["detection_count"] == DETECTION["detection_count"]


@pytest.mark.xfail(
    strict=True,
    reason="export writes a channel column but import never reads it",
)
def test_channel_survives(reimported):
    assert reimported.get("channel") == DETECTION["channel"]


# --- deduplication behaviour --------------------------------------------------


def test_reimporting_the_same_mac_increments_rather_than_duplicates(
    client, app_module, reimported
):
    """A second import of the same MAC updates the existing record."""
    exported = client.get("/api/export/csv?type=session")
    assert exported.status_code == 200

    response = client.post(
        "/api/import/csv",
        data={"file": (io.BytesIO(exported.data), "again.csv")},
        content_type="multipart/form-data",
    )
    assert response.status_code == 200

    assert len(app_module.detections) == 1
    assert app_module.detections[0]["detection_count"] == 2
