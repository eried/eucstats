from ingest.parser import parse_csv

DARKNESSBOT = (
    "Date,Speed,Voltage,PWM,Current,Power,Battery level,Total mileage,Temperature,Pitch,Roll,Latitude,Longitude,Altitude\n"
    "2025-05-30T20:17:22.000000,0.0,121.8,,8.7,1055.9,67.0,0.0,27.0,,,69.6545046,18.9190817,50.7\n"
    "2025-05-30T20:17:24.000000,19.4,121.7,,9.2,1122.6,67.0,0.011,27.0,,,69.6544136,18.919216,52.5\n"
)

EUCPLANET = (
    "Date,Speed,Voltage,Temperature,Battery level,Altitude,Latitude,Longitude,Total mileage,GPS speed,Current,PWM,G-Force,G-Force X,G-Force Y\n"
    "01.06.2026 20:24:31.204,1.9,132.2,26.0,100,68.5,69.667394,18.924065,1653.4,5.4,-0.1,0.0,0.0,0.0,0.0\n"
    "01.06.2026 20:24:32.208,5.2,132.2,27.0,100,68.5,69.667393,18.924111,1653.4,5.2,0.2,1.8,0.3,0.1,0.2\n"
)


def test_parse_darknessbot():
    s = parse_csv(DARKNESSBOT)
    assert len(s) == 2
    assert s[0].lat == 69.6545046 and s[0].lon == 18.9190817
    assert s[0].speed == 0.0 and s[1].speed == 19.4
    assert s[0].power == 1055.9
    assert s[0].pwm is None          # empty column -> None
    assert s[0].g is None            # column absent -> None
    assert s[0].odo == 0.0 and s[1].odo == 0.011
    assert s[0].t.year == 2025 and s[0].t.tzinfo is not None


def test_parse_eucplanet_gforce():
    s = parse_csv(EUCPLANET)
    assert len(s) == 2
    assert s[0].gps_speed == 5.4
    assert s[0].pwm == 0.0
    assert s[1].g == 0.3
    assert s[0].odo == 1653.4
    assert s[0].t.year == 2026 and s[0].t.month == 6


def test_tz_offset_applied():
    # local 20:24 at +120 min -> 18:24 UTC
    s = parse_csv(EUCPLANET, tz_offset_min=120)
    assert s[0].t.hour == 18


def test_no_date_column_raises():
    import pytest
    with pytest.raises(ValueError):
        parse_csv("Speed,Voltage\n1,2\n")
