"""MCU wire-format framing.

Defines the `Framing` ABC and a `PassthroughFraming` stub. Real
framing (protobuf-over-wire, COBS-framed structs, NMEA-like text, or
something else) lands in a follow-up PR once the MCU wire format is
decided. Keeping the abstraction in place from day one means the
follow-up is one new class + a CLI flag.
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class Framing(ABC):
    """Stateless codec between MCU wire bytes and discrete messages."""

    @abstractmethod
    def encode(self, message: bytes) -> bytes:
        """Wrap a message into wire bytes ready for ``Transport.write``."""

    @abstractmethod
    def decode(self, buffer: bytearray) -> list[bytes]:
        """Pull every complete message out of ``buffer``.

        ``buffer`` is mutated in place: consumed prefixes are removed,
        leaving only the trailing partial frame (if any) for the next
        read. Returns the list of messages decoded this call, possibly
        empty.
        """


class PassthroughFraming(Framing):
    """Stub: no framing at all.

    Every call to :meth:`decode` returns the accumulated buffer as one
    raw-bytes "message" and clears the buffer. Useful only as a
    placeholder until real framing lands — there is no message
    boundary detection, so partial reads will surface as oddly-sized
    messages.
    """

    def encode(self, message: bytes) -> bytes:
        return message

    def decode(self, buffer: bytearray) -> list[bytes]:
        if not buffer:
            return []
        data = bytes(buffer)
        buffer.clear()
        return [data]
