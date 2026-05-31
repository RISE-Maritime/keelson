"""Linux joystick HID event protocol — shared constants and parsing.

Used by bin/hc2keelson (the production connector) and
examples/joystick_reader.py (the standalone diagnostic reader).
"""

import struct

# Linux joystick event types (from linux/joystick.h)
JS_EVENT_BUTTON = 0x01  # Button pressed/released
JS_EVENT_AXIS = 0x02  # Joystick moved
JS_EVENT_INIT = 0x80  # Initial state flag (OR'd with the real type)

# 8-byte event format: timestamp (uint32), value (int16), type (uint8), number (uint8)
EVENT_FORMAT = "IhBB"
EVENT_SIZE = 8


def read_event(device_file):
    """Read a single 8-byte joystick event from device_file.

    Returns (timestamp, value, event_type, number), or None on a short read
    or read error. Callers can treat None uniformly as "no event this tick".
    """
    try:
        data = device_file.read(EVENT_SIZE)
    except OSError:
        return None
    if len(data) < EVENT_SIZE:
        return None
    return struct.unpack(EVENT_FORMAT, data)


def normalize_axis(value):
    """Normalize int16 axis value to percent (-100.0..100.0).

    Uses a 32768 divisor for symmetric mapping; clamps as defense in depth.
    """
    return max(-100.0, min(100.0, value * 100.0 / 32768.0))
