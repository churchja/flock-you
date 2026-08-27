"""Tests for NMEA sentence parsing.

`parse_nmea_sentence` is the seam where a GPS dongle's raw output becomes the
coordinates that tag every detection. It only understands GGA fix sentences
($GPGGA / $GNGGA) and returns None for everything else.
"""

import pytest

from flockyou import parse_nmea_sentence

# A well-formed fix: 48°07.038'N 011°31.000'E, quality 1, 8 satellites.
GPGGA_FIX = "$GPGGA,123519,4807.038,N,01131.000,E,1,08,0.9,545.4,M,46.9,M,,*47"


def test_parses_a_gpgga_fix():
    result = parse_nmea_sentence(GPGGA_FIX)

    assert result is not None
    # 48 + 7.038/60 and 11 + 31.000/60, rounded to 8dp by the parser.
    assert result["latitude"] == pytest.approx(48.1173)
    assert result["longitude"] == pytest.approx(11.51666667)
    assert result["altitude"] == pytest.approx(545.4)
    assert result["fix_quality"] == 1
    assert result["satellites"] == 8
    assert result["hdop"] == pytest.approx(0.9)
    assert result["timestamp"] == "123519"


def test_accepts_gnss_combined_sentences():
    """$GNGGA is the GPS+GLONASS variant and must parse identically."""
    result = parse_nmea_sentence(GPGGA_FIX.replace("$GPGGA", "$GNGGA"))

    assert result is not None
    assert result["latitude"] == pytest.approx(48.1173)


def test_southern_and_western_hemispheres_are_negated():
    southwest = "$GPGGA,123519,4807.038,S,01131.000,W,1,08,0.9,545.4,M,46.9,M,,*47"

    result = parse_nmea_sentence(southwest)

    assert result["latitude"] == pytest.approx(-48.1173)
    assert result["longitude"] == pytest.approx(-11.51666667)


@pytest.mark.parametrize(
    ("sentence", "reason"),
    [
        (
            "$GPGGA,123519,4807.038,N,01131.000,E,0,08,0.9,545.4,M,46.9,M,,*47",
            "fix_quality 0 means no fix",
        ),
        ("GPGGA,123519,4807.038,N", "missing the leading $"),
        ("$GPGGA,123519,4807.038,N", "too few fields to be a fix"),
        (
            "$GPGGA,123519,,N,,E,1,08,0.9,545.4,M,46.9,M,,*47",
            "claims a fix but carries no coordinates",
        ),
        (
            "$GPGGA,123519,ABCD.EFG,N,01131.000,E,1,08,0.9,545.4,M,46.9,M,,*47",
            "latitude is not a number",
        ),
        (
            "$GPRMC,123519,A,4807.038,N,01131.000,E,022.4,084.4,230394,003.1,W*6A",
            "RMC is not a GGA fix sentence",
        ),
        ("", "empty line"),
    ],
)
def test_returns_none_for_unusable_sentences(sentence, reason):
    assert parse_nmea_sentence(sentence) is None, reason
