"""End-to-end tests for excluding keys from a recording (#266).

A real mcap-record subprocess, a real Zenoh session in the test process:
exclusions from the command line, and a key set replaced over configurable/v1
while the recorder keeps writing the same file.
"""

import json
import time
from pathlib import Path

import pytest
import zenoh
from mcap.reader import make_reader

import keelson
from keelson.interfaces.ErrorResponse_pb2 import ErrorResponse
from keelson.scaffolding import create_zenoh_config

REALM = "test-realm"
ENTITY = "test-recorder"
SOURCE = "rec1"

BOAT = f"{REALM}/@v0/boat"
POSITION_KEY = f"{BOAT}/pubsub/location_fix/gnss/0"
LOG_KEY = f"{BOAT}/pubsub/log_message/docker/ntrip"
LOGS = f"{BOAT}/pubsub/log_message/**"


def _rpc_key(procedure: str) -> str:
    return keelson.construct_rpc_key(
        REALM, ENTITY, "configurable", "v1", procedure, SOURCE
    )


def _call(session: zenoh.Session, procedure: str, doc=None, timeout: float = 3.0):
    """Call a configurable/v1 procedure; return (ok_bytes, error_description)."""
    ok: list[bytes] = []
    err: list[str] = []

    def _cb(reply: zenoh.Reply) -> None:
        if reply.ok is not None:
            ok.append(reply.ok.payload.to_bytes())
        else:
            msg = ErrorResponse()
            msg.ParseFromString(reply.err.payload.to_bytes())
            err.append(msg.error_description)

    payload = json.dumps(doc).encode() if doc is not None else b""
    session.get(_rpc_key(procedure), _cb, payload=payload)
    deadline = time.time() + timeout
    while not (ok or err) and time.time() < deadline:
        time.sleep(0.05)
    assert ok or err, f"no reply to {procedure}"
    return (ok[0] if ok else None), (err[0] if err else None)


def _get_config(session: zenoh.Session) -> dict:
    ok, err = _call(session, "get_config")
    assert err is None, err
    return json.loads(ok)


def _publish(session: zenoh.Session, key: str, payloads) -> None:
    for payload in payloads:
        session.put(key, keelson.enclose(payload=payload))
        time.sleep(0.05)


def _recording(output_dir: Path):
    """(messages by topic, key-set metadata records) from the single file."""
    files = list(output_dir.glob("*.mcap"))
    assert len(files) == 1, f"expected one file (no rotation), got {files}"
    messages: dict[str, list[bytes]] = {}
    with files[0].open("rb") as f:
        reader = make_reader(f)
        for _schema, channel, message in reader.iter_messages():
            messages.setdefault(channel.topic, []).append(message.data)
    with files[0].open("rb") as f:
        records = [
            m.metadata
            for m in make_reader(f).iter_metadata()
            if m.name == "keelson2mcap.key_set"
        ]
    return messages, records


@pytest.fixture
def session(zenoh_endpoints):
    conf = create_zenoh_config(
        mode="peer", connect=[zenoh_endpoints["connect"]], listen=None
    )
    s = zenoh.open(conf)
    try:
        yield s
    finally:
        s.close()


@pytest.fixture
def output_dir(temp_dir: Path) -> Path:
    d = temp_dir / "recording"
    d.mkdir()
    return d


@pytest.fixture
def start_recorder(connector_process_factory, output_dir, zenoh_endpoints):
    def _start(*extra: str):
        recorder = connector_process_factory(
            "mcap",
            "mcap-record",
            [
                "--output-folder",
                str(output_dir),
                "--realm",
                REALM,
                "--entity-id",
                ENTITY,
                "--source-id",
                SOURCE,
                "--mode",
                "peer",
                "--listen",
                zenoh_endpoints["listen"],
                "--bypass-safeguards",
                *extra,
            ],
        )
        recorder.start()
        time.sleep(1.5)
        return recorder

    return _start


@pytest.mark.e2e
class TestExcludeKeys:

    def test_excluded_key_is_never_written(self, start_recorder, session, output_dir):
        recorder = start_recorder("-k", f"{BOAT}/**", "-x", LOGS)

        _publish(session, POSITION_KEY, [b"pos"] * 5)
        _publish(session, LOG_KEY, [b"NTRIP_PASSWORD=hunter2"] * 5)
        time.sleep(1)
        recorder.stop()

        messages, records = _recording(output_dir)
        assert POSITION_KEY in messages
        assert LOG_KEY not in messages
        assert len(records) == 1
        assert records[0]["generation"] == "0"
        assert records[0]["source"] == "cli"
        assert json.loads(records[0]["exclude_keys"]) == [LOGS]

    def test_exclusion_applied_mid_recording_without_rotation(
        self, start_recorder, session, output_dir
    ):
        recorder = start_recorder("-k", f"{BOAT}/**")

        _publish(session, LOG_KEY, [b"before-1", b"before-2"])
        time.sleep(0.5)

        ok, err = _call(
            session, "set_config", {"keys": [f"{BOAT}/**"], "exclude_keys": [LOGS]}
        )
        assert err is None, err
        time.sleep(0.5)

        _publish(session, LOG_KEY, [b"after-1", b"after-2"])
        _publish(session, POSITION_KEY, [b"pos"] * 3)
        time.sleep(1)
        recorder.stop()

        messages, records = _recording(output_dir)
        assert messages[LOG_KEY] == [b"before-1", b"before-2"]
        assert POSITION_KEY in messages
        assert [r["generation"] for r in records] == ["0", "1"]
        assert records[1]["source"] == "set_config"
        assert json.loads(records[1]["exclude_keys"]) == [LOGS]

    def test_get_config_and_rejected_document(self, start_recorder, session):
        recorder = start_recorder("-k", f"{BOAT}/**", "-x", LOGS)
        try:
            before = _get_config(session)
            assert before == {"keys": [f"{BOAT}/**"], "exclude_keys": [LOGS]}

            ok, err = _call(session, "set_config", {"keys": ["a//b"]})
            assert ok is None
            assert "not a valid key expression" in err

            assert _get_config(session) == before
        finally:
            recorder.stop()

    def test_adding_a_key_at_runtime_starts_recording_it(
        self, start_recorder, session, output_dir
    ):
        recorder = start_recorder("-k", POSITION_KEY)

        ok, err = _call(session, "set_config", {"keys": [POSITION_KEY, LOG_KEY]})
        assert err is None, err
        time.sleep(0.5)

        _publish(session, LOG_KEY, [b"now-recorded"])
        time.sleep(1)
        recorder.stop()

        messages, _records = _recording(output_dir)
        assert messages.get(LOG_KEY) == [b"now-recorded"]

    def test_locked_recorder_refuses_set_config(self, start_recorder, session):
        recorder = start_recorder("-k", f"{BOAT}/**", "--no-runtime-reconfiguration")
        try:
            ok, err = _call(
                session, "set_config", {"keys": [f"{BOAT}/**"], "exclude_keys": [LOGS]}
            )
            assert ok is None
            assert "--no-runtime-reconfiguration" in err
            assert _get_config(session) == {"keys": [f"{BOAT}/**"], "exclude_keys": []}
        finally:
            recorder.stop()
