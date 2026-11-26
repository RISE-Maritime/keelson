"""Tests for the zenoh-cli codecs in keelson.codec.

These are what porla pipelines use to put lines onto, and read them off, the
keelson bus (`zenoh put --encoder keelson-enclose-from-text ...`), so they are
a contract with code outside this repo.
"""

import json
import time
from base64 import b64encode
from importlib.metadata import entry_points

import pytest

import keelson
from keelson import codec
from keelson.payloads.Primitives_pb2 import TimestampedBytes, TimestampedString

pytestmark = pytest.mark.unit


def key(subject: str) -> str:
    return keelson.construct_pubsub_key(
        base_path="rise", entity_id="vessel", subject=subject, source_id="porla/0"
    )


SENTENCE = "$GPGGA,123519,4807.038,N,01131.000,E,1,08,0.9,545.4,M,46.9,M,,*47"
BINARY = bytes(range(256))


@pytest.mark.parametrize(
    "subject", ["raw", "raw_lwe450", "raw_nmea0183", "raw_json", "name"]
)
def test_text_round_trip(subject):
    envelope = codec.enclose_from_text(key(subject), SENTENCE)
    assert codec.uncover_to_text(key(subject), envelope) == SENTENCE


@pytest.mark.parametrize("terminator", ["\n", "\r\n"])
def test_text_drops_the_line_terminator(terminator):
    """zenoh-cli passes stdin lines with their newline; connectors strip it."""
    envelope = codec.enclose_from_text(key("raw_nmea0183"), SENTENCE + terminator)
    assert codec.uncover_to_text(key("raw_nmea0183"), envelope) == SENTENCE


@pytest.mark.parametrize(
    "subject, payload_type",
    [("raw_nmea0183", TimestampedString), ("raw_lwe450", TimestampedBytes)],
)
def test_text_encodes_the_subjects_own_payload_type(subject, payload_type):
    """A consumer decoding by the subject registry must get what it expects."""
    before = time.time_ns()
    envelope = codec.enclose_from_text(key(subject), SENTENCE)
    after = time.time_ns()

    _, _, payload = keelson.uncover(envelope)
    decoded = keelson.decode_protobuf_payload_from_type_name(
        payload, keelson.get_subject_schema(subject)
    )

    # The registry builds its classes from the descriptor pool, so compare
    # the type by name rather than with isinstance.
    assert decoded.DESCRIPTOR.full_name == payload_type.DESCRIPTOR.full_name
    assert decoded.value in (SENTENCE, SENTENCE.encode())
    assert before <= decoded.timestamp.ToNanoseconds() <= after


@pytest.mark.parametrize("subject", ["raw", "raw_nmea2000", "raw_rtcm_v3"])
def test_base64_round_trip_is_lossless_for_binary(subject):
    encoded = b64encode(BINARY).decode()
    envelope = codec.enclose_from_base64(key(subject), encoded)
    assert codec.uncover_to_base64(key(subject), envelope) == encoded


def test_text_cannot_decode_binary_payload():
    envelope = codec.enclose_from_base64(
        key("raw_rtcm_v3"), b64encode(b"\xd3\x00\xff").decode()
    )
    with pytest.raises(UnicodeDecodeError):
        codec.uncover_to_text(key("raw_rtcm_v3"), envelope)


@pytest.mark.parametrize(
    "fn",
    [codec.enclose_from_base64, codec.uncover_to_base64],
)
def test_base64_rejects_string_subjects(fn):
    with pytest.raises(ValueError, match="TimestampedBytes"):
        fn(key("raw_nmea0183"), b"")


@pytest.mark.parametrize(
    "fn",
    [
        codec.enclose_from_text,
        codec.enclose_from_base64,
        codec.uncover_to_text,
        codec.uncover_to_base64,
    ],
)
def test_bare_value_codecs_reject_structured_subjects(fn):
    with pytest.raises(ValueError, match="json codec"):
        fn(key("location_fix"), b"")


@pytest.mark.parametrize(
    "fn",
    [
        codec.enclose_from_text,
        codec.enclose_from_base64,
        codec.uncover_to_text,
        codec.uncover_to_base64,
    ],
)
def test_bare_value_codecs_reject_unknown_subjects(fn):
    with pytest.raises(ValueError, match="not well-known"):
        fn(key("no_such_subject"), b"")


def test_json_round_trip():
    value = json.dumps({"latitude": 57.7, "longitude": 11.9, "altitude": 3.0})
    envelope = codec.enclose_from_json(key("location_fix"), value)
    decoded = json.loads(codec.uncover_to_json(key("location_fix"), envelope))
    assert decoded["latitude"] == 57.7
    assert decoded["longitude"] == 11.9
    assert decoded["altitude"] == 3.0


@pytest.mark.parametrize(
    "group, names",
    [
        (
            "zenoh_cli.codecs.encoders",
            {
                "keelson-enclose-from-text",
                "keelson-enclose-from-base64",
                "keelson-enclose-from-json",
            },
        ),
        (
            "zenoh_cli.codecs.decoders",
            {
                "keelson-uncover-to-text",
                "keelson-uncover-to-base64",
                "keelson-uncover-to-json",
            },
        ),
    ],
)
def test_codecs_are_registered_with_zenoh_cli(group, names):
    """zenoh-cli discovers the codecs by these entry-point names."""
    registered = {ep.name: ep for ep in entry_points(group=group)}
    assert names <= registered.keys()
    for name in names:
        assert callable(registered[name].load())
