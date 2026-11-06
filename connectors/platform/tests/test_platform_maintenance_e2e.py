"""The platform connector's set_config only in MAINTENANCE (protocols/maintenance.yaml, crowsnest-dev#303).

The real connector process on an ISOLATED loopback router (multicast and gossip scouting off), with
--maintenance-vessel: a write is refused INVALID_STATE, naming why, while the vessel's maintenance_state
is unheard, in another state, or stale; in a fresh MAINTENANCE it is applied, and get_config shows it.
Reads stay open throughout.
"""

import json
import socket
import time

import keelson
import pytest
import zenoh
from keelson.interfaces.ErrorResponse_pb2 import ErrorResponse
from keelson.payloads.MaintenanceState_pb2 import MaintenanceState

pytestmark = pytest.mark.e2e

REALM, V, SRC = "e2e", "testvessel", "platform-e2e"
BASE_CONFIG = {
    "name": "Test Vessel",
    "length_over_all_m": 25.0,
    "breadth_over_all_m": 8.0,
}


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _isolated(conf, **kv):
    conf.insert_json5("scouting/multicast/enabled", "false")
    conf.insert_json5("scouting/gossip/enabled", "false")
    for k, v in kv.items():
        conf.insert_json5(k, json.dumps(v))
    return conf


@pytest.fixture
def bus(temp_dir):
    port = _free_port()
    session = zenoh.open(
        _isolated(
            zenoh.Config(),
            mode="router",
            **{"listen/endpoints": [f"tcp/127.0.0.1:{port}"], "connect/endpoints": []},
        )
    )
    zcfg = temp_dir / "zenoh.json5"
    zcfg.write_text(
        json.dumps(
            {
                "mode": "client",
                "scouting": {
                    "multicast": {"enabled": False},
                    "gossip": {"enabled": False},
                },
                "connect": {"endpoints": [f"tcp/127.0.0.1:{port}"]},
                "listen": {"endpoints": []},
            }
        )
    )
    yield session, zcfg
    session.close()


def _maintenance(session, state):
    key = keelson.construct_pubsub_key(REALM, V, "maintenance_state", V)
    record = MaintenanceState(
        vessel_id=V, state=state, maintenance_id="m-1", revision=3
    )
    session.put(key, keelson.enclose(record.SerializeToString()))


def _call(session, procedure, payload=b""):
    key = keelson.construct_rpc_key(REALM, V, "configurable", "v1", procedure, SRC)
    for reply in session.get(key, payload=payload, timeout=5):
        if reply.ok is not None:
            return True, reply.ok.payload.to_bytes()
        err = ErrorResponse()
        err.ParseFromString(reply.err.payload.to_bytes())
        return False, err
    raise AssertionError(f"no reply to {procedure}")


def _wait(pred, what, timeout=15.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if pred():
            return
        time.sleep(0.1)
    raise AssertionError(f"timed out waiting for {what}")


def test_set_config_only_in_a_fresh_maintenance(
    connector_process_factory, temp_dir, bus
):
    session, zcfg = bus
    config_path = temp_dir / "platform.json"
    config_path.write_text(json.dumps(BASE_CONFIG))
    proc = connector_process_factory(
        "platform",
        "platform-geometry",
        [
            "-r",
            REALM,
            "-e",
            V,
            "-s",
            SRC,
            "--config",
            str(config_path),
            "--interval",
            "1",
            "--zenoh-config",
            str(zcfg),
            "--maintenance-vessel",
            V,
            "--maintenance-max-age-ms",
            "1500",
        ],
    )
    proc.start()
    token = keelson.construct_source_liveliness_key(REALM, V, SRC)
    _wait(
        lambda: any(True for _ in session.liveliness().get(token, timeout=0.5)),
        "the connector's liveliness",
    )
    time.sleep(0.5)  # its queryables are declared just after the token

    wanted = {**BASE_CONFIG, "name": "Renamed in maintenance"}
    write = json.dumps(wanted).encode()

    ok, err = _call(session, "set_config", write)
    assert (
        not ok
        and err.code == ErrorResponse.Code.INVALID_STATE
        and "no maintenance_state heard" in err.error_description
    )

    _maintenance(session, MaintenanceState.State.STATE_INACTIVE)
    time.sleep(0.3)
    ok, err = _call(session, "set_config", write)
    assert not ok and err.code == ErrorResponse.Code.INVALID_STATE
    assert (
        f"set_config needs maintenance_state MAINTENANCE: {V} is INACTIVE"
        in err.error_description
    )

    ok, body = _call(session, "get_config")  # reads stay open
    assert ok and json.loads(body)["name"] == "Test Vessel"

    _maintenance(session, MaintenanceState.State.STATE_MAINTENANCE)
    time.sleep(0.3)
    ok, _ = _call(session, "set_config", write)
    assert ok
    ok, body = _call(session, "get_config")
    assert ok and json.loads(body)["name"] == "Renamed in maintenance"

    time.sleep(1.8)  # the manager went quiet: the record is now stale
    ok, err = _call(session, "set_config", json.dumps(BASE_CONFIG).encode())
    assert (
        not ok
        and err.code == ErrorResponse.Code.INVALID_STATE
        and "stale" in err.error_description
    )
    ok, body = _call(session, "get_config")
    assert (
        json.loads(body)["name"] == "Renamed in maintenance"
    )  # the refused write changed nothing


def test_not_opted_in_set_config_is_open(connector_process_factory, temp_dir, bus):
    """No --maintenance-vessel: today's behaviour, for a deployment no manager serves yet."""
    session, zcfg = bus
    config_path = temp_dir / "platform.json"
    config_path.write_text(json.dumps(BASE_CONFIG))
    proc = connector_process_factory(
        "platform",
        "platform-geometry",
        [
            "-r",
            REALM,
            "-e",
            V,
            "-s",
            SRC,
            "--config",
            str(config_path),
            "--interval",
            "1",
            "--zenoh-config",
            str(zcfg),
        ],
    )
    proc.start()
    token = keelson.construct_source_liveliness_key(REALM, V, SRC)
    _wait(
        lambda: any(True for _ in session.liveliness().get(token, timeout=0.5)),
        "the connector's liveliness",
    )
    time.sleep(0.5)
    ok, _ = _call(
        session, "set_config", json.dumps({**BASE_CONFIG, "name": "Open"}).encode()
    )
    assert ok
    assert json.loads(_call(session, "get_config")[1])["name"] == "Open"
