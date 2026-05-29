#!/usr/bin/env python3

"""Tests for ntrip2keelson.py."""

import base64
import io
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from pyrtcm import RTCMReader

from conftest import RTCM_1005_FRAME, ntrip2keelson


@pytest.mark.unit
def test_ntrip2keelson_help(run_connector):
    """ntrip2keelson should be runnable through the connector test helper."""
    result = run_connector("rtcm", "ntrip2keelson", ["--help"])

    assert result.returncode == 0
    assert "ntrip2keelson" in result.stdout


@pytest.mark.unit
class TestNMEAChecksum:
    """Test NMEA checksum generation."""

    def test_checksum_known_gga_body(self):
        """Checksum should match a known NMEA GGA body."""
        body = (
            "GPGGA,123519,4807.038,N,01131.000,E,1,08,0.9,"
            "545.4,M,46.9,M,,"
        )

        assert ntrip2keelson.nmea_checksum(body) == "47"

    def test_checksum_is_two_uppercase_hex_chars(self):
        """Checksum should be formatted as two uppercase hexadecimal chars."""
        checksum = ntrip2keelson.nmea_checksum("GPGGA")

        assert len(checksum) == 2
        assert checksum == checksum.upper()
        int(checksum, 16)


@pytest.mark.unit
class TestLatLonFormatting:
    """Test conversion from decimal degrees to NMEA ddmm.mmmm format."""

    def test_formats_northern_eastern_position(self):
        """Positive latitude/longitude should use N/E hemispheres."""
        lat, lat_dir, lon, lon_dir = ntrip2keelson.format_lat_lon(
            57.6890020,
            11.9756379,
        )

        assert lat_dir == "N"
        assert lon_dir == "E"
        assert lat.startswith("5741.")
        assert lon.startswith("01158.")

    def test_formats_southern_western_position(self):
        """Negative latitude/longitude should use S/W hemispheres."""
        lat, lat_dir, lon, lon_dir = ntrip2keelson.format_lat_lon(
            -57.6890020,
            -11.9756379,
        )

        assert lat_dir == "S"
        assert lon_dir == "W"
        assert lat.startswith("5741.")
        assert lon.startswith("01158.")


@pytest.mark.unit
class TestGGAGeneration:
    """Test GGA generation used for NTRIP caster position feedback."""

    def test_make_gga_returns_valid_sentence_shape(self):
        """Generated GGA should be a complete NMEA sentence with checksum."""
        gga = ntrip2keelson.make_gga(
            57.6890020,
            11.9756379,
            62.24,
            "GP",
        )

        assert gga.startswith("$GPGGA,")
        assert gga.endswith("\r\n")
        assert "*" in gga

    def test_make_gga_checksum_matches_body(self):
        """Generated GGA checksum should match the generated body."""
        gga = ntrip2keelson.make_gga(
            57.6890020,
            11.9756379,
            62.24,
            "GP",
        )

        sentence = gga.strip()
        body, checksum = sentence[1:].split("*", maxsplit=1)

        assert checksum == ntrip2keelson.nmea_checksum(body)

    def test_make_gga_uses_fix_quality_one(self):
        """GGA sent to the caster should use a valid approximate fix."""
        gga = ntrip2keelson.make_gga(
            57.6890020,
            11.9756379,
            62.24,
            "GP",
        )

        fields = gga.strip()[1:].split("*", maxsplit=1)[0].split(",")

        assert fields[0] == "GPGGA"
        assert fields[6] == "1"


@pytest.mark.unit
class TestNTRIPRequest:
    """Test HTTP/NTRIP request generation."""

    def test_build_ntrip_v2_request_contains_auth_and_mountpoint(self):
        """NTRIP v2 request should include mountpoint and Basic auth."""
        args = SimpleNamespace(
            mountpoint="MSM_GNSS",
            username="user",
            caster_host="nrtk.example.test",
            caster_port=2101,
            user_agent="NTRIP ntrip2keelson/test",
            ntrip_version="2",
        )

        request = ntrip2keelson.build_ntrip_request(args, "pass").decode("ascii")
        expected_auth = base64.b64encode(b"user:pass").decode("ascii")

        assert request.startswith("GET /MSM_GNSS HTTP/1.1\r\n")
        assert "Host: nrtk.example.test:2101\r\n" in request
        assert f"Authorization: Basic {expected_auth}\r\n" in request
        assert "Ntrip-Version: Ntrip/2.0\r\n" in request
        assert request.endswith("\r\n\r\n")

    def test_build_ntrip_v1_request_omits_ntrip_version_header(self):
        """NTRIP v1 request should use HTTP/1.0 and omit Ntrip-Version."""
        args = SimpleNamespace(
            mountpoint="/MSM_GNSS",
            username="user",
            caster_host="nrtk.example.test",
            caster_port=2101,
            user_agent="NTRIP ntrip2keelson/test",
            ntrip_version="1",
        )

        request = ntrip2keelson.build_ntrip_request(args, "pass").decode("ascii")

        assert request.startswith("GET /MSM_GNSS HTTP/1.0\r\n")
        assert "Ntrip-Version:" not in request


@pytest.mark.unit
class TestNTRIPHeaderParsing:
    """Test NTRIP/HTTP response header parsing."""

    def test_accepts_icy_200_response(self):
        """NTRIP v1 style ICY 200 response should be accepted."""
        stream = io.BytesIO(b"ICY 200 OK\r\nServer: test\r\n\r\n")

        headers = ntrip2keelson.read_response_headers(stream)

        assert "ICY 200 OK" in headers

    def test_accepts_http_200_response(self):
        """HTTP 200 response should be accepted."""
        stream = io.BytesIO(b"HTTP/1.1 200 OK\r\nServer: test\r\n\r\n")

        headers = ntrip2keelson.read_response_headers(stream)

        assert "HTTP/1.1 200 OK" in headers

    def test_rejects_non_200_response(self):
        """Non-200 caster response should raise RuntimeError."""
        stream = io.BytesIO(
            b"HTTP/1.1 401 Unauthorized\r\n"
            b'WWW-Authenticate: Basic realm="NTRIP"\r\n'
            b"\r\n"
        )

        with pytest.raises(RuntimeError, match="rejected connection"):
            ntrip2keelson.read_response_headers(stream)


@pytest.mark.unit
class TestRTCMReaderFromNTRIPStream:
    """Test RTCM parsing from the downstream caster byte stream."""

    def test_reads_valid_rtcm_frame_from_stream(self):
        """RTCMReader should parse a valid RTCM frame from a stream."""
        stream = io.BytesIO(RTCM_1005_FRAME)
        reader = RTCMReader(stream)

        frames = list(reader)

        assert len(frames) == 1

        raw_data, parsed_data = frames[0]

        assert raw_data == RTCM_1005_FRAME
        assert parsed_data.identity == "1005"

    def test_reads_multiple_concatenated_rtcm_frames(self):
        """RTCMReader should parse multiple concatenated RTCM frames."""
        stream = io.BytesIO(RTCM_1005_FRAME * 3)
        reader = RTCMReader(stream)

        frames = list(reader)

        assert len(frames) == 3
        assert all(raw_data == RTCM_1005_FRAME for raw_data, _ in frames)


@pytest.mark.unit
class TestLatestFix:
    """Test LatestFix behavior."""

    def test_latest_fix_update_and_snapshot(self):
        """LatestFix should store and return position values."""
        fix = ntrip2keelson.LatestFix()

        fix.update(57.6890020, 11.9756379, 62.24, 123)

        assert fix.snapshot() == (57.6890020, 11.9756379, 62.24)
        assert fix.timestamp_ns == 123


@pytest.mark.unit
class TestLocationFixCallback:
    """Test location_fix callback behavior."""

    def test_invalid_latitude_is_ignored(self, monkeypatch):
        """Invalid latitude should not update the latest fix."""
        fake_fix = Mock()
        fake_fix.latitude = 91.0
        fake_fix.longitude = 11.0
        fake_fix.altitude = 10.0
        fake_fix.timestamp.seconds = 0
        fake_fix.timestamp.nanos = 0

        monkeypatch.setattr(
            ntrip2keelson.LocationFix,
            "FromString",
            Mock(return_value=fake_fix),
        )
        monkeypatch.setattr(
            ntrip2keelson.keelson,
            "uncover",
            Mock(return_value=(0, 0, b"payload")),
        )

        ntrip2keelson._latest_fix = ntrip2keelson.LatestFix()

        sample = Mock()
        sample.payload.to_bytes.return_value = b"envelope"

        ntrip2keelson.update_latest_fix_from_sample(sample)

        assert ntrip2keelson._latest_fix.latitude is None
        assert ntrip2keelson._latest_fix.longitude is None

    def test_valid_location_fix_updates_latest_fix(self, monkeypatch):
        """Valid location_fix sample should update the shared latest fix."""
        fake_fix = Mock()
        fake_fix.latitude = 57.6890020
        fake_fix.longitude = 11.9756379
        fake_fix.altitude = 62.24
        fake_fix.timestamp.seconds = 100
        fake_fix.timestamp.nanos = 5

        monkeypatch.setattr(
            ntrip2keelson.LocationFix,
            "FromString",
            Mock(return_value=fake_fix),
        )
        monkeypatch.setattr(
            ntrip2keelson.keelson,
            "uncover",
            Mock(return_value=(0, 0, b"payload")),
        )

        ntrip2keelson._latest_fix = ntrip2keelson.LatestFix()

        sample = Mock()
        sample.payload.to_bytes.return_value = b"envelope"

        ntrip2keelson.update_latest_fix_from_sample(sample)

        assert ntrip2keelson._latest_fix.latitude == pytest.approx(57.6890020)
        assert ntrip2keelson._latest_fix.longitude == pytest.approx(11.9756379)
        assert ntrip2keelson._latest_fix.altitude == pytest.approx(62.24)
        assert ntrip2keelson._latest_fix.timestamp_ns == 100_000_000_005
