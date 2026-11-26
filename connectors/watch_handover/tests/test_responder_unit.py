"""The responder's bookkeeping: what gets answered, how often, and when it stops.

`decide` is pure and covered next door. What is covered here is the part around
it that no e2e test can reach honestly — the retry of an answer that was *never
delivered*.

That distinction matters. A `put` is fire-and-forget under `congestion_control:
DROP`, so an answer can be shed on the way out and nothing ever comes back. From
outside the process that is indistinguishable from the answer having landed, so
an e2e test cannot stage it: republishing the record as pending makes the
connector's own answer echo back first, which clears the record and re-admits it
by a different path entirely. Driving the worker directly is the only way to
watch the retry counter do its job.
"""

import importlib.util
import pathlib
from importlib.machinery import SourceFileLoader

import keelson
import pytest
from keelson.payloads.ChecklistHandover_pb2 import ChecklistHandover

BIN = pathlib.Path(__file__).resolve().parents[1] / "bin" / "watch-handover2keelson.py"
_loader = SourceFileLoader("watch_handover_bin", str(BIN))
_spec = importlib.util.spec_from_loader(_loader.name, _loader)
responder_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(responder_mod)


class FakeSession:
    """Records puts. Stands in for a session whose puts are all being shed."""

    def __init__(self):
        self.puts = []
        self.put_kwargs = []

    def put(self, key, payload, **kwargs):
        self.puts.append((str(key), payload))
        self.put_kwargs.append(kwargs)


class Args:
    realm = "test-realm"
    entity_id = "test-vessel"
    checklist_realm = "test-roc"
    checklist_entity = "roc1"
    min_level = 3
    authority_max_age_s = 30.0
    authority_source_id = None
    startup_grace_s = 0.0
    answer_interval_s = 0.01
    answer_max_attempts = 3


class FakeAuthority:
    """Enough of an OperationalAuthority for `decide`."""

    def __init__(self, level):
        self.level = level
        self.active_constraints = []

    def HasField(self, _name):
        return False


@pytest.fixture
def responder():
    session = FakeSession()
    r = responder_mod.WatchHandoverResponder(session, Args())
    # A healthy vessel, so the verdict is a confirmation and the grace never bites.
    r._authority = {"agg": (FakeAuthority(5), r._started_at)}
    return r


def pending_record(handover_id="h-1", entity_id=Args.entity_id, realm=""):
    record = ChecklistHandover(
        handover_id=handover_id,
        status=ChecklistHandover.HANDOVER_STATUS_PENDING_VESSEL,
    )
    record.vessel.entity_id = entity_id
    record.vessel.realm = realm
    return record


def admit(responder, handover_id="h-1", record=None):
    """Put a pending record in front of the worker, as `on_handover` would."""
    responder._pending[handover_id] = 0
    responder._records[handover_id] = record or pending_record(handover_id)


def published(responder, index=-1):
    """The ChecklistHandover in a put, unwrapped from its envelope."""
    _key, payload = responder.session.puts[index]
    _, _, raw = keelson.uncover(payload)
    record = ChecklistHandover()
    record.ParseFromString(raw)
    return record


def test_an_undelivered_answer_is_published_again(responder):
    """The record staying at PENDING_VESSEL is the only signal there is.

    Nothing acknowledges a put. If the record never comes back changed, the
    answer never arrived, and a single attempt would strand the handover for
    ever — which is what the previous `_answered` set did.
    """
    admit(responder)

    responder.answer_pending()
    assert len(responder.session.puts) == 1

    # No echo arrives: the answer was shed. The worker must come back to it.
    responder.answer_pending()
    assert len(responder.session.puts) == 2


def test_retrying_stops_rather_than_publishing_for_ever(responder):
    """A record that never changes means a misconfigured storage, not a slow one.

    Retrying without a bound would turn that into an infinite publish loop and
    hide the misconfiguration instead of reporting it.
    """
    admit(responder)

    for _ in range(Args.answer_max_attempts + 3):
        responder.answer_pending()

    assert len(responder.session.puts) == Args.answer_max_attempts
    assert "h-1" not in responder._pending
    assert "h-1" not in responder._records


def test_a_record_that_leaves_pending_is_forgotten(responder):
    """The bookkeeping that stops both dicts growing without bound.

    A terminal record arriving is the acknowledgement the put cannot give, and
    dropping it here is what makes the maps bounded by *live* handovers rather
    than by every handover this process has ever seen.
    """
    admit(responder)
    responder.answer_pending()
    assert "h-1" in responder._pending

    responder.on_handover(
        FakeSample(
            "test-roc/@v0/roc1/pubsub/checklist_handover/h-1",
            ChecklistHandover(
                handover_id="h-1",
                status=ChecklistHandover.HANDOVER_STATUS_ACCEPTED,
            ),
        )
    )

    assert "h-1" not in responder._pending
    assert "h-1" not in responder._records

    # And it is not picked back up on the next pass.
    before = len(responder.session.puts)
    responder.answer_pending()
    assert len(responder.session.puts) == before


def test_the_grace_window_defers_only_the_no_authority_verdict(responder):
    """A vessel that has said nothing yet is "not heard from", not "no authority".

    Every other verdict is answered at once — the grace must not delay a refusal
    the vessel has actually earned.
    """
    responder.args.startup_grace_s = 60.0

    responder._authority = {}
    admit(responder, "h-silent")
    responder.answer_pending()
    assert responder.session.puts == [], "refused before hearing from the vessel"

    # A real reading, even a refusing one, is answered immediately.
    responder._authority = {"agg": (FakeAuthority(1), responder._started_at)}
    responder.answer_pending()
    assert len(responder.session.puts) == 1


def test_the_lowest_level_among_fresh_readings_governs(responder):
    """operational_authority is a veto: a second, cheerier publisher cannot lift it."""
    responder._authority = {
        "optimist": (FakeAuthority(5), responder._started_at),
        "pessimist": (FakeAuthority(1), responder._started_at),
    }
    authority, _age = responder.governing_authority()
    assert authority.level == 1


class FakeSample:
    """A sample carrying `record` as the wire carries it: an enveloped protobuf.
    Raw bytes pass through untouched, for payloads that are not one."""

    def __init__(self, key_expr, record):
        self.key_expr = key_expr
        raw = (
            record
            if isinstance(record, bytes)
            else keelson.enclose(record.SerializeToString())
        )
        self.payload = _Payload(raw)


class _Payload:
    def __init__(self, raw):
        self._raw = raw

    def to_bytes(self):
        return self._raw


# ── the answer on the wire (§7.5, keelson#239) ───────────────────────────


def test_a_confirmation_is_published_as_an_accepted_checklist_handover(responder):
    admit(responder)
    responder.answer_pending()

    answer = published(responder)
    assert answer.status == ChecklistHandover.HANDOVER_STATUS_ACCEPTED
    assert answer.handover_id == "h-1"
    assert answer.vessel_verdict.gate == ChecklistHandover.VesselVerdict.GATE_CONFIRMED
    assert answer.HasField("vessel_confirmed_at")
    assert answer.HasField("timestamp")
    assert not answer.HasField("refused_at")
    assert answer.refusal_source == ChecklistHandover.REFUSAL_SOURCE_UNKNOWN


def test_a_vessel_refusal_says_it_was_the_vessel_and_names_no_person(responder):
    """§7.5: REFUSED from the connector carries REFUSAL_SOURCE_VESSEL.

    `refused_by` is a PERSON; a vessel refusal never sets it, which is exactly
    why it cannot stand in for `refusal_source`.
    """
    responder._authority = {"agg": (FakeAuthority(1), responder._started_at)}
    admit(responder)
    responder.answer_pending()

    answer = published(responder)
    assert answer.status == ChecklistHandover.HANDOVER_STATUS_REFUSED
    assert answer.refusal_source == ChecklistHandover.REFUSAL_SOURCE_VESSEL
    assert not answer.HasField("refused_by")
    assert answer.HasField("refused_at")
    assert answer.refusal_reason == answer.vessel_verdict.reason != ""
    assert (
        answer.vessel_verdict.gate
        == ChecklistHandover.VesselVerdict.GATE_NON_AUTHORIZING
    )


def test_the_answer_keeps_every_field_the_station_wrote(responder):
    """A read-modify-write: what the vessel does not own goes back as it came.

    `accepted_at` in particular is the relief's instant, and the vessel's answer
    is a second instant beside it, never a replacement.
    """
    record = pending_record()
    record.run_id = "run-7"
    record.acknowledgment_note = "all clear"
    record.offered_by.operator_id = "op-1"
    record.accepted_at.FromSeconds(1_800_000_000)
    admit(responder, record=record)
    responder.answer_pending()

    answer = published(responder)
    assert answer.run_id == "run-7"
    assert answer.acknowledgment_note == "all clear"
    assert answer.offered_by.operator_id == "op-1"
    assert answer.accepted_at.seconds == 1_800_000_000


def test_a_pending_record_is_admitted_from_the_wire(responder):
    responder.on_handover(
        FakeSample(
            "test-roc/@v0/roc1/pubsub/checklist_handover/h-9", pending_record("h-9")
        )
    )
    assert "h-9" in responder._pending


def test_another_realms_vessel_of_the_same_name_is_left_alone(responder):
    responder.on_handover(
        FakeSample(
            "test-roc/@v0/roc1/pubsub/checklist_handover/h-x",
            pending_record("h-x", realm="elsewhere"),
        )
    )
    assert "h-x" not in responder._pending


def test_a_json_record_is_not_answered_and_says_why(responder, caplog):
    """The form from before keelson#231. Named, so it is not mistaken for noise."""
    import json

    responder.on_handover(
        FakeSample(
            "test-roc/@v0/roc1/pubsub/checklist_handover/h-old",
            json.dumps(
                {
                    "handoverId": "h-old",
                    "status": "pending_vessel",
                    "vessel": {"entityId": Args.entity_id},
                }
            ).encode("utf-8"),
        )
    )
    assert "h-old" not in responder._pending
    assert "raw JSON" in caplog.text


def test_the_answer_is_put_with_keywords_a_real_session_accepts(responder):
    """This fake takes any keyword; zenoh's `Session.put` does not.

    `reliability` is publisher-only and a put given it raises TypeError, which the
    worker logs and then retries until it gives up. That looks exactly like a
    misconfigured storage. Only the e2e run would have shown it.
    """
    import inspect

    import zenoh

    admit(responder)
    responder.answer_pending()

    accepted = set(inspect.signature(zenoh.Session.put).parameters)
    (kwargs,) = responder.session.put_kwargs
    assert set(kwargs) <= accepted
    assert kwargs["priority"] == zenoh.Priority.DATA_HIGH  # `elevated`
