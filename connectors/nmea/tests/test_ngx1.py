#!/usr/bin/env python3

"""Tests for ngx1 -- the Actisense NGX-1 BST-BEM protocol module."""

import asyncio

import pytest
from nmea2000.encoder import NMEA2000Encoder
from nmea2000.input_formats import N2KFormat
from nmea2000.message import NMEA2000Field, NMEA2000Message

# bin/ and its siblings are on sys.path via the connector conftest.
import n2k_gateway
import ngx1


# --------------------------------------------------------------------------
# BST-BEM framing
# --------------------------------------------------------------------------


def test_bst_checksum():
    # (256 - sum) % 256
    assert ngx1.bst_checksum(bytes([0xA1, 0x01, 0x11])) == (256 - 0xB3) % 256
    assert ngx1.bst_checksum(b"") == 0


def test_bst_frame_round_trip():
    frame = ngx1.bst_frame(ngx1.NGT_CMD_SEND, bytes([ngx1.ACMD_OPERATING_MODE]))
    assert frame[:2] == bytes([ngx1.DLE, ngx1.STX])
    assert frame[-2:] == bytes([ngx1.DLE, ngx1.ETX])
    command, payload = ngx1.bst_parse_frame(frame)
    assert command == ngx1.NGT_CMD_SEND
    assert payload == bytes([ngx1.ACMD_OPERATING_MODE])


def test_bst_frame_dle_stuffing_round_trips():
    """A payload containing 0x10 (DLE) survives stuff/unstuff."""
    payload = bytes([0x10, 0x10, 0x02, 0x03, 0x10])
    frame = ngx1.bst_frame(ngx1.N2K_MSG_SEND, payload)
    # Each DLE in the inner bytes is doubled.
    assert frame.count(bytes([ngx1.DLE, ngx1.DLE])) >= payload.count(ngx1.DLE)
    command, parsed = ngx1.bst_parse_frame(frame)
    assert command == ngx1.N2K_MSG_SEND
    assert parsed == payload


def test_bst_parse_frame_rejects_bad_checksum():
    frame = bytearray(ngx1.bst_frame(ngx1.NGT_CMD_SEND, bytes([0x11])))
    frame[4] ^= 0xFF  # corrupt the payload byte
    assert ngx1.bst_parse_frame(bytes(frame)) is None


def test_bst_parse_frame_rejects_bad_wrapper():
    assert ngx1.bst_parse_frame(b"not a frame") is None
    assert ngx1.bst_parse_frame(b"") is None


def test_extract_bst_frames_multiple():
    a = ngx1.bst_frame(ngx1.NGT_CMD_SEND, bytes([0x11]))
    b = ngx1.bst_frame(ngx1.N2K_MSG_SEND, bytes([1, 2, 3]))
    buffer = bytearray(a + b)
    frames = ngx1.extract_bst_frames(buffer)
    assert [cmd for cmd, _ in frames] == [ngx1.NGT_CMD_SEND, ngx1.N2K_MSG_SEND]
    assert len(buffer) == 0


def test_extract_bst_frames_keeps_partial_remainder():
    full = ngx1.bst_frame(ngx1.NGT_CMD_SEND, bytes([0x11]))
    buffer = bytearray(full + full[:4])  # one full frame + a partial one
    frames = ngx1.extract_bst_frames(buffer)
    assert len(frames) == 1
    assert bytes(buffer) == full[:4]  # partial frame retained for next read


def test_extract_bst_frames_drops_leading_junk():
    full = ngx1.bst_frame(ngx1.NGT_CMD_SEND, bytes([0x11]))
    buffer = bytearray(b"\x00\xff\x99" + full)
    frames = ngx1.extract_bst_frames(buffer)
    assert len(frames) == 1
    assert len(buffer) == 0


# --------------------------------------------------------------------------
# N2K payloads (0x93 / 0x94)
# --------------------------------------------------------------------------


def test_decode_n2k_recv():
    payload = bytes([2, 0x01, 0xF8, 0x01, 255, 22, 0, 0, 0, 0, 8]) + bytes(range(8))
    recv = ngx1.decode_n2k_recv(payload)
    assert recv is not None
    assert recv.priority == 2
    assert recv.pgn == 129025  # 0x01F801, little-endian
    assert recv.destination == 255
    assert recv.source == 22
    assert recv.data == bytes(range(8))


def test_decode_n2k_recv_too_short():
    assert ngx1.decode_n2k_recv(bytes(5)) is None


def test_encode_n2k_send_layout():
    """0x94 payload is priority, PGN-LE, dest, len, data -- no source byte."""
    data = bytes(range(8))
    frame = ngx1.encode_n2k_send(priority=2, pgn=129025, destination=255, data=data)
    command, payload = ngx1.bst_parse_frame(frame)
    assert command == ngx1.N2K_MSG_SEND
    assert payload[0] == 2  # priority
    assert payload[1:4] == bytes([0x01, 0xF8, 0x01])  # PGN little-endian
    assert payload[4] == 255  # destination
    assert payload[5] == 8  # data length
    assert payload[6:] == data
    # 6-byte header + 8 data bytes, no source byte.
    assert len(payload) == 14


def test_n2k_send_recv_pgn_round_trip():
    """A PGN survives 0x94 encode then 0x93-style decode of the same fields."""
    frame = ngx1.encode_n2k_send(priority=3, pgn=130306, destination=255, data=bytes(6))
    _, payload = ngx1.bst_parse_frame(frame)
    pgn = payload[1] | (payload[2] << 8) | (payload[3] << 16)
    assert pgn == 130306


# --------------------------------------------------------------------------
# NGT control frames
# --------------------------------------------------------------------------


def test_build_control_frames():
    for builder in (
        ngx1.build_get_operating_mode,
        ngx1.build_commit_eeprom,
    ):
        command, payload = ngx1.bst_parse_frame(builder())
        assert command == ngx1.NGT_CMD_SEND

    _, set_mode = ngx1.bst_parse_frame(
        ngx1.build_set_operating_mode(ngx1.MODE_TRANSFER_RX_ALL)
    )
    assert set_mode[0] == ngx1.ACMD_OPERATING_MODE
    assert set_mode[1] == ngx1.MODE_TRANSFER_RX_ALL


def test_build_set_baud():
    _, payload = ngx1.bst_parse_frame(ngx1.build_set_baud(115200))
    assert payload[0] == ngx1.ACMD_PORT_BAUD_CFG
    assert payload[1] == ngx1.BAUD_CODES[115200]


def test_build_set_baud_rejects_unknown_rate():
    with pytest.raises(ValueError, match="Unsupported NGX-1 baud rate"):
        ngx1.build_set_baud(9999)


# --------------------------------------------------------------------------
# Operating-mode response parsing
# --------------------------------------------------------------------------


def test_parse_operating_mode_response():
    # payload[0]=sub-cmd, [2]=model id, [12]=mode byte
    payload = bytes([0x11, 0x01, 59] + [0] * 9 + [0x04, 0x00])
    info = ngx1.parse_operating_mode_response(payload)
    assert info is not None
    assert info.model_id == 59
    assert info.model_name == "NGX-1"
    assert info.mode == 0x04
    assert info.is_transfer_rx_all is False


def test_parse_operating_mode_response_transfer_rx_all():
    payload = bytes([0x11, 0x01, 59] + [0] * 9 + [ngx1.MODE_TRANSFER_RX_ALL, 0x00])
    info = ngx1.parse_operating_mode_response(payload)
    assert info.is_transfer_rx_all is True


def test_parse_operating_mode_response_wrong_subcommand():
    assert ngx1.parse_operating_mode_response(bytes([0x12, 0x00])) is None
    assert ngx1.parse_operating_mode_response(b"") is None


# --------------------------------------------------------------------------
# ensure_transfer_mode
# --------------------------------------------------------------------------


class FakeNgxSerial:
    """A fake serial.Serial that emulates an NGX-1 over BST-BEM.

    Answers GetOperatingMode with its current mode, and applies
    SetOperatingMode. ``responsive=False`` simulates a silent device.
    """

    def __init__(self, mode: int = 0x04, model_id: int = 59, responsive: bool = True):
        self.mode = mode
        self.model_id = model_id
        self.responsive = responsive
        self._rx = bytearray()
        self.written = bytearray()
        self.dtr = False
        self.rts = False
        self.closed = False

    @property
    def in_waiting(self) -> int:
        return len(self._rx)

    def read(self, count: int) -> bytes:
        chunk = bytes(self._rx[:count])
        del self._rx[:count]
        return chunk

    def write(self, data: bytes) -> None:
        self.written.extend(data)
        if not self.responsive:
            return
        for command, payload in ngx1.extract_bst_frames(bytearray(data)):
            if command != ngx1.NGT_CMD_SEND or not payload:
                continue
            if payload[0] == ngx1.ACMD_OPERATING_MODE and len(payload) == 1:
                response = (
                    bytes([ngx1.ACMD_OPERATING_MODE, 0x01, self.model_id])
                    + bytes(9)
                    + bytes([self.mode, 0x00])
                )
                self._rx.extend(ngx1.bst_frame(ngx1.NGT_CMD_RECV, response))
            elif payload[0] == ngx1.ACMD_OPERATING_MODE:
                self.mode = payload[1]

    def flush(self) -> None:
        pass

    def reset_input_buffer(self) -> None:
        self._rx.clear()

    def close(self) -> None:
        self.closed = True


def test_ensure_transfer_mode_already_correct(monkeypatch):
    fake = FakeNgxSerial(mode=ngx1.MODE_TRANSFER_RX_ALL)
    monkeypatch.setattr(ngx1.serial, "Serial", lambda *a, **k: fake)
    result = ngx1.ensure_transfer_mode("/dev/ttyUSB0", target_baud=115200)
    assert result.success is True
    assert result.reconfigured is False
    assert result.model_name == "NGX-1"


def test_ensure_transfer_mode_switches_from_convert(monkeypatch):
    fake = FakeNgxSerial(mode=0x04)  # factory Convert mode
    monkeypatch.setattr(ngx1.serial, "Serial", lambda *a, **k: fake)
    result = ngx1.ensure_transfer_mode("/dev/ttyUSB0", target_baud=115200)
    assert result.success is True
    assert result.reconfigured is True
    assert fake.mode == ngx1.MODE_TRANSFER_RX_ALL  # the device was switched


def test_ensure_transfer_mode_no_device(monkeypatch):
    fake = FakeNgxSerial(responsive=False)
    monkeypatch.setattr(ngx1.serial, "Serial", lambda *a, **k: fake)
    result = ngx1.ensure_transfer_mode("/dev/ttyUSB0")
    assert result.success is False


def test_ensure_transfer_mode_rejects_bad_baud():
    with pytest.raises(ValueError, match="Unsupported NGX-1 target baud"):
        ngx1.ensure_transfer_mode("/dev/ttyUSB0", target_baud=9999)


# --------------------------------------------------------------------------
# Ngx1BstGateway
# --------------------------------------------------------------------------


class FakeReader:
    """A minimal asyncio StreamReader stand-in serving canned chunks."""

    def __init__(self, chunks):
        self._chunks = list(chunks)

    async def read(self, count: int) -> bytes:
        return self._chunks.pop(0) if self._chunks else b""


def _position_message(source: int = 22) -> NMEA2000Message:
    msg = NMEA2000Message(
        PGN=129025,
        id="positionRapidUpdate",
        source=source,
        destination=255,
        priority=2,
    )
    msg.fields = [
        NMEA2000Field(id="latitude", value=59.5, unit_of_measurement="deg"),
        NMEA2000Field(id="longitude", value=18.25, unit_of_measurement="deg"),
    ]
    return msg


def test_ngx1_gateway_encode_produces_0x94():
    """_encode_impl turns a message into a 0x94 BST send frame."""

    async def run():
        gateway = ngx1.Ngx1BstGateway("/dev/ttyUSB0")
        frames = gateway._encode_impl(_position_message())
        command, payload = ngx1.bst_parse_frame(frames[0])
        assert command == ngx1.N2K_MSG_SEND
        pgn = payload[1] | (payload[2] << 8) | (payload[3] << 16)
        assert pgn == 129025
        await gateway.close()

    asyncio.run(run())


def test_ngx1_gateway_receive_decodes_0x93():
    """_receive_impl decodes a 0x93 frame onto the message queue."""

    async def run():
        gateway = ngx1.Ngx1BstGateway("/dev/ttyUSB0")
        # Raw N2K data bytes for a 129025 message, via the library codec.
        encoded = NMEA2000Encoder().encode(
            _position_message(), output_format=N2KFormat.BASIC_STRING
        )
        _, _, _, data = ngx1._parse_basic_string(encoded)
        payload = bytes([2, 0x01, 0xF8, 0x01, 255, 22, 0, 0, 0, 0, len(data)]) + data
        frame = ngx1.bst_frame(ngx1.N2K_MSG_RECV, payload)

        gateway.reader = FakeReader([frame])
        await gateway._receive_impl()
        received = gateway.queue.get_nowait()
        assert received.PGN == 129025
        await gateway.close()

    asyncio.run(run())


# --------------------------------------------------------------------------
# actisense_ngx1 gateway profile
# --------------------------------------------------------------------------


def test_actisense_ngx1_profile():
    profile = n2k_gateway.GATEWAY_PROFILES["actisense_ngx1"]
    assert profile.transport == "usb"
    assert profile.polite_node is True
    assert profile.manufacturer_code == 273  # Actisense
    assert profile.preflight is not None


def test_create_gateway_ngx1_builds_bst_gateway():
    async def run():
        client = n2k_gateway.create_gateway(
            "actisense_ngx1", device="/dev/ttyUSB0", baud=115200
        )
        assert isinstance(client, ngx1.Ngx1BstGateway)
        assert client.baud == 115200
        await client.close()

    asyncio.run(run())
