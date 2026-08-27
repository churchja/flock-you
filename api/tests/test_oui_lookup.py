"""Tests for OUI -> manufacturer lookup.

The first three bytes of a MAC are the OUI. This lookup is what turns a raw
address from the firmware into a vendor name in the dashboard.
"""

import pytest

from flockyou import lookup_manufacturer


@pytest.fixture
def seeded_oui(monkeypatch):
    monkeypatch.setattr("flockyou.oui_database", {"826BF2": "Flock Safety"})


@pytest.mark.parametrize(
    "mac",
    [
        "82:6B:F2:11:22:33",
        "82-6B-F2-11-22-33",
        "826bf2112233",
        "82:6b:f2:11:22:33",
    ],
)
def test_matches_regardless_of_separator_or_case(seeded_oui, mac):
    """Colons, hyphens, bare hex and lowercase all normalise to the same OUI."""
    assert lookup_manufacturer(mac) == "Flock Safety"


def test_unknown_oui_falls_back(seeded_oui):
    assert lookup_manufacturer("00:11:22:33:44:55") == "Unknown Manufacturer"


def test_empty_mac_returns_none():
    assert lookup_manufacturer("") is None
    assert lookup_manufacturer(None) is None


def test_runt_mac_cannot_yield_an_oui():
    """Fewer than 3 bytes is not enough to identify a vendor."""
    assert lookup_manufacturer("82:6B") == "Unknown Manufacturer"
