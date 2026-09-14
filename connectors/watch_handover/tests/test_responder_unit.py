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

import pytest

BIN = pathlib.Path(__file__).resolve().parents[1] / "bin" / "watch-handover2keelson.py"
_loader = SourceFileLoader("watch_handover_bin", str(BIN))
_spec = importlib.util.spec_from_loader(_loader.name, _loader)
responder_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(responder_mod)


class FakeSession:
    """Records puts. Stands in for a session whose puts are all being shed."""

    def __init__(self):
        self.puts = []

    def put(self, key, payload, **kwargs):
        self.puts.append((str(key), payload))


class Args:
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


def admit(responder, handover_id="h-1"):
    """Put a pending record in front of the worker, as `on_handover` would."""
    responder._pending[handover_id] = 0
    responder._records[handover_id] = {
        "handoverId": handover_id,
        "status": "pending_vessel",
        "vessel": {"entityId": Args.entity_id},
    }


def test_an_undelivered_answer_is_published_again(responder):
    """The record staying at pending_vessel is the only signal there is.

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
            {"handoverId": "h-1", "status": "accepted"},
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
    def __init__(self, key_expr, record):
        import json

        self.key_expr = key_expr
        self.payload = _Payload(json.dumps(record).encode("utf-8"))


class _Payload:
    def __init__(self, raw):
        self._raw = raw

    def to_bytes(self):
        return self._raw
