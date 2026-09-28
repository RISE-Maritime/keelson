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

from collections import deque
from dataclasses import dataclass, field
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


@dataclass(frozen=True)
class LinkStats:
    """One peer's link over the window (NetworkStatus fields 9-12, #314)."""

    jitter_ms: Optional[float]
    pings_sent: int
    pongs_received: int
    window_s: float

    @property
    def loss_ratio(self) -> Optional[float]:
        if self.pings_sent == 0:
            return None
        return 1 - self.pongs_received / self.pings_sent


@dataclass
class LinkWindow:
    """Rolling per-peer statistics over the last ``window_s`` seconds of rounds.

    One round is one ping to one peer. It is answered (an RTT) or it is not
    (``None``) — and the unanswered ones are the reason this exists: the
    connector publishes nothing for a lost ping, so without counting rounds
    here a lossy link is indistinguishable from a quiet one, in a recording
    most of all.

    Jitter is the mean |ΔRTT| between consecutive ANSWERED rounds (the IPDV
    sense). A lost round in between is skipped rather than treated as a jump:
    loss is already counted, and counting it twice would make a lossy link
    look jittery too. It is ``None`` below two answers — a single sample has no
    variation to report, and 0 would claim a perfectly steady link.

    Pure: time is passed in, so tests need no clock.
    """

    window_s: float = 60.0
    _rounds: Deque[Tuple[float, Optional[float]]] = field(default_factory=deque)

    def record(self, at_s: float, rtt_ms: Optional[float]) -> None:
        self._rounds.append((at_s, rtt_ms))
        self._prune(at_s)

    def _prune(self, now_s: float) -> None:
        while self._rounds and now_s - self._rounds[0][0] > self.window_s:
            self._rounds.popleft()

    def stats(self, now_s: Optional[float] = None) -> LinkStats:
        if now_s is not None:
            self._prune(now_s)
        answered = [rtt for _, rtt in self._rounds if rtt is not None]
        jitter = None
        if len(answered) >= 2:
            jitter = sum(abs(b - a) for a, b in zip(answered, answered[1:])) / (
                len(answered) - 1
            )
        return LinkStats(
            jitter_ms=jitter,
            pings_sent=len(self._rounds),
            pongs_received=len(answered),
            window_s=self.window_s,
        )
