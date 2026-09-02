"""End-to-end tests for the watch_handover connector.

The unit tests cover `decide` exhaustively, but `decide` is pure — everything
between a sample arriving and an answer landing on the bus was verified by hand
until now: the key shapes, the entity match, echo suppression, and the three
behaviours added in response to review (a startup grace, a retry, and taking the
minimum across authority publishers).

Each test here fails against the code as it was before those fixes, which is the
point of them. The connector runs as a real subprocess against a real Zenoh peer
mesh; this process publishes `operational_authority` and handover records, and
subscribes to the handover key to read back what the vessel answered.
"""

import json
import threading
import time

import pytest
import zenoh

from keelson import construct_pubsub_key, construct_source_liveliness_key, enclose
from keelson.payloads.OperationalAuthority_pb2 import OperationalAuthority
from keelson.scaffolding import create_zenoh_config


REALM = "test-realm"
ENTITY_ID = "test-vessel"
CHECKLIST_REALM = "test-roc"
CHECKLIST_ENTITY = "roc-under-test"

# From OperationalAuthority.AuthorityLevel.
MINIMAL_SAFE_MODE = 1
SUPERVISED_REMOTE = 2
REMOTE_CONTROLLED = 3
FULL_AUTONOMOUS = 5


def handover_key(handover_id="*"):
    return (
        f"{CHECKLIST_REALM}/@v0/{CHECKLIST_ENTITY}"
        f"/pubsub/checklist_handover/{handover_id}"
    )


def authority_key(source_id):
    return construct_pubsub_key(REALM, ENTITY_ID, "operational_authority", source_id)


def pending_record(handover_id, entity_id=ENTITY_ID):
    return {
        "handoverId": handover_id,
        "status": "pending_vessel",
        "vessel": {"entityId": entity_id},
    }


class _Answers:
    """Collects handover records the connector writes back."""

    def __init__(self):
        self.records = []
        self._seen = threading.Event()

    def __call__(self, sample):
        try:
            record = json.loads(bytes(sample.payload.to_bytes()).decode("utf-8"))
        except Exception:
            return
        # Our own stimulus echoes back on the same key; only answers are interesting.
        if record.get("status") in ("accepted", "refused"):
            self.records.append(record)
            self._seen.set()

    def wait(self, timeout, count=1):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if len(self.records) >= count:
                return True
            time.sleep(0.05)
        return len(self.records) >= count


def publish_authority(session, source_id, level):
    authority = OperationalAuthority()
    authority.level = level
    session.put(authority_key(source_id), enclose(authority.SerializeToString()))


LIVELINESS_KEY = construct_source_liveliness_key(
    CHECKLIST_REALM, CHECKLIST_ENTITY, f"watch_handover/{ENTITY_ID}"
)


def wait_until_present(session, timeout=20.0):
    """Block until the connector's source-level liveliness token appears.

    Not politeness — correctness. Without a storage on the handover key, a record
    published before the connector's subscriber exists is simply lost, and the
    test would be asserting against a connector that never saw its stimulus. The
    token is also the only signal that says "subscribers declared": it is taken
    out in the same `with` block that declares them.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        replies = session.liveliness().get(LIVELINESS_KEY, timeout=1.0)
        for reply in replies:
            if reply.ok is not None:
                return True
        time.sleep(0.2)
    raise AssertionError(f"connector never became present at {LIVELINESS_KEY}")


def start_connector(factory, zenoh_endpoints, *extra):
    connector = factory(
        "watch_handover",
        "watch-handover2keelson.py",
        [
            "--realm",
            REALM,
            "--entity-id",
            ENTITY_ID,
            "--checklist-realm",
            CHECKLIST_REALM,
            "--checklist-entity",
            CHECKLIST_ENTITY,
            "--connect",
            zenoh_endpoints["connect"],
            "--answer-interval-s",
            "0.5",
            *extra,
        ],
    )
    connector.start()
    return connector


@pytest.fixture
def bus(zenoh_endpoints):
    session = zenoh.open(
        create_zenoh_config(
            mode="peer", connect=None, listen=[zenoh_endpoints["listen"]]
        )
    )
    try:
        yield session
    finally:
        session.close()


@pytest.mark.e2e
@pytest.mark.parametrize(
    "level, status, gate",
    [
        (FULL_AUTONOMOUS, "accepted", "confirmed"),
        (SUPERVISED_REMOTE, "refused", "below_floor"),
        (MINIMAL_SAFE_MODE, "refused", "non_authorizing"),
    ],
)
def test_the_vessel_answers_from_its_standing_authority(
    bus, connector_process_factory, zenoh_endpoints, level, status, gate
):
    """The whole path: authority in, answer out, on the keys the ROC reads."""
    answers = _Answers()
    sub = bus.declare_subscriber(handover_key(), answers)
    connector = start_connector(connector_process_factory, zenoh_endpoints)
    try:
        wait_until_present(bus)
        # Authority first, so the grace window never enters into it.
        for _ in range(6):
            publish_authority(bus, "agg", level)
            time.sleep(0.25)

        bus.put(handover_key("h-1"), json.dumps(pending_record("h-1")).encode("utf-8"))
        assert answers.wait(20), "connector never answered"

        answered = answers.records[0]
        assert answered["status"] == status
        assert answered["vesselVerdict"]["gate"] == gate
        assert answered["handoverId"] == "h-1"
        assert answered["vesselConfirmedAt"]
    finally:
        connector.stop()
        sub.undeclare()


@pytest.mark.e2e
def test_silence_at_startup_waits_rather_than_refusing(
    bus, connector_process_factory, zenoh_endpoints
):
    """The startup race: a retained record arrives before the first authority.

    Both subscriptions are declared together but do not fill together — the router
    replays the handover at once, while the aggregator's next sample is a publish
    period away. Answering immediately refuses a healthy vessel, terminally. With
    no grace this refuses within one worker pass; with it, the late authority is
    heard and the vessel is confirmed.
    """
    answers = _Answers()
    sub = bus.declare_subscriber(handover_key(), answers)
    connector = start_connector(
        connector_process_factory, zenoh_endpoints, "--startup-grace-s", "8"
    )
    try:
        wait_until_present(bus)
        # The record lands first, with nothing on operational_authority yet.
        bus.put(
            handover_key("h-race"),
            json.dumps(pending_record("h-race")).encode("utf-8"),
        )
        time.sleep(3.0)
        assert not answers.records, (
            "refused inside the grace window — a restart mid-handover would strand "
            "a healthy vessel"
        )

        # Authority turns up late, as it does after a restart.
        for _ in range(4):
            publish_authority(bus, "agg", FULL_AUTONOMOUS)
            time.sleep(0.25)

        assert answers.wait(20), "never answered once authority arrived"
        assert answers.records[0]["status"] == "accepted"
    finally:
        connector.stop()
        sub.undeclare()


@pytest.mark.e2e
def test_the_lowest_authority_among_publishers_governs(
    bus, connector_process_factory, zenoh_endpoints
):
    """Two aggregators disagreeing must not be settled by arrival order.

    operational_authority is the vessel's veto, so an aggregator reporting a
    constraint IS the vessel being constrained. The cheerful publisher is sent
    last on purpose: under the previous last-writer-wins cache that alone flipped
    the verdict to accepted.
    """
    answers = _Answers()
    sub = bus.declare_subscriber(handover_key(), answers)
    connector = start_connector(connector_process_factory, zenoh_endpoints)
    try:
        wait_until_present(bus)
        for _ in range(6):
            publish_authority(bus, "pessimist", MINIMAL_SAFE_MODE)
            time.sleep(0.1)
            publish_authority(bus, "optimist", FULL_AUTONOMOUS)
            time.sleep(0.15)

        bus.put(handover_key("h-2"), json.dumps(pending_record("h-2")).encode("utf-8"))
        assert answers.wait(20), "connector never answered"

        answered = answers.records[0]
        assert answered["status"] == "refused"
        assert answered["vesselVerdict"]["gate"] == "non_authorizing"
    finally:
        connector.stop()
        sub.undeclare()


@pytest.mark.e2e
def test_a_record_returned_to_pending_is_answered_again(
    bus, connector_process_factory, zenoh_endpoints
):
    """A handover put back to pending_vessel gets a fresh answer.

    Deliberately NOT the dropped-answer retry — that cannot be staged from out
    here. Republishing the record makes the connector's own answer echo back
    first, which releases the id and re-admits it by a different path; the retry
    counter is never consulted. `test_responder_unit.py` drives that one directly.

    What this does cover is that the previous `_answered` set is gone: it stamped
    the id before publishing and never revisited it, so a record returned to
    pending by a station was ignored for the life of the process.
    """
    answers = _Answers()
    sub = bus.declare_subscriber(handover_key(), answers)
    connector = start_connector(connector_process_factory, zenoh_endpoints)
    try:
        wait_until_present(bus)
        for _ in range(6):
            publish_authority(bus, "agg", FULL_AUTONOMOUS)
            time.sleep(0.2)

        bus.put(handover_key("h-3"), json.dumps(pending_record("h-3")).encode("utf-8"))
        assert answers.wait(20), "no first answer"

        # As if the answer had been shed on the way out.
        bus.put(handover_key("h-3"), json.dumps(pending_record("h-3")).encode("utf-8"))
        assert answers.wait(20, count=2), "never retried a dropped answer"
    finally:
        connector.stop()
        sub.undeclare()


@pytest.mark.e2e
def test_another_vessels_handover_is_left_alone(
    bus, connector_process_factory, zenoh_endpoints
):
    """One tree carries every vessel's handovers; answering another's is a bug."""
    answers = _Answers()
    sub = bus.declare_subscriber(handover_key(), answers)
    connector = start_connector(connector_process_factory, zenoh_endpoints)
    try:
        wait_until_present(bus)
        for _ in range(4):
            publish_authority(bus, "agg", FULL_AUTONOMOUS)
            time.sleep(0.2)

        bus.put(
            handover_key("h-other"),
            json.dumps(pending_record("h-other", entity_id="some-other-vessel")).encode(
                "utf-8"
            ),
        )
        time.sleep(5.0)
        assert not answers.records, "answered a handover naming another vessel"
    finally:
        connector.stop()
        sub.undeclare()


@pytest.mark.e2e
def test_the_answer_echoing_back_is_not_answered_again(
    bus, connector_process_factory, zenoh_endpoints
):
    """Idempotence by construction: only `pending_vessel` is this connector's."""
    answers = _Answers()
    sub = bus.declare_subscriber(handover_key(), answers)
    connector = start_connector(connector_process_factory, zenoh_endpoints)
    try:
        wait_until_present(bus)
        for _ in range(6):
            publish_authority(bus, "agg", FULL_AUTONOMOUS)
            time.sleep(0.2)

        bus.put(handover_key("h-4"), json.dumps(pending_record("h-4")).encode("utf-8"))
        assert answers.wait(20), "connector never answered"

        # The connector's own answer is on the wire and has echoed back to it.
        # Nothing further should be written for this id.
        time.sleep(4.0)
        assert len(answers.records) == 1, "re-answered its own terminal record"
    finally:
        connector.stop()
        sub.undeclare()
