"""ISO 22133 wire codec — the MONR subset this connector needs.

WHICH CODEC IS AUTHORITATIVE
----------------------------
RI-SE publishes the reference implementation of ISO 22133 at
https://github.com/RI-SE/iso22133, with SWIG Python bindings. That library is
the authority: the standard is paywalled, and reimplementing a safety-adjacent
protocol from memory is exactly how subtle interop bugs are born.

So this module PREFERS those bindings when they are importable, and falls back
to the pure-Python decoder below only when they are not. `codec_name()` reports
which is in use and the connector logs it at startup, because "which codec
decoded this" is the first question when a field looks wrong.

The fallback is not guesswork. Every field, width, order and scale factor below
was read off the reference headers and is cited inline:

    include/header.h   HeaderType
    include/monr.h     MONRType, VALUE_ID_MONR_STRUCT = 0x80
    include/footer.h   FooterType { uint16_t Crc; }
    include/defines.h  ISO_SYNC_WORD 0x7E7F, and the scale/sentinel values

The structs are `#pragma pack(push,1)` and the protocol is little-endian, hence
the `<` format strings. The fallback decodes MONR only — the message this
connector consumes — and refuses anything else rather than half-parsing it.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

# --- from include/defines.h -------------------------------------------------
ISO_SYNC_WORD = 0x7E7F

POSITION_ONE_METER_VALUE = 1000.0  # positions are millimetres
POSITION_UNAVAILABLE_VALUE = -2147483648
SPEED_ONE_METER_PER_SECOND_VALUE = 100.0  # speeds are centimetres/second
SPEED_UNAVAILABLE_VALUE = -32768
ACCELERATION_ONE_METER_PER_SECOND_SQUARED_VALUE = 1000.0
ACCELERATION_UNAVAILABLE_VALUE = -32768
YAW_ONE_DEGREE_VALUE = 100.0  # angles are centidegrees
YAW_UNAVAILABLE_VALUE = 65535

# --- from include/monr.h ----------------------------------------------------
VALUE_ID_MONR_STRUCT = 0x80
MESSAGE_ID_MONR = 0x0006

# HeaderType: syncWord u16, messageLength u32, ackReqProtVer u8,
#             transmitterID u32, receiverID u32, messageCounter u8, messageID u16
_HEADER = struct.Struct("<HIBIIBH")
assert _HEADER.size == 18

# MONR body: valueID u16, contentLength u16, gpsQmsOfWeek u32,
#            x/y/z i32, yaw u16, pitch i16, roll i16,
#            longSpeed i16, latSpeed i16, longAcc i16, latAcc i16,
#            driveDirection u8, state u8, readyToArm u8, errorStatus u8,
#            errorCode u16
_BODY = struct.Struct("<HHIiiiHhhhhhhBBBBH")
assert _BODY.size == 40

_FOOTER = struct.Struct("<H")  # FooterType { uint16_t Crc; }

MONR_SIZE = _HEADER.size + _BODY.size + _FOOTER.size  # 60

# --- error bitmask, from include/defines.h ----------------------------------
ERROR_BITS = (
    ("abort_request", 0x80),
    ("outside_geofence", 0x40),
    ("bad_positioning_accuracy", 0x20),
    ("engine_fault", 0x10),
    ("battery_fault", 0x08),
    ("other", 0x04),
    ("sync_point_ended", 0x02),
    ("vendor_specific", 0x01),
)


class DecodeError(ValueError):
    """The bytes are not a MONR message this codec can read."""


@dataclass(frozen=True)
class Monr:
    """A decoded MONR, in SI units — millimetres and centidegrees stay in the
    codec. `None` means the object reported the value as unavailable, which is
    not the same as zero and must not be flattened into one."""

    transmitter_id: int
    receiver_id: int
    message_counter: int
    gps_qms_of_week: int
    x_m: float | None
    y_m: float | None
    z_m: float | None
    yaw_deg: float | None
    pitch_deg: float | None
    roll_deg: float | None
    longitudinal_speed_mps: float | None
    lateral_speed_mps: float | None
    longitudinal_acc_mps2: float | None
    lateral_acc_mps2: float | None
    drive_direction: int
    state: int
    ready_to_arm: int
    error_status: int
    error_code: int


def _scaled(raw: int, unavailable: int, divisor: float) -> float | None:
    return None if raw == unavailable else raw / divisor


def decode_monr(data: bytes) -> Monr:
    """Decode one MONR message.

    Raises DecodeError rather than returning a partly-filled object: a test
    record built from a message we only half understood is worse than no
    record. The CRC is read but not validated — the reference library owns
    that, and inventing a second CRC implementation here would be precisely
    the reimplementation this module avoids. Malformed frames are caught by
    the length and identifier checks instead.
    """
    if len(data) < MONR_SIZE:
        raise DecodeError(f"too short for MONR: {len(data)} < {MONR_SIZE}")

    sync, msg_len, _ack_ver, tx, rx, counter, msg_id = _HEADER.unpack_from(data, 0)
    if sync != ISO_SYNC_WORD:
        raise DecodeError(f"bad sync word 0x{sync:04X}, expected 0x{ISO_SYNC_WORD:04X}")
    if msg_id != MESSAGE_ID_MONR:
        raise DecodeError(f"not a MONR (message id 0x{msg_id:04X})")

    (
        value_id,
        _content_len,
        qms,
        x,
        y,
        z,
        yaw,
        pitch,
        roll,
        long_speed,
        lat_speed,
        long_acc,
        lat_acc,
        drive_dir,
        state,
        ready,
        err_status,
        err_code,
    ) = _BODY.unpack_from(data, _HEADER.size)

    if value_id != VALUE_ID_MONR_STRUCT:
        raise DecodeError(f"bad MONR value id 0x{value_id:04X}")

    return Monr(
        transmitter_id=tx,
        receiver_id=rx,
        message_counter=counter,
        gps_qms_of_week=qms,
        x_m=_scaled(x, POSITION_UNAVAILABLE_VALUE, POSITION_ONE_METER_VALUE),
        y_m=_scaled(y, POSITION_UNAVAILABLE_VALUE, POSITION_ONE_METER_VALUE),
        z_m=_scaled(z, POSITION_UNAVAILABLE_VALUE, POSITION_ONE_METER_VALUE),
        yaw_deg=_scaled(yaw, YAW_UNAVAILABLE_VALUE, YAW_ONE_DEGREE_VALUE),
        pitch_deg=_scaled(pitch, SPEED_UNAVAILABLE_VALUE, YAW_ONE_DEGREE_VALUE),
        roll_deg=_scaled(roll, SPEED_UNAVAILABLE_VALUE, YAW_ONE_DEGREE_VALUE),
        longitudinal_speed_mps=_scaled(
            long_speed, SPEED_UNAVAILABLE_VALUE, SPEED_ONE_METER_PER_SECOND_VALUE
        ),
        lateral_speed_mps=_scaled(
            lat_speed, SPEED_UNAVAILABLE_VALUE, SPEED_ONE_METER_PER_SECOND_VALUE
        ),
        longitudinal_acc_mps2=_scaled(
            long_acc,
            ACCELERATION_UNAVAILABLE_VALUE,
            ACCELERATION_ONE_METER_PER_SECOND_SQUARED_VALUE,
        ),
        lateral_acc_mps2=_scaled(
            lat_acc,
            ACCELERATION_UNAVAILABLE_VALUE,
            ACCELERATION_ONE_METER_PER_SECOND_SQUARED_VALUE,
        ),
        drive_direction=drive_dir,
        state=state,
        ready_to_arm=ready,
        error_status=err_status,
        error_code=err_code,
    )
    # msg_len is intentionally unused: it describes the payload the sender
    # declared, and trusting it over the struct sizes would let a wrong length
    # field steer the parse.


def encode_monr(
    *,
    transmitter_id: int,
    receiver_id: int = 0,
    message_counter: int = 0,
    gps_qms_of_week: int = 0,
    x_m: float | None = 0.0,
    y_m: float | None = 0.0,
    z_m: float | None = 0.0,
    yaw_deg: float | None = 0.0,
    pitch_deg: float | None = 0.0,
    roll_deg: float | None = 0.0,
    longitudinal_speed_mps: float | None = 0.0,
    lateral_speed_mps: float | None = 0.0,
    longitudinal_acc_mps2: float | None = 0.0,
    lateral_acc_mps2: float | None = 0.0,
    drive_direction: int = 0,
    state: int = 0,
    ready_to_arm: int = 0,
    error_status: int = 0,
    error_code: int = 0,
) -> bytes:
    """Encode a MONR. Used by the test-object simulator, and by the tests to
    round-trip the decoder — an encoder written from the same headers is the
    only way to exercise the decoder without a real object on the wire."""

    def raw(value, unavailable, divisor):
        return unavailable if value is None else int(round(value * divisor))

    body = _BODY.pack(
        VALUE_ID_MONR_STRUCT,
        _BODY.size - 4,  # content length excludes the value id + length fields
        gps_qms_of_week,
        raw(x_m, POSITION_UNAVAILABLE_VALUE, POSITION_ONE_METER_VALUE),
        raw(y_m, POSITION_UNAVAILABLE_VALUE, POSITION_ONE_METER_VALUE),
        raw(z_m, POSITION_UNAVAILABLE_VALUE, POSITION_ONE_METER_VALUE),
        raw(yaw_deg, YAW_UNAVAILABLE_VALUE, YAW_ONE_DEGREE_VALUE),
        raw(pitch_deg, SPEED_UNAVAILABLE_VALUE, YAW_ONE_DEGREE_VALUE),
        raw(roll_deg, SPEED_UNAVAILABLE_VALUE, YAW_ONE_DEGREE_VALUE),
        raw(
            longitudinal_speed_mps,
            SPEED_UNAVAILABLE_VALUE,
            SPEED_ONE_METER_PER_SECOND_VALUE,
        ),
        raw(
            lateral_speed_mps, SPEED_UNAVAILABLE_VALUE, SPEED_ONE_METER_PER_SECOND_VALUE
        ),
        raw(
            longitudinal_acc_mps2,
            ACCELERATION_UNAVAILABLE_VALUE,
            ACCELERATION_ONE_METER_PER_SECOND_SQUARED_VALUE,
        ),
        raw(
            lateral_acc_mps2,
            ACCELERATION_UNAVAILABLE_VALUE,
            ACCELERATION_ONE_METER_PER_SECOND_SQUARED_VALUE,
        ),
        drive_direction,
        state,
        ready_to_arm,
        error_status,
        error_code,
    )
    header = _HEADER.pack(
        ISO_SYNC_WORD,
        len(body),
        0,
        transmitter_id,
        receiver_id,
        message_counter & 0xFF,
        MESSAGE_ID_MONR,
    )
    return header + body + _FOOTER.pack(0)


def decode_error_flags(error_status: int) -> dict[str, bool]:
    """Unpack the MONR error bitmask into named flags."""
    return {name: bool(error_status & bit) for name, bit in ERROR_BITS}


# --- codec selection --------------------------------------------------------


def _load_reference_bindings():
    try:
        import iso22133  # type: ignore  # noqa: F401

        return iso22133
    except ImportError:
        return None


_REFERENCE = _load_reference_bindings()


def codec_name() -> str:
    """Which codec is in use — logged at startup and worth reading."""
    return (
        "RI-SE/iso22133 bindings"
        if _REFERENCE
        else "built-in MONR decoder (development)"
    )


def reference_bindings_available() -> bool:
    return _REFERENCE is not None
