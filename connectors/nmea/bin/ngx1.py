#!/usr/bin/env python3

"""Actisense NGX-1 BST-BEM protocol — framing, control and N2K codec.

The NGX-1-USB ships in NMEA 0183 Convert mode and must be switched to
"Transfer Receive All" mode to act as an NMEA 2000 gateway. This module
implements the Actisense BST-BEM binary protocol: the DLE-stuffed framing, the
NGT control commands used to probe and switch the operating mode, and the
``0x93`` (receive) / ``0x94`` (send) N2K message payloads.

This is *not* an entry point; it is imported as a sibling by ``n2k_gateway``.

Ported from a hardware-verified reference implementation. Protocol references:
OpenSkipper (Pack_Startup), aldas/go-nmea-client, canboat ``actisense-serial.c``.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Optional

import serial
import serial_asyncio

from nmea2000.input_formats import N2KFormat
from nmea2000.ioclient import AsyncIOClient

# --------------------------------------------------------------------------
# Constants
# --------------------------------------------------------------------------

# BST-BEM framing bytes.
DLE = 0x10
STX = 0x02
ETX = 0x03

# Command bytes.
NGT_CMD_SEND = 0xA1  # host -> device  (NGT control command)
NGT_CMD_RECV = 0xA0  # device -> host  (NGT control response)
N2K_MSG_RECV = 0x93  # device -> host  (N2K message received from the bus)
N2K_MSG_SEND = 0x94  # host -> device  (N2K message to transmit to the bus)

# NGT sub-commands (the first payload byte of an NGT control command).
ACMD_COMMIT_EEPROM = 0x01
ACMD_OPERATING_MODE = 0x11
ACMD_PORT_BAUD_CFG = 0x12

# Operating modes (the codes the SET command accepts).
MODE_CONVERT = 0x00
MODE_TRANSFER_NORMAL = 0x01
MODE_TRANSFER_RX_ALL = 0x02

MODE_NAMES = {
    0x00: "Convert (alt code)",
    0x01: "Transfer Normal",
    0x02: "Transfer Receive All",
    0x04: "Convert (NMEA 0183 <-> NMEA 2000)",
}

# ARL model IDs (ACMD_OPERATING_MODE response, payload[2]).
MODEL_NAMES = {
    14: "NGT-1",
    27: "NGT-1 (Nobeltec OEM)",
    59: "NGX-1",
    61: "WGX-1",
}

# Baud-rate codes for ACMD_PORT_BAUD_CFG.
BAUD_CODES = {4800: 0x03, 38400: 0x05, 57600: 0x06, 115200: 0x07, 230400: 0x08}


# --------------------------------------------------------------------------
# BST-BEM framing
# --------------------------------------------------------------------------


def bst_checksum(data: bytes) -> int:
    """BST-BEM checksum: ``(256 - sum(bytes)) % 256``."""
    return (256 - (sum(data) % 256)) % 256


def bst_frame(command: int, payload: bytes) -> bytes:
    """Build a DLE-stuffed BST-BEM frame.

    Layout: ``DLE STX <CMD> <LEN> <PAYLOAD...> <CRC> DLE ETX``. Every ``0x10``
    (DLE) byte in CMD/LEN/PAYLOAD/CRC is escaped as ``0x10 0x10``.
    """
    inner = bytes([command, len(payload)]) + bytes(payload)
    inner += bytes([bst_checksum(inner)])

    stuffed = bytearray()
    for byte in inner:
        stuffed.append(byte)
        if byte == DLE:
            stuffed.append(DLE)
    return bytes([DLE, STX]) + bytes(stuffed) + bytes([DLE, ETX])


def bst_parse_frame(raw: bytes) -> Optional[tuple[int, bytes]]:
    """Parse one ``DLE STX ... DLE ETX`` frame.

    Returns ``(command, payload)``, or ``None`` if the wrapper, length or
    checksum is invalid.
    """
    if len(raw) < 5 or raw[0] != DLE or raw[1] != STX:
        return None
    if raw[-2] != DLE or raw[-1] != ETX:
        return None

    inner = raw[2:-2]
    unstuffed = bytearray()
    i = 0
    while i < len(inner):
        byte = inner[i]
        if byte == DLE and i + 1 < len(inner) and inner[i + 1] == DLE:
            unstuffed.append(DLE)
            i += 2
        else:
            unstuffed.append(byte)
            i += 1

    if len(unstuffed) < 3:
        return None
    command, length = unstuffed[0], unstuffed[1]
    if len(unstuffed) <= 2 + length:
        return None
    payload = bytes(unstuffed[2 : 2 + length])
    crc = unstuffed[2 + length]
    if crc != bst_checksum(bytes([command, length]) + payload):
        return None
    return command, payload


def extract_bst_frames(buffer: bytearray) -> list[tuple[int, bytes]]:
    """Pull every complete BST frame out of ``buffer``.

    ``buffer`` is mutated in place: consumed frames (and any leading junk) are
    removed, leaving only a trailing partial frame for the next read.
    """
    frames: list[tuple[int, bytes]] = []
    while True:
        start = buffer.find(bytes([DLE, STX]))
        if start < 0:
            # No frame start; keep only a trailing DLE that may begin one.
            del buffer[: max(0, len(buffer) - 1)]
            break

        end = -1
        j = start + 2
        while j < len(buffer) - 1:
            if buffer[j] == DLE:
                if buffer[j + 1] == ETX:
                    end = j + 2
                    break
                if buffer[j + 1] == DLE:
                    j += 2  # escaped DLE
                    continue
            j += 1

        if end < 0:
            del buffer[:start]  # drop junk before the (incomplete) frame
            break

        parsed = bst_parse_frame(bytes(buffer[start:end]))
        del buffer[:end]
        if parsed is not None:
            frames.append(parsed)
    return frames


# --------------------------------------------------------------------------
# N2K message payloads (0x93 receive / 0x94 send)
# --------------------------------------------------------------------------


@dataclass
class N2kRecv:
    """A decoded ``0x93`` N2K_MSG_RECV payload."""

    priority: int
    pgn: int
    source: int
    destination: int
    data: bytes


def decode_n2k_recv(payload: bytes) -> Optional[N2kRecv]:
    """Decode a ``0x93`` N2K_MSG_RECV payload.

    Layout: ``priority(1) PGN(3 LE) destination(1) source(1) timestamp(4)
    data_len(1) data(...)``.
    """
    if len(payload) < 11:
        return None
    priority = payload[0]
    pgn = payload[1] | (payload[2] << 8) | (payload[3] << 16)
    destination = payload[4]
    source = payload[5]
    # payload[6:10] is the device timestamp -- not used.
    data_len = payload[10]
    data = bytes(payload[11 : 11 + data_len])
    return N2kRecv(priority, pgn, source, destination, data)


def encode_n2k_send(priority: int, pgn: int, destination: int, data: bytes) -> bytes:
    """Build a ``0x94`` N2K_MSG_SEND BST frame.

    Payload layout (per canboat ``actisense-serial.c``): ``priority(1)
    PGN(3 LE) destination(1) data_len(1) data(...)``. NOTE: there is
    deliberately **no source byte** -- the device fills in its own claimed
    address, and including a source byte makes the device silently drop the
    frame.
    """
    payload = bytes(
        [
            priority & 0xFF,
            pgn & 0xFF,
            (pgn >> 8) & 0xFF,
            (pgn >> 16) & 0xFF,
            destination & 0xFF,
            len(data),
        ]
    ) + bytes(data)
    return bst_frame(N2K_MSG_SEND, payload)


# --------------------------------------------------------------------------
# NGT control frames
# --------------------------------------------------------------------------


def build_get_operating_mode() -> bytes:
    """BST frame querying the current operating mode."""
    return bst_frame(NGT_CMD_SEND, bytes([ACMD_OPERATING_MODE]))


def build_set_operating_mode(mode: int) -> bytes:
    """BST frame setting the operating mode."""
    return bst_frame(NGT_CMD_SEND, bytes([ACMD_OPERATING_MODE, mode, 0x00]))


def build_set_baud(baud: int) -> bytes:
    """BST frame setting the device serial baud rate.

    Raises:
        ValueError: the baud rate has no known device code.
    """
    code = BAUD_CODES.get(baud)
    if code is None:
        valid = ", ".join(str(b) for b in sorted(BAUD_CODES))
        raise ValueError(f"Unsupported NGX-1 baud rate {baud}; valid: {valid}.")
    return bst_frame(NGT_CMD_SEND, bytes([ACMD_PORT_BAUD_CFG, code]))


def build_commit_eeprom() -> bytes:
    """BST frame committing the current settings to EEPROM (persistent)."""
    return bst_frame(NGT_CMD_SEND, bytes([ACMD_COMMIT_EEPROM]))


@dataclass
class OperatingModeInfo:
    """A parsed response to a GetOperatingMode query."""

    mode: Optional[int] = None
    model_id: Optional[int] = None
    raw: bytes = field(default=b"")

    @property
    def mode_name(self) -> str:
        if self.mode is None:
            return "unknown"
        return MODE_NAMES.get(self.mode, f"unknown (0x{self.mode:02X})")

    @property
    def model_name(self) -> str:
        if self.model_id is None:
            return "unknown"
        return MODEL_NAMES.get(self.model_id, f"unknown (id={self.model_id})")

    @property
    def is_transfer_rx_all(self) -> bool:
        return self.mode == MODE_TRANSFER_RX_ALL


def parse_operating_mode_response(payload: bytes) -> Optional[OperatingModeInfo]:
    """Parse an NGT operating-mode response payload.

    The current mode byte is at ``payload[12]`` on NGX-1 firmware -- ``payload[1]``
    is a constant ``0x01`` unrelated to the mode, a known trap from NGT-1-era
    references.
    """
    if len(payload) < 1 or payload[0] != ACMD_OPERATING_MODE:
        return None
    info = OperatingModeInfo(raw=bytes(payload))
    if len(payload) >= 3:
        info.model_id = payload[2]
    if len(payload) >= 13:
        info.mode = payload[12]
    return info


# --------------------------------------------------------------------------
# Connect-time control: ensure Transfer Receive All mode
# --------------------------------------------------------------------------

logger = logging.getLogger("ngx1")

DEFAULT_BAUD = 115200
_BAUD_SCAN_ORDER = (115200, 230400, 4800, 38400, 57600)


@dataclass
class EnsureResult:
    """Outcome of an NGX-1 ensure-transfer-mode pre-flight."""

    success: bool
    baud: Optional[int] = None
    model_name: str = "unknown"
    mode_name: str = "unknown"
    reconfigured: bool = False


def _open_serial(device: str, baud: int) -> serial.Serial:
    """Open a serial port for synchronous BST-BEM control I/O."""
    port = serial.Serial(
        device,
        baudrate=baud,
        bytesize=serial.EIGHTBITS,
        parity=serial.PARITY_NONE,
        stopbits=serial.STOPBITS_ONE,
        timeout=0.5,
        write_timeout=1.0,
    )
    port.dtr = True
    port.rts = True
    return port


def _read_bst_frames(port: serial.Serial, timeout: float) -> list[tuple[int, bytes]]:
    """Read BST frames from a synchronous serial port for up to `timeout` s."""
    buffer = bytearray()
    frames: list[tuple[int, bytes]] = []
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        waiting = port.in_waiting
        if waiting:
            buffer.extend(port.read(waiting))
            frames.extend(extract_bst_frames(buffer))
        else:
            time.sleep(0.01)
    return frames


def _query_operating_mode(port: serial.Serial) -> Optional[OperatingModeInfo]:
    """Send GetOperatingMode and return the parsed response, if any."""
    port.reset_input_buffer()
    port.write(build_get_operating_mode())
    port.flush()
    for command, payload in _read_bst_frames(port, timeout=0.8):
        if command == NGT_CMD_RECV:
            info = parse_operating_mode_response(payload)
            if info is not None:
                return info
    return None


def ensure_transfer_mode(
    device: str,
    *,
    target_baud: int = DEFAULT_BAUD,
    preferred_baud: Optional[int] = None,
    persist: bool = False,
) -> EnsureResult:
    """Probe an NGX-1 and switch it into Transfer Receive All mode if needed.

    Scans baud rates for a BST-BEM response, reads the operating mode, and --
    only on a mismatch -- sets Transfer Receive All mode and ``target_baud``.
    Idempotent, so it is safe to run on every connect. With ``persist`` a
    change is also committed to the device EEPROM.

    Raises:
        ValueError: ``target_baud`` is not a baud rate the NGX-1 supports.
    """
    if target_baud not in BAUD_CODES:
        raise ValueError(f"Unsupported NGX-1 target baud rate: {target_baud}")

    scan = list(_BAUD_SCAN_ORDER)
    if preferred_baud:
        scan = [preferred_baud] + [b for b in scan if b != preferred_baud]

    port: Optional[serial.Serial] = None
    info: Optional[OperatingModeInfo] = None
    baud: Optional[int] = None
    for candidate in scan:
        try:
            port = _open_serial(device, candidate)
        except serial.SerialException as exc:
            logger.error("Cannot open NGX-1 serial port %s: %s", device, exc)
            return EnsureResult(success=False)
        info = _query_operating_mode(port)
        if info is not None:
            baud = candidate
            break
        port.close()
        port = None

    if info is None or port is None:
        logger.error("No BST-BEM response from an NGX-1 on %s at any baud", device)
        if port is not None:
            port.close()
        return EnsureResult(success=False)

    logger.info(
        "NGX-1 found on %s: model=%s, mode=%s, baud=%d",
        device,
        info.model_name,
        info.mode_name,
        baud,
    )

    mode_name = info.mode_name
    reconfigured = False
    try:
        if not info.is_transfer_rx_all:
            logger.info("Switching NGX-1 to Transfer Receive All mode")
            port.write(build_set_operating_mode(MODE_TRANSFER_RX_ALL))
            port.flush()
            _read_bst_frames(port, timeout=1.0)
            reconfigured = True

        if baud != target_baud:
            logger.info("Switching NGX-1 baud rate %d -> %d", baud, target_baud)
            port.write(build_set_baud(target_baud))
            port.flush()
            time.sleep(0.3)
            port.close()
            port = _open_serial(device, target_baud)
            baud = target_baud
            reconfigured = True

        if persist and reconfigured:
            logger.info("Committing NGX-1 configuration to EEPROM")
            port.write(build_commit_eeprom())
            port.flush()
            _read_bst_frames(port, timeout=1.0)

        verify = _query_operating_mode(port)
        if verify is not None:
            mode_name = verify.mode_name
            if not verify.is_transfer_rx_all:
                logger.warning(
                    "NGX-1 reports mode %s after reconfigure (expected "
                    "Transfer Receive All)",
                    verify.mode_name,
                )
    finally:
        port.close()

    return EnsureResult(
        success=True,
        baud=baud,
        model_name=info.model_name,
        mode_name=mode_name,
        reconfigured=reconfigured,
    )


# --------------------------------------------------------------------------
# Ngx1BstGateway -- the NGX-1 data gateway
# --------------------------------------------------------------------------


def _basic_string_line(recv: N2kRecv) -> str:
    """Format a received N2K message as a canboat BASIC_STRING text line."""
    timestamp = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime())
    data_hex = ",".join(f"{b:02x}" for b in recv.data)
    return (
        f"{timestamp},{recv.priority},{recv.pgn},{recv.source},"
        f"{recv.destination},{len(recv.data)},{data_hex}"
    )


def _parse_basic_string(line: str) -> tuple[int, int, int, bytes]:
    """Parse a canboat BASIC_STRING line -> (priority, pgn, destination, data)."""
    parts = line.strip().split(",")
    priority = int(parts[1])
    pgn = int(parts[2])
    destination = int(parts[4])
    data = bytes(int(part, 16) for part in parts[6:])
    return priority, pgn, destination, data


class Ngx1BstGateway(AsyncIOClient):
    """An Actisense NGX-1 gateway speaking BST ``0x93`` receive / ``0x94`` send.

    Talks to the NGX-1 over its USB serial port; the device must already be in
    Transfer Receive All mode (see :func:`ensure_transfer_mode`, which the
    connector runs as a connect-time pre-flight).

    Inbound ``0x93`` frames and outbound messages are bridged to the nmea2000
    library codec through the canboat BASIC_STRING text format, so every PGN
    the library knows is handled without per-PGN hand-coding.
    """

    def __init__(
        self,
        device: str,
        baud: int = DEFAULT_BAUD,
        exclude_pgns: Optional[list] = None,
        include_pgns: Optional[list] = None,
        exclude_manufacturer_code: Optional[list] = None,
        include_manufacturer_code: Optional[list] = None,
        preferred_units: Optional[dict] = None,
        dump_to_file: Optional[str] = None,
        dump_pgns: Optional[list] = None,
        build_network_map: bool = False,
    ):
        super().__init__(
            exclude_pgns=exclude_pgns or [],
            include_pgns=include_pgns or [],
            exclude_manufacturer_code=exclude_manufacturer_code or [],
            include_manufacturer_code=include_manufacturer_code or [],
            preferred_units=preferred_units or {},
            dump_to_file=dump_to_file,
            dump_pgns=dump_pgns or [],
            build_network_map=build_network_map,
            seed_network_map=False,
            bound_format=None,
        )
        self.device = device
        self.baud = baud
        self._buffer = bytearray()

    async def _connect_impl(self):
        self.logger.info(
            "Opening NGX-1 serial port %s @ %d baud", self.device, self.baud
        )
        self.reader, self.writer = await serial_asyncio.open_serial_connection(
            url=self.device,
            baudrate=self.baud,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
        )
        self._buffer = bytearray()
        self.logger.info("Connected to NGX-1 on %s", self.device)

    async def _receive_impl(self):
        data = await self.reader.read(4096)
        if not data:
            raise ConnectionError("NGX-1 serial connection closed")
        self._buffer.extend(data)
        for command, payload in extract_bst_frames(self._buffer):
            if command != N2K_MSG_RECV:
                continue  # NGT control responses etc. -- not bus data
            recv = decode_n2k_recv(payload)
            if recv is None:
                continue
            try:
                message = self.decoder.decode(_basic_string_line(recv))
            except Exception as exc:
                self.logger.warning("NGX-1: failed to decode PGN %s: %s", recv.pgn, exc)
                continue
            if message is not None:
                await self.queue.put(message)

    def _encode_impl(self, nmea2000Message) -> list[bytes]:
        encoded = self.encoder.encode(
            nmea2000Message, output_format=N2KFormat.BASIC_STRING
        )
        line = encoded[0] if isinstance(encoded, list) else encoded
        if isinstance(line, (bytes, bytearray)):
            line = line.decode("ascii", errors="ignore")
        priority, pgn, destination, data = _parse_basic_string(line)
        return [encode_n2k_send(priority, pgn, destination, data)]
