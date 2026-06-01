"""Unit tests for rorkult.framing.

PassthroughFraming is the v1 stub; tests are mostly sanity checks that
the abstraction does what the docstring says and behaves correctly on
the empty buffer.
"""

from rorkult.framing import PassthroughFraming


def test_passthrough_encode_is_identity():
    f = PassthroughFraming()
    assert f.encode(b"hello") == b"hello"
    assert f.encode(b"") == b""


def test_passthrough_decode_drains_buffer():
    f = PassthroughFraming()
    buf = bytearray(b"abc")
    out = f.decode(buf)
    assert out == [b"abc"]
    assert buf == bytearray()


def test_passthrough_decode_empty_buffer_returns_no_messages():
    f = PassthroughFraming()
    buf = bytearray()
    assert f.decode(buf) == []


def test_passthrough_decode_back_to_back():
    f = PassthroughFraming()
    buf = bytearray()
    buf.extend(b"first")
    assert f.decode(buf) == [b"first"]
    assert buf == bytearray()
    buf.extend(b"second")
    assert f.decode(buf) == [b"second"]
    assert buf == bytearray()
