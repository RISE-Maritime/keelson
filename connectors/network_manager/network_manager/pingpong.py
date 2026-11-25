"""Round-trip timing maths for the NetworkPingPong exchange.

Kept free of zenoh and protobuf so it can be tested directly.

The exchange yields four timestamps, and ``NetworkPingPong.proto`` carries
exactly the ones needed for the standard NTP-style calculation:

    t1  ping sent          pinger's clock     NetworkPong.ping.sent_at
    t2  ping received      responder's clock  NetworkPong.ping_received_at
    t3  pong sent          responder's clock  NetworkPong.sent_at
    t4  pong received      pinger's clock     measured locally

From those::

    rtt    = (t4 - t1) - (t3 - t2)      # excludes the responder's own delay
    skew   = ((t2 - t1) + (t3 - t4)) / 2
    latency = rtt / 2

Subtracting (t3 - t2) matters: a responder that takes 200 ms to answer is not a
200 ms slower *link*, and reporting it as one would send someone looking for a
network fault that is not there.

The skew estimate assumes the path is roughly symmetric, which is the same
assumption NTP makes. An asymmetric path shows up as an apparent offset, so
treat skew as an indicator rather than a clock correction.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass
from typing import Deque, Optional, Tuple

NS_PER_MS = 1_000_000


@dataclass(frozen=True)
class PingResult:
    """One completed ping/pong exchange, in milliseconds."""

    round_trip_time_ms: float
    latency_ms: float
    clock_skew_ms: float
    payload_size_bytes: int = 0

    @property
    def payload_size_mb(self) -> float:
        return self.payload_size_bytes / (1024 * 1024)


def compute(
    ping_sent_ns: int,
    ping_received_ns: int,
    pong_sent_ns: int,
    pong_received_ns: int,
    payload_size_bytes: int = 0,
) -> PingResult:
    """Compute link timing from the four timestamps, in nanoseconds.

    Args are t1..t4 as described in the module docstring. t2 and t3 come from
    the responder's clock and may be offset from t1/t4 by any amount — that
    offset is what ``clock_skew_ms`` reports, and it cancels out of the RTT.
    """
    rtt_ns = (pong_received_ns - ping_sent_ns) - (pong_sent_ns - ping_received_ns)
    skew_ns = (
        (ping_received_ns - ping_sent_ns) + (pong_sent_ns - pong_received_ns)
    ) / 2

    # A negative RTT is not a fast link, it is a broken measurement: the
    # responder reported spending longer on the request than the whole
    # round trip took. Clamp rather than publish a nonsense figure.
    rtt_ms = max(0.0, rtt_ns / NS_PER_MS)

    return PingResult(
        round_trip_time_ms=rtt_ms,
        latency_ms=rtt_ms / 2,
        clock_skew_ms=skew_ns / NS_PER_MS,
        payload_size_bytes=payload_size_bytes,
    )


class _Window:
    """Entries over the last ``window_s`` seconds, measured back from the end of
    the latest round. Pure: time is passed in, so tests need no clock.

    Pass monotonic time. A wall clock that steps back, as one does when a
    vessel's computer syncs after boot, would stop old rounds from being pruned
    and let them dilute an outage for as long as the step was.
    """

    def __init__(self, window_s: float = 60.0):
        self.window_s = window_s
        self._entries: Deque[Tuple[float, object]] = deque()

    def _prune(self, end_s: float) -> None:
        while self._entries and end_s - self._entries[0][0] > self.window_s:
            self._entries.popleft()

    def __len__(self) -> int:
        return len(self._entries)

    def span_s(self, end_s: float) -> float:
        """The span actually covered: from the oldest entry still in the window
        to ``end_s``. Short of ``window_s`` until the window has filled, which
        is what a consumer must be told rather than the configured figure."""
        self._prune(end_s)
        if not self._entries:
            return 0.0
        return max(0.0, end_s - self._entries[0][0])


class PeerWindow(_Window):
    """The rounds to one peer: one ping is one round, answered (by any
    responder under the peer) or not.

    The latest round is never pruned, so there is a loss figure every round,
    however short the window. Loss lives here, per peer, and is reported every
    round, answered or not.
    That is the point of it: a link that drops completely keeps publishing
    loss rising to 100 %, live and in a recording, where a per-exchange
    message would fall silent for exactly as long as the outage lasts.
    """

    def _prune(self, end_s: float) -> None:
        while len(self._entries) > 1 and end_s - self._entries[0][0] > self.window_s:
            self._entries.popleft()

    def record(self, at_s: float, answered: bool, end_s: Optional[float] = None):
        self._entries.append((at_s, answered))
        self._prune(at_s if end_s is None else end_s)

    def loss_pct(self, end_s: Optional[float] = None) -> Optional[float]:
        """Percent of the rounds in the window with no answer; None with none."""
        if end_s is not None:
            self._prune(end_s)
        if not self._entries:
            return None
        lost = sum(1 for _, answered in self._entries if not answered)
        return 100.0 * lost / len(self._entries)


class ResponderWindow(_Window):
    """The answers from one responder under a peer: one link.

    Jitter and p95 are per responder, never per peer. A peer may run several
    responders, and timing each round by whichever answered first compares
    two different links whenever the fastest one changes — jitter that no
    link has. A round this responder did not answer is not a jump either:
    loss is counted per peer, and counting it again here would make a lossy
    link look jittery too.

    Both figures are None below two answers: one sample has no variation and
    no percentile worth the name, and 0 would claim a perfectly steady link.
    """

    def record(self, at_s: float, rtt_ms: float, end_s: Optional[float] = None):
        self._entries.append((at_s, rtt_ms))
        self._prune(at_s if end_s is None else end_s)

    def age(self, end_s: float) -> None:
        self._prune(end_s)

    def _rtts(self):
        return [rtt for _, rtt in self._entries]

    def jitter_ms(self) -> Optional[float]:
        """Mean |delta RTT| between consecutive answers (the IPDV sense)."""
        rtts = self._rtts()
        if len(rtts) < 2:
            return None
        return sum(abs(b - a) for a, b in zip(rtts, rtts[1:])) / (len(rtts) - 1)

    def p95_ms(self) -> Optional[float]:
        """95th-percentile RTT by nearest rank: an RTT that was actually
        measured, never an interpolation between two. Below 20 answers that
        rank is the largest one."""
        rtts = sorted(self._rtts())
        if len(rtts) < 2:
            return None
        return rtts[math.ceil(0.95 * len(rtts)) - 1]
