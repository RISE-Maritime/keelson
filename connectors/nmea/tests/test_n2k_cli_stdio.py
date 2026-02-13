#!/usr/bin/env python3

"""Tests for n2k-cli stdio gateway type and canboat conversion."""

import importlib.util
from importlib.machinery import SourceFileLoader
import pathlib
import sys
from datetime import datetime, timezone
from nmea2000.message import NMEA2000Message, NMEA2000Field

# Path to the bin root
bin_root = pathlib.Path(__file__).resolve().parent.parent / "bin"
sys.path.insert(0, str(bin_root))

# Import the script dynamically
script_path = bin_root / "n2k-cli.py"
loader = SourceFileLoader("n2k_cli", str(script_path))
spec = importlib.util.spec_from_loader(loader.name, loader)
n2k_cli = importlib.util.module_from_spec(spec)
spec.loader.exec_module(n2k_cli)

# Import functions and classes to test
GatewayType = n2k_cli.GatewayType
Protocol = n2k_cli.Protocol
convert_canboat_to_nmea2000 = n2k_cli.convert_canboat_to_nmea2000
convert_nmea2000_to_canboat = n2k_cli.convert_nmea2000_to_canboat
CANBOAT_FIELD_MAP = n2k_cli.CANBOAT_FIELD_MAP
NMEA2000_TO_CANBOAT_FIELD = n2k_cli.NMEA2000_TO_CANBOAT_FIELD


def test_gateway_type_stdio():
    """Test STDIO gateway type enum value"""
    assert GatewayType.STDIO.value == "stdio"


def test_protocol_canboat_json():
    """Test CANBOAT_JSON protocol enum value"""
    assert Protocol.CANBOAT_JSON.value == "canboat-json"


class TestCanboatToNmea2000:
    def test_convert_position(self):
        """Test converting canboat position message to NMEA2000"""
        canboat = {
            "timestamp": "2026-02-12T13:18:55.658Z",
            "prio": 2,
            "src": 2,
            "dst": 255,
            "pgn": 129025,
            "description": "Position, Rapid Update",
            "fields": {"Latitude": 57.6892076, "Longitude": 11.9750416},
        }

        msg = convert_canboat_to_nmea2000(canboat)

        assert msg.PGN == 129025
        assert msg.source == 2
        assert msg.destination == 255
        assert msg.priority == 2
        assert msg.description == "Position, Rapid Update"

        lat = next(f for f in msg.fields if f.id == "latitude")
        assert lat.value == 57.6892076
        assert lat.unit_of_measurement == "deg"

        lon = next(f for f in msg.fields if f.id == "longitude")
        assert lon.value == 11.9750416
        assert lon.unit_of_measurement == "deg"

    def test_convert_cog_sog(self):
        """Test converting canboat COG/SOG message to NMEA2000"""
        canboat = {
            "pgn": 129026,
            "src": 2,
            "fields": {"COG": 180.5, "SOG": 5.14, "COG Reference": "True"},
        }

        msg = convert_canboat_to_nmea2000(canboat)

        assert msg.PGN == 129026

        cog = next(f for f in msg.fields if f.id == "cog")
        assert cog.value == 180.5
        assert cog.unit_of_measurement == "deg"

        sog = next(f for f in msg.fields if f.id == "sog")
        assert sog.value == 5.14
        assert sog.unit_of_measurement == "m/s"

    def test_convert_attitude(self):
        """Test converting canboat attitude message to NMEA2000"""
        canboat = {"pgn": 127257, "fields": {"SID": 152, "Pitch": 0.6, "Roll": 0.7}}

        msg = convert_canboat_to_nmea2000(canboat)

        assert msg.PGN == 127257

        pitch = next(f for f in msg.fields if f.id == "pitch")
        assert pitch.value == 0.6
        assert pitch.unit_of_measurement == "deg"

        roll = next(f for f in msg.fields if f.id == "roll")
        assert roll.value == 0.7
        assert roll.unit_of_measurement == "deg"

    def test_convert_heading(self):
        """Test converting canboat heading message to NMEA2000"""
        canboat = {"pgn": 127250, "fields": {"Heading": 270.5}}

        msg = convert_canboat_to_nmea2000(canboat)

        heading = next(f for f in msg.fields if f.id == "heading")
        assert heading.value == 270.5
        assert heading.unit_of_measurement == "deg"

    def test_convert_timestamp(self):
        """Test timestamp conversion from ISO format"""
        canboat = {
            "timestamp": "2026-02-12T13:18:55.658Z",
            "pgn": 129025,
            "fields": {},
        }

        msg = convert_canboat_to_nmea2000(canboat)

        assert msg.timestamp.year == 2026
        assert msg.timestamp.month == 2
        assert msg.timestamp.day == 12
        assert msg.timestamp.hour == 13

    def test_convert_defaults(self):
        """Test default values when fields are missing"""
        canboat = {"pgn": 129025, "fields": {}}

        msg = convert_canboat_to_nmea2000(canboat)

        assert msg.source == 0
        assert msg.destination == 255
        assert msg.priority == 0

    def test_convert_unknown_field(self):
        """Test that unknown fields are normalized (spaces removed)"""
        canboat = {"pgn": 129025, "fields": {"Some Unknown Field": 42}}

        msg = convert_canboat_to_nmea2000(canboat)

        field = msg.fields[0]
        assert field.id == "someunknownfield"
        assert field.name == "Some Unknown Field"
        assert field.value == 42


class TestNmea2000ToCanboat:
    def test_convert_position(self):
        """Test converting NMEA2000 position message to canboat"""
        msg = NMEA2000Message(
            PGN=129025,
            source=2,
            destination=255,
            priority=2,
            description="Position, Rapid Update",
            timestamp=datetime(2026, 2, 12, 13, 18, 55, tzinfo=timezone.utc),
        )
        msg.fields = [
            NMEA2000Field(id="latitude", value=57.68),
            NMEA2000Field(id="longitude", value=11.97),
        ]

        canboat = convert_nmea2000_to_canboat(msg)

        assert canboat["pgn"] == 129025
        assert canboat["src"] == 2
        assert canboat["dst"] == 255
        assert canboat["prio"] == 2
        assert canboat["fields"]["Latitude"] == 57.68
        assert canboat["fields"]["Longitude"] == 11.97

    def test_convert_attitude(self):
        """Test converting NMEA2000 attitude message to canboat"""
        msg = NMEA2000Message(PGN=127257)
        msg.fields = [
            NMEA2000Field(id="pitch", value=1.5),
            NMEA2000Field(id="roll", value=2.0),
            NMEA2000Field(id="yaw", value=0.5),
        ]

        canboat = convert_nmea2000_to_canboat(msg)

        assert canboat["fields"]["Pitch"] == 1.5
        assert canboat["fields"]["Roll"] == 2.0
        assert canboat["fields"]["Yaw"] == 0.5

    def test_convert_cog_sog(self):
        """Test converting NMEA2000 COG/SOG message to canboat"""
        msg = NMEA2000Message(PGN=129026)
        msg.fields = [
            NMEA2000Field(id="cog", value=180.0),
            NMEA2000Field(id="sog", value=5.0),
        ]

        canboat = convert_nmea2000_to_canboat(msg)

        assert canboat["fields"]["Cog"] == 180.0
        assert canboat["fields"]["Sog"] == 5.0


class TestRoundtrip:
    def test_canboat_nmea2000_canboat(self):
        """Test round-trip conversion canboat → nmea2000 → canboat"""
        original = {
            "pgn": 129025,
            "src": 2,
            "prio": 2,
            "dst": 255,
            "description": "Position, Rapid Update",
            "fields": {"Latitude": 57.68, "Longitude": 11.97},
        }

        msg = convert_canboat_to_nmea2000(original)
        result = convert_nmea2000_to_canboat(msg)

        assert result["pgn"] == original["pgn"]
        assert result["src"] == original["src"]
        assert result["prio"] == original["prio"]
        assert result["dst"] == original["dst"]
        assert result["fields"]["Latitude"] == original["fields"]["Latitude"]
        assert result["fields"]["Longitude"] == original["fields"]["Longitude"]

    def test_nmea2000_canboat_nmea2000(self):
        """Test round-trip conversion nmea2000 → canboat → nmea2000"""
        original = NMEA2000Message(
            PGN=129025,
            source=2,
            destination=255,
            priority=2,
            description="Position, Rapid Update",
        )
        original.fields = [
            NMEA2000Field(id="latitude", value=57.68, unit_of_measurement="deg"),
            NMEA2000Field(id="longitude", value=11.97, unit_of_measurement="deg"),
        ]

        canboat = convert_nmea2000_to_canboat(original)
        result = convert_canboat_to_nmea2000(canboat)

        assert result.PGN == original.PGN
        assert result.source == original.source
        assert result.destination == original.destination
        assert result.priority == original.priority

        lat = next(f for f in result.fields if f.id == "latitude")
        assert lat.value == 57.68


class TestFieldMappings:
    def test_canboat_field_map_contains_expected_fields(self):
        """Test that CANBOAT_FIELD_MAP contains expected mappings"""
        expected_mappings = {
            "latitude": ("latitude", "deg"),
            "longitude": ("longitude", "deg"),
            "heading": ("heading", "deg"),
            "cog": ("cog", "deg"),
            "sog": ("sog", "m/s"),
            "pitch": ("pitch", "deg"),
            "roll": ("roll", "deg"),
            "yaw": ("yaw", "deg"),
        }

        for canboat_name, expected in expected_mappings.items():
            assert canboat_name in CANBOAT_FIELD_MAP
            assert CANBOAT_FIELD_MAP[canboat_name] == expected

    def test_reverse_mapping(self):
        """Test that reverse mapping is consistent"""
        # latitude -> Latitude
        assert NMEA2000_TO_CANBOAT_FIELD["latitude"] == "Latitude"
        # cog -> Cog
        assert NMEA2000_TO_CANBOAT_FIELD["cog"] == "Cog"
        # windSpeed -> Windspeed
        assert NMEA2000_TO_CANBOAT_FIELD["windSpeed"] == "Windspeed"


class TestStdioReader:
    def test_reader_init(self):
        """Test N2KCLIStdioReader initialization"""
        reader = n2k_cli.N2KCLIStdioReader(
            include_pgns=[129025, 129026], exclude_pgns=[60928]
        )

        assert reader.include_pgns == {129025, 129026}
        assert reader.exclude_pgns == {60928}
        assert reader.running is True

    def test_reader_init_no_filters(self):
        """Test N2KCLIStdioReader initialization without filters"""
        reader = n2k_cli.N2KCLIStdioReader()

        assert reader.include_pgns == set()
        assert reader.exclude_pgns == set()


class TestStdioWriter:
    def test_writer_init(self):
        """Test N2KCLIStdioWriter initialization"""
        writer = n2k_cli.N2KCLIStdioWriter(include_pgns=[129025], exclude_pgns=[127258])

        assert writer.include_pgns == {129025}
        assert writer.exclude_pgns == {127258}
        assert writer.running is True

    def test_writer_init_no_filters(self):
        """Test N2KCLIStdioWriter initialization without filters"""
        writer = n2k_cli.N2KCLIStdioWriter()

        assert writer.include_pgns == set()
        assert writer.exclude_pgns == set()
