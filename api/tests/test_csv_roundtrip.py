"""Export -> import round-trip for the CSV surface.

`/api/export/csv` writes 25 columns and `/api/import/csv` reads them back. These
tests pin down which fields survive that round-trip.

Four of them started life as strict xfails recording real defects — `protocol`
was hardcoded to `bluetooth_le`, `ssid` and `channel` were written but never
read, and `detection_count` was reset to 1 on every new record. All four are
fixed; the assertions below are what keeps them fixed.
"""

import io

import pytest

# A WiFi detection shaped the way the firmware emits them.
DETECTION = {
    "mac_address": "82:6B:F2:11:22:33",
    "protocol": "wifi_2_4ghz",
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


def test_protocol_survives(reimported):
    """A WiFi detection must not come back relabelled as BLE."""
    assert reimported["protocol"] == DETECTION["protocol"]


def test_ssid_survives(reimported):
    """The SSID is load-bearing — a zero-length one is the wildcard-probe tell."""
    assert reimported["ssid"] == DETECTION["ssid"]


def test_channel_survives(reimported):
    assert reimported["channel"] == DETECTION["channel"]


def test_detection_count_survives(reimported):
    """An imported count is a real observation total, not a fresh sighting."""
    assert reimported["detection_count"] == DETECTION["detection_count"]


def test_legacy_exports_without_a_protocol_column_still_import_as_ble(
    client, app_module
):
    """Older ESP32 BLE exports carry no protocol column and must keep defaulting."""
    legacy_csv = b"mac,name,rssi,count\n" b"AA:BB:CC:DD:EE:FF,old-device,-70,3\n"

    response = client.post(
        "/api/import/csv",
        data={"file": (io.BytesIO(legacy_csv), "legacy.csv")},
        content_type="multipart/form-data",
    )

    assert response.status_code == 200
    assert app_module.detections[0]["protocol"] == "bluetooth_le"


# --- deduplication behaviour --------------------------------------------------


def test_reimporting_the_same_mac_increments_rather_than_duplicates(
    client, app_module, reimported
):
    """A second import of the same MAC updates the existing record.

    This is an increment, not a merge: re-importing the same file adds one more
    sighting on top of the stored count, so importing twice is not idempotent.
    """
    exported = client.get("/api/export/csv?type=session")
    assert exported.status_code == 200

    response = client.post(
        "/api/import/csv",
        data={"file": (io.BytesIO(exported.data), "again.csv")},
        content_type="multipart/form-data",
    )
    assert response.status_code == 200

    assert len(app_module.detections) == 1
    assert (
        app_module.detections[0]["detection_count"]
        == DETECTION["detection_count"] + 1
    )
