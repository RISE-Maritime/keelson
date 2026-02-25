#!/usr/bin/env python3

"""Tests for n2k-cli - NMEA2000 CAN Gateway Bridge"""

import importlib.util
from importlib.machinery import SourceFileLoader
import pathlib
import sys
import json
from unittest.mock import Mock, AsyncMock
import pytest
from nmea2000.message import NMEA2000Message, NMEA2000Field

# Path to the bin root
bin_root = pathlib.Path(__file__).resolve().parent.parent / "bin"
sys.path.insert(0, str(bin_root))  # Make sibling imports work

# Import the script dynamically
script_path = bin_root / "n2k-cli.py"
loader = SourceFileLoader("n2k_cli", str(script_path))
spec = importlib.util.spec_from_loader(loader.name, loader)
n2k_cli = importlib.util.module_from_spec(spec)
spec.loader.exec_module(n2k_cli)

# Import classes to test
GatewayType = n2k_cli.GatewayType
Protocol = n2k_cli.Protocol
parse_pgn_list = n2k_cli.parse_pgn_list


def test_gateway_type_enum():
    """Test GatewayType enum values"""
    assert GatewayType.TCP.value == "tcp"
    assert GatewayType.USB.value == "usb"


def test_protocol_enum():
    """Test Protocol enum values"""
    assert Protocol.EBYTE.value == "ebyte"
    assert Protocol.ACTISENSE.value == "actisense"
    assert Protocol.YACHT_DEVICES.value == "yacht_devices"
    assert Protocol.WAVESHARE.value == "waveshare"


def test_parse_pgn_list_valid():
    """Test parsing valid PGN list"""
    result = parse_pgn_list("129025,129026,127250")
    assert result == [129025, 129026, 127250]


def test_parse_pgn_list_with_spaces():
    """Test parsing PGN list with spaces"""
    result = parse_pgn_list("129025, 129026, 127250")
    assert result == [129025, 129026, 127250]


def test_parse_pgn_list_empty():
    """Test parsing empty PGN list"""
    result = parse_pgn_list("")
    assert result is None


def test_parse_pgn_list_none():
    """Test parsing None PGN list"""
    result = parse_pgn_list(None)
    assert result is None


def test_parse_pgn_list_invalid():
    """Test parsing invalid PGN list raises error"""
    with pytest.raises(ValueError):
        parse_pgn_list("129025,invalid,127250")


def test_reader_json_output():
    """Test that N2KCLIReader outputs valid JSON"""
    import asyncio

    async def run_test():
        mock_client = AsyncMock()
        mock_client.connect = AsyncMock()
        mock_client.close = AsyncMock()

        reader = n2k_cli.N2KCLIReader(mock_client)

        # Create a test message
        msg = NMEA2000Message(PGN=129025, id="positionRapidUpdate")
        msg.fields = [
            NMEA2000Field(id="latitude", name="Latitude", value=59.0),
            NMEA2000Field(id="longitude", name="Longitude", value=18.0),
        ]

        # Capture stdout
        import io
        from contextlib import redirect_stdout

        f = io.StringIO()
        with redirect_stdout(f):
            await reader.handle_received_message(msg)

        output = f.getvalue()
        assert output.strip()  # Should have output

        # Verify it's valid JSON
        json_data = json.loads(output.strip())
        assert json_data["PGN"] == 129025

    asyncio.run(run_test())


def test_reader_pgn_filtering():
    """Test PGN filtering in reader"""
    mock_client = Mock()

    # Test include filter
    reader = n2k_cli.N2KCLIReader(mock_client, include_pgns=[129025, 129026])
    assert reader.include_pgns == {129025, 129026}

    # Test exclude filter
    reader = n2k_cli.N2KCLIReader(mock_client, exclude_pgns=[60928])
    assert reader.exclude_pgns == {60928}
