#!/usr/bin/env python3

"""Tests for n2k2keelson AIS PGN decode handlers (129038, 129039, 129794)."""

import importlib.util
import pathlib
import sys
from datetime import date, datetime, time, timezone
from importlib.machinery import SourceFileLoader
from unittest.mock import Mock

import pytest
import keelson
from keelson.payloads.Primitives_pb2 import (
    TimestampedFloat,
    TimestampedString,
    TimestampedTimestamp,
)
from keelson.payloads.VesselNavStatus_pb2 import VesselNavStatus
from keelson.payloads.VesselType_pb2 import VesselType as VesselTypePb
from nmea2000.message import NMEA2000Field, NMEA2000Message

# Load the bin script dynamically (it is a standalone executable, not a package).
BIN_ROOT = pathlib.Path(__file__).resolve().parent.parent / "bin"
sys.path.insert(0, str(BIN_ROOT))
_loader = SourceFileLoader("n2k2keelson", str(BIN_ROOT / "n2k2keelson.py"))
_spec = importlib.util.spec_from_loader(_loader.name, _loader)
n2k2keelson = importlib.util.module_from_spec(_spec)
_loader.exec_module(n2k2keelson)


REALM = "test/realm"
ENTITY = "sensors"
SOURCE = "n2k/recv"
MMSI = 265547250  # a neutral Swedish MMSI


@pytest.fixture
def recording_session():
    """Mock Zenoh session recording published payloads keyed by key expression."""
    n2k2keelson.PUBLISHERS.clear()
    published: dict[str, list[bytes]] = {}

    def declare_publisher(key_expr, **kwargs):
        published.setdefault(key_expr, [])
        pub = Mock()
        pub.put = Mock(side_effect=lambda data: published[key_expr].append(data))
        return pub

    session = Mock()
    session.declare_publisher = Mock(side_effect=declare_publisher)
    session.published = published
    return session


def _field(field_id, value=None, raw_value=None, unit=None):
    return NMEA2000Field(
        id=field_id, value=value, raw_value=raw_value, unit_of_measurement=unit
    )


def _message(pgn, fields):
    msg = NMEA2000Message(PGN=pgn, id=f"pgn{pgn}", timestamp=datetime.now(timezone.utc))
    msg.fields = fields
    return msg


def _key_for(session, subject):
    """Return the (single) key expression whose subject segment matches."""
    matches = [k for k in session.published if f"/pubsub/{subject}/" in k]
    assert len(matches) <= 1, f"ambiguous keys for {subject}: {matches}"
    return matches[0] if matches else None


def _payload(session, subject):
    """Return (key, decoded payload bytes) for a published subject."""
    key = _key_for(session, subject)
    assert key is not None, f"{subject} was not published"
    payloads = session.published[key]
    assert len(payloads) == 1
    *_, raw = keelson.uncover(payloads[0])
    return key, raw


# --- PGN 129038: AIS Class A position ------------------------------------


def _class_a_position():
    return _message(
        129038,
        [
            _field("userId", value=MMSI, raw_value=MMSI),
            _field("latitude", value=59.32, unit="deg"),
            _field("longitude", value=18.07, unit="deg"),
            _field("cog", value=1.5707963, unit="rad"),
            _field("sog", value=5.144, unit="m/s"),
            _field("heading", value=0.7853982, unit="rad"),
            _field("rateOfTurn", value=0.0, unit="rad/s"),
            _field("navStatus", value="Under way using engine", raw_value=0),
        ],
    )


def test_129038_publishes_all_subjects_with_target(recording_session):
    n2k2keelson.handle_pgn_129038(
        _class_a_position(), recording_session, REALM, ENTITY, SOURCE
    )
    for subject in (
        "mmsi_number",
        "location_fix",
        "course_over_ground_deg",
        "speed_over_ground_knots",
        "heading_true_north_deg",
        "yaw_rate_degps",
        "nav_status",
    ):
        key = _key_for(recording_session, subject)
        assert key is not None, f"{subject} not published"
        assert key.endswith(f"/@target/mmsi_{MMSI}")


def test_129038_unit_conversions(recording_session):
    n2k2keelson.handle_pgn_129038(
        _class_a_position(), recording_session, REALM, ENTITY, SOURCE
    )

    _, cog_raw = _payload(recording_session, "course_over_ground_deg")
    cog = TimestampedFloat()
    cog.ParseFromString(cog_raw)
    assert cog.value == pytest.approx(90.0, abs=1e-3)

    _, sog_raw = _payload(recording_session, "speed_over_ground_knots")
    sog = TimestampedFloat()
    sog.ParseFromString(sog_raw)
    assert sog.value == pytest.approx(5.144 * 1.94384, abs=1e-3)


def test_129038_nav_status_offset(recording_session):
    """The keelson VesselNavStatus enum is the AIS code + 1."""
    n2k2keelson.handle_pgn_129038(
        _class_a_position(), recording_session, REALM, ENTITY, SOURCE
    )
    _, raw = _payload(recording_session, "nav_status")
    nav = VesselNavStatus()
    nav.ParseFromString(raw)
    assert nav.navigation_status == 1  # AIS 0 -> keelson 1


def test_129038_skips_not_available_fields(recording_session):
    msg = _message(
        129038,
        [
            _field("userId", value=MMSI, raw_value=MMSI),
            _field("latitude", value=None, unit="deg"),
            _field("longitude", value=None, unit="deg"),
            _field("cog", value=None, unit="rad"),
            _field("sog", value=None, unit="m/s"),
            _field("heading", value=None, unit="rad"),
            _field("rateOfTurn", value=None, unit="rad/s"),
            _field("navStatus", value=None, raw_value=None),
        ],
    )
    n2k2keelson.handle_pgn_129038(msg, recording_session, REALM, ENTITY, SOURCE)
    # Only the MMSI is always available.
    assert _key_for(recording_session, "mmsi_number") is not None
    for subject in ("location_fix", "course_over_ground_deg", "nav_status"):
        assert _key_for(recording_session, subject) is None


def test_129038_without_mmsi_publishes_nothing(recording_session):
    msg = _message(129038, [_field("userId", value=None, raw_value=None)])
    n2k2keelson.handle_pgn_129038(msg, recording_session, REALM, ENTITY, SOURCE)
    assert recording_session.published == {}


# --- PGN 129039: AIS Class B position ------------------------------------


def test_129039_publishes_position_without_nav_status(recording_session):
    msg = _message(
        129039,
        [
            _field("userId", value=MMSI, raw_value=MMSI),
            _field("latitude", value=59.32, unit="deg"),
            _field("longitude", value=18.07, unit="deg"),
            _field("cog", value=1.5707963, unit="rad"),
            _field("sog", value=5.144, unit="m/s"),
            _field("heading", value=0.7853982, unit="rad"),
        ],
    )
    n2k2keelson.handle_pgn_129039(msg, recording_session, REALM, ENTITY, SOURCE)

    for subject in (
        "mmsi_number",
        "location_fix",
        "course_over_ground_deg",
        "speed_over_ground_knots",
        "heading_true_north_deg",
    ):
        assert _key_for(recording_session, subject) is not None
    # Class B carries neither navigation status nor rate of turn.
    assert _key_for(recording_session, "nav_status") is None
    assert _key_for(recording_session, "yaw_rate_degps") is None


# --- PGN 129794: AIS Class A static --------------------------------------


def _class_a_static():
    return _message(
        129794,
        [
            _field("userId", value=MMSI, raw_value=MMSI),
            _field("name", value="KEELSON TEST"),
            _field("callsign", value="SHIP1"),
            _field("imoNumber", value=9811000),
            _field("typeOfShip", value="Cargo", raw_value=70),
            _field("length", value=120.0, unit="m"),
            _field("beam", value=20.0, unit="m"),
            _field("draft", value=6.5, unit="m"),
            _field("etaDate", value=date(2026, 6, 1)),
            _field("etaTime", value=time(13, 30)),
            _field("destination", value="GOTHENBURG"),
        ],
    )


def test_129794_publishes_static_subjects(recording_session):
    n2k2keelson.handle_pgn_129794(
        _class_a_static(), recording_session, REALM, ENTITY, SOURCE
    )
    for subject in (
        "mmsi_number",
        "name",
        "call_sign",
        "imo_number",
        "vessel_type",
        "length_over_all_m",
        "breadth_over_all_m",
        "draught_mean_m",
        "eta",
        "destination",
    ):
        key = _key_for(recording_session, subject)
        assert key is not None, f"{subject} not published"
        assert key.endswith(f"/@target/mmsi_{MMSI}")


def test_129794_vessel_type_passthrough(recording_session):
    """The keelson VesselType values are the AIS ship-type codes directly."""
    n2k2keelson.handle_pgn_129794(
        _class_a_static(), recording_session, REALM, ENTITY, SOURCE
    )
    _, raw = _payload(recording_session, "vessel_type")
    vessel_type = VesselTypePb()
    vessel_type.ParseFromString(raw)
    assert vessel_type.vessel_type == 70


def test_129794_eta_reconstructed(recording_session):
    n2k2keelson.handle_pgn_129794(
        _class_a_static(), recording_session, REALM, ENTITY, SOURCE
    )
    _, raw = _payload(recording_session, "eta")
    eta = TimestampedTimestamp()
    eta.ParseFromString(raw)
    expected_ns = int(
        datetime(2026, 6, 1, 13, 30, tzinfo=timezone.utc).timestamp() * 1_000_000_000
    )
    assert eta.value.ToNanoseconds() == expected_ns


def test_129794_name_value(recording_session):
    n2k2keelson.handle_pgn_129794(
        _class_a_static(), recording_session, REALM, ENTITY, SOURCE
    )
    _, raw = _payload(recording_session, "name")
    name = TimestampedString()
    name.ParseFromString(raw)
    assert name.value == "KEELSON TEST"


# --- target_id format + regression ---------------------------------------


def test_target_id_format_is_mmsi_prefixed(recording_session):
    """The target_id shared with the ais connector is 'mmsi_<MMSI>'."""
    n2k2keelson.handle_pgn_129038(
        _class_a_position(), recording_session, REALM, ENTITY, SOURCE
    )
    key = _key_for(recording_session, "location_fix")
    assert "/@target/mmsi_265547250" in key


def test_non_ais_handler_publishes_without_target(recording_session):
    """Existing non-AIS handlers must still publish with no @target segment."""
    msg = _message(
        129025,
        [
            _field("latitude", value=59.32, unit="deg"),
            _field("longitude", value=18.07, unit="deg"),
        ],
    )
    n2k2keelson.handle_pgn_129025(msg, recording_session, REALM, ENTITY, SOURCE)
    key = _key_for(recording_session, "location_fix")
    assert key is not None
    assert "@target" not in key


def test_ais_pgns_registered():
    for pgn in (129038, 129039, 129794):
        assert pgn in n2k2keelson.PGN_HANDLERS
