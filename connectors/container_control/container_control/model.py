"""Translation from a container runtime's raw attributes into protobuf.

Deliberately pure: this module imports neither ``docker`` nor ``zenoh``, so the
bulk of the test suite exercises it with plain dictionaries. :mod:`backend`
produces a :class:`ContainerSnapshot` from whatever the runtime hands back; this
module turns that into a :class:`ContainerInfo` and nothing else touches the
wire types.
"""

from __future__ import annotations

import fnmatch
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from google.protobuf.timestamp_pb2 import Timestamp

from keelson.interfaces.ContainerControl_pb2 import LogLine
from keelson.payloads.ContainerHost_pb2 import (
    ContainerHealthStatus,
    ContainerInfo,
    ContainerResourceUsage,
    ContainerRestartPolicy,
    ContainerState,
)

# The Docker API reports "never happened" as the proto-zero instant rather than
# omitting the field. Passed through, it renders as a year-1 date in a UI.
_ZERO_TIMES = frozenset({"", "0001-01-01T00:00:00Z", "0001-01-01T00:00:00.000000000Z"})

# Docker's --timestamps prefix: RFC3339Nano, then a single space, then the line.
_LOG_PREFIX = re.compile(rb"^(\S+?Z)\s(.*)$", re.DOTALL)

_STATES = {
    "created": ContainerState.CONTAINER_STATE_CREATED,
    "running": ContainerState.CONTAINER_STATE_RUNNING,
    "paused": ContainerState.CONTAINER_STATE_PAUSED,
    "restarting": ContainerState.CONTAINER_STATE_RESTARTING,
    "removing": ContainerState.CONTAINER_STATE_REMOVING,
    "exited": ContainerState.CONTAINER_STATE_EXITED,
    "dead": ContainerState.CONTAINER_STATE_DEAD,
}

_RESTART_POLICIES = {
    # The Engine API spells "no restart policy" as the empty string, which is
    # indistinguishable from "not reported" by the time it reaches a UI. Both
    # spellings map to the explicit NO.
    "": ContainerRestartPolicy.RESTART_POLICY_NO,
    "no": ContainerRestartPolicy.RESTART_POLICY_NO,
    "always": ContainerRestartPolicy.RESTART_POLICY_ALWAYS,
    "unless-stopped": ContainerRestartPolicy.RESTART_POLICY_UNLESS_STOPPED,
    "on-failure": ContainerRestartPolicy.RESTART_POLICY_ON_FAILURE,
}

_HEALTH = {
    "starting": ContainerHealthStatus.HEALTH_STATUS_STARTING,
    "healthy": ContainerHealthStatus.HEALTH_STATUS_HEALTHY,
    "unhealthy": ContainerHealthStatus.HEALTH_STATUS_UNHEALTHY,
    "none": ContainerHealthStatus.HEALTH_STATUS_NONE,
}

COMPOSE_PROJECT_LABEL = "com.docker.compose.project"
COMPOSE_SERVICE_LABEL = "com.docker.compose.service"


@dataclass(frozen=True)
class ContainerSnapshot:
    """One container as the runtime described it, with nothing interpreted yet.

    ``image_tags`` and ``image_id`` are carried separately from ``attrs``
    because docker-py exposes them on the image object rather than in the
    container's attribute dictionary.
    """

    name: str
    id: str
    attrs: dict
    image_tags: Sequence[str] = ()
    image_id: str = ""
    labels: dict = field(default_factory=dict)


def parse_docker_time(value) -> Timestamp | None:
    """RFC3339(Nano) string to a Timestamp, or None when the runtime is saying
    "never".

    ``Timestamp.FromJsonString`` is used rather than ``datetime.fromisoformat``
    because Docker emits nine fractional digits, which ``fromisoformat`` rejects
    on every Python that ships today.
    """
    if not isinstance(value, str) or value in _ZERO_TIMES:
        return None
    ts = Timestamp()
    try:
        ts.FromJsonString(value)
    except ValueError:
        return None
    # A runtime that reports a differently-spelled zero instant still means
    # "never"; catch it after parsing rather than growing the literal set.
    if ts.seconds < 0 and ts.ToDatetime().year <= 1:
        return None
    return ts


def container_state(raw: str) -> int:
    return _STATES.get((raw or "").lower(), ContainerState.CONTAINER_STATE_UNSPECIFIED)


def restart_policy(raw) -> int:
    if raw is None:
        return ContainerRestartPolicy.RESTART_POLICY_UNSPECIFIED
    return _RESTART_POLICIES.get(
        str(raw).lower(), ContainerRestartPolicy.RESTART_POLICY_UNSPECIFIED
    )


def health_status(state: dict) -> int:
    """A container with no health check declares none -- which is a different
    answer from "the responder could not tell"."""
    health = (state or {}).get("Health")
    if not health:
        return ContainerHealthStatus.HEALTH_STATUS_NONE
    return _HEALTH.get(
        str(health.get("Status", "")).lower(),
        ContainerHealthStatus.HEALTH_STATUS_UNSPECIFIED,
    )


def image_reference(snapshot: ContainerSnapshot) -> str:
    """The image as deployed.

    The old implementation was ``container.image.tags[0]``, which raises
    IndexError for an image pulled by digest or whose tag was pruned out from
    under it -- and took the entire listing down with it, not one row.
    """
    for tag in snapshot.image_tags or ():
        if tag:
            return tag
    configured = (snapshot.attrs.get("Config") or {}).get("Image")
    return configured or snapshot.image_id or ""


def build_container_info(
    snapshot: ContainerSnapshot, *, controllable: bool, removable: bool
) -> ContainerInfo:
    """Render one snapshot as the wire message.

    Both permission flags are REQUIRED keywords rather than defaulted. They are
    the two fields a client greys its buttons on, and a call site that forgot one
    would ship a plausible-looking message claiming the wrong thing -- silently,
    since False is a valid answer. Making it a TypeError means a new call site
    has to decide.
    """
    attrs = snapshot.attrs or {}
    state = attrs.get("State") or {}
    host_config = attrs.get("HostConfig") or {}
    policy = host_config.get("RestartPolicy") or {}
    labels = snapshot.labels or (attrs.get("Config") or {}).get("Labels") or {}

    info = ContainerInfo(
        name=snapshot.name,
        id=snapshot.id,
        image=image_reference(snapshot),
        raw_state=str(state.get("Status", "") or ""),
        restart_policy=restart_policy(policy.get("Name")),
        restart_policy_max_retries=int(policy.get("MaximumRetryCount") or 0),
        restart_count=int(attrs.get("RestartCount") or 0),
        health=health_status(state),
        controllable=controllable,
        removable=removable,
        compose_project=str(labels.get(COMPOSE_PROJECT_LABEL, "") or ""),
        compose_service=str(labels.get(COMPOSE_SERVICE_LABEL, "") or ""),
    )
    info.state = container_state(info.raw_state)

    for field_name, raw in (
        ("created_at", attrs.get("Created")),
        ("started_at", state.get("StartedAt")),
        ("finished_at", state.get("FinishedAt")),
    ):
        parsed = parse_docker_time(raw)
        if parsed is not None:
            getattr(info, field_name).CopyFrom(parsed)

    # ExitCode is meaningless until the container has actually exited once; a
    # running container's 0 would read as "exited cleanly".
    if parse_docker_time(state.get("FinishedAt")) is not None:
        info.exit_code = int(state.get("ExitCode") or 0)

    return info


# -----------------------------------------------------------------------------
# Resource utilisation
# -----------------------------------------------------------------------------
#
# All of it derived here rather than in the publisher, for the reason the rest
# of this module exists: no docker, no zenoh, so the arithmetic is testable with
# plain dictionaries and the two cases that actually bite -- a first sample with
# nothing to difference against, and counters that went backwards -- are unit
# tests rather than something you wait for a container to restart to see.


@dataclass(frozen=True)
class StatsSample:
    """The counters from one reading, kept so the next one can be differenced.

    Every field is cumulative-since-container-start except ``monotonic_s``,
    which is when we read them. The window is measured, not assumed from the
    configured interval: a tick the daemon was slow to answer produces a wider
    one, and dividing by the nominal interval would report a rate that never
    happened.
    """

    cpu_total_ns: int
    system_cpu_ns: int
    #: None for a container on the host network stack, which has no interfaces.
    rx_bytes: int | None
    tx_bytes: int | None
    #: None when the runtime reports no block accounting.
    block_read_bytes: int | None
    block_write_bytes: int | None
    monotonic_s: float


def _num(value, default=0):
    """Docker's stats payload is JSON from a Go struct: a field can be absent, be
    null, or -- on a container that is not running -- be present with the whole
    parent object empty. All three mean "no reading"."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    return value


def _network_totals(raw: dict) -> tuple[int | None, int | None]:
    """Summed rx/tx across interfaces, or ``(None, None)``.

    A container sharing the host's network namespace (``network_mode: host``)
    has no interface of its own, and the Engine omits the `networks` key rather
    than reporting zeroes. Passing zeroes on would claim the container sends
    nothing, which is the opposite of true -- it is using the host's NIC, whose
    counters belong to the host and are published by a host telemetry agent.
    """
    networks = raw.get("networks")
    if not isinstance(networks, dict) or not networks:
        return None, None
    rx = tx = 0
    for interface in networks.values():
        if isinstance(interface, dict):
            rx += int(_num(interface.get("rx_bytes")))
            tx += int(_num(interface.get("tx_bytes")))
    return rx, tx


def _block_totals(raw: dict) -> tuple[int | None, int | None]:
    """Summed block-device bytes read and written, or ``(None, None)``.

    ``io_service_bytes_recursive`` is a list of ``{major, minor, op, value}``
    per device and operation. The op is spelled "read"/"write" under cgroup v2
    and "Read"/"Write" under v1, so both sides are lowercased rather than the
    payload trusted to be one or the other.

    None when the runtime reported no block accounting at all -- some storage
    drivers report none, and the Engine sends the key as null. "Wrote nothing"
    and "nobody counted" are different facts, and flattening the second into a
    zero is the mistake this whole message is written to avoid.
    """
    entries = (raw.get("blkio_stats") or {}).get("io_service_bytes_recursive")
    if not entries:
        return None, None
    read = write = 0
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        op = str(entry.get("op", "")).lower()
        value = int(_num(entry.get("value")))
        if op == "read":
            read += value
        elif op == "write":
            write += value
    return read, write


def _memory_working_set(memory: dict) -> int:
    """Usage minus reclaimable file cache -- what ``docker stats`` shows.

    Raw ``usage`` includes page cache the kernel would drop the moment anything
    asked for the memory, so a container that has merely read a large file reads
    as one leaking. cgroup v2 names the reclaimable part `inactive_file`, v1
    `total_inactive_file`; a runtime reporting neither falls back to raw usage,
    which is wrong but visibly wrong rather than absent.
    """
    usage = int(_num(memory.get("usage")))
    detail = memory.get("stats")
    if isinstance(detail, dict):
        # v1's key is tried FIRST, and the value is required to be below usage,
        # exactly as docker's own CLI does it: a v1 host reports both spellings
        # and the `total_` one is the correct subtrahend. The guard is not
        # defensive tidiness -- it is what makes a nonsensical pair fall back to
        # raw usage instead of producing a clamped zero that looks like an idle
        # container.
        for key in ("total_inactive_file", "inactive_file"):
            value = detail.get(key)
            if value is not None and 0 <= int(_num(value)) < usage:
                return usage - int(_num(value))
    return usage


def _cpu_allocation_cores(host_config: dict) -> float | None:
    """Cores the container may use, or None when unconstrained.

    Two spellings of the same constraint: `--cpus` arrives as NanoCpus, while
    the older `--cpu-quota`/`--cpu-period` pair arrives as itself. Compose
    writes one or the other depending on the key used, so both are read.
    """
    nano = _num(host_config.get("NanoCpus"))
    if nano > 0:
        return nano / 1e9
    quota = _num(host_config.get("CpuQuota"))
    period = _num(host_config.get("CpuPeriod"))
    if quota > 0 and period > 0:
        return quota / period
    return None


def read_stats_sample(raw: dict, *, monotonic_s: float) -> StatsSample | None:
    """The counters worth keeping from one reading, or None if there are none.

    A container that stopped between the listing and the stats call still
    answers, with `cpu_stats` present but zeroed. Differencing against that
    would report a negative delta on the next sample; returning None means the
    next sample is treated as a first one instead.
    """
    if not isinstance(raw, dict):
        return None
    cpu = raw.get("cpu_stats") or {}
    total = int(_num((cpu.get("cpu_usage") or {}).get("total_usage")))
    system = int(_num(cpu.get("system_cpu_usage")))
    # Both guards matter and neither subsumes the other: a stopped container's
    # body has zeroes throughout, while a live one always has a host-wide system
    # total. Without a system total there is nothing to difference against next
    # tick, so there is no sample worth keeping.
    if total <= 0 or system <= 0:
        return None
    rx, tx = _network_totals(raw)
    block_read, block_write = _block_totals(raw)
    return StatsSample(
        cpu_total_ns=total,
        system_cpu_ns=system,
        rx_bytes=rx,
        tx_bytes=tx,
        block_read_bytes=block_read,
        block_write_bytes=block_write,
        monotonic_s=monotonic_s,
    )


def _rate(current: int, previous: int, elapsed_s: float) -> float | None:
    """Bytes per second, or None when the pair cannot support the claim.

    A negative delta means the counters reset under us -- the container was
    recreated, or restarted in place -- and the honest answer there is no
    answer, not a spike the size of the whole counter.
    """
    if elapsed_s <= 0:
        return None
    delta = current - previous
    if delta < 0:
        return None
    return delta / elapsed_s


def build_resource_usage(
    snapshot: ContainerSnapshot,
    raw: dict,
    previous: StatsSample | None,
    *,
    monotonic_s: float,
) -> tuple[ContainerResourceUsage | None, StatsSample | None]:
    """One container's utilisation, plus the sample to difference the next one against.

    Returns ``(usage, sample)``, or ``(None, None)`` when the reading carries no
    measurement at all.

    That second case is a container that stopped between the listing and its
    stats call. The Engine answers such a call with 200 and an all-empty body --
    no `system_cpu_usage`, empty `memory_stats` -- and building a row from it
    would publish "0% CPU, 0 bytes" for a container whose truth is "not
    running". Omitting it is the honest answer; container_status carries the
    stopped ones.

    UNSET IS NOT ZERO. Everything derived from a pair of readings -- the CPU
    percentage and all four rates -- is left unset when there is no usable
    predecessor. That is the first sample after this process started or after
    the container appeared, and any sample whose counters went backwards. A zero
    there would draw a flat line through a gap and read as an idle container.
    """
    raw = raw if isinstance(raw, dict) else {}
    sample = read_stats_sample(raw, monotonic_s=monotonic_s)
    if sample is None:
        return None, None

    host_config = (snapshot.attrs or {}).get("HostConfig") or {}
    cpu_stats = raw.get("cpu_stats") or {}
    cpu_usage = cpu_stats.get("cpu_usage") or {}
    memory = raw.get("memory_stats") or {}
    pids = raw.get("pids_stats") or {}
    throttling = cpu_stats.get("throttling_data") or {}

    usage = ContainerResourceUsage(
        name=snapshot.name,
        id=snapshot.id,
        memory_used_bytes=_memory_working_set(memory),
        pids_current=int(_num(pids.get("current"))),
        cpuset_cpus=str(host_config.get("CpusetCpus") or ""),
    )

    # Set whenever the runtime reports them AT ALL, which means a healthy
    # container publishes an explicit zero rather than nothing. That is the
    # entire reason these two are `optional`: zero throttling is the normal
    # reading, so a plain proto3 scalar would drop them from the wire on exactly
    # the containers that are fine, and a consumer decoding a hard 0 could not
    # tell "measured, never throttled" from "not reported".
    if isinstance(throttling, dict) and throttling:
        usage.cpu_throttled_periods = int(_num(throttling.get("throttled_periods")))
        usage.cpu_throttled_time_ns = int(_num(throttling.get("throttled_time")))

    online = int(_num(cpu_stats.get("online_cpus")))
    if online <= 0:
        online = len(cpu_usage.get("percpu_usage") or ()) or 0
    if online > 0:
        # Left unset rather than zeroed when the runtime reports neither a core
        # count nor a per-core list: it is the divisor a consumer needs to
        # normalise cpu_load_pct, and a zero denominator fails silently.
        usage.online_cpus = online

    # The memory ceiling is read from the configuration, not from the counters:
    # an unconstrained container's cgroup limit IS the host's total memory, and
    # reporting that as "the limit" makes every container on a 32 GiB box look
    # comfortably within bounds it does not have.
    # Two fields, two sources, neither inferred from the other.
    #
    # The percentage divides by `memory_stats.limit`, which the Engine sets to
    # the configured limit when there is one and to the host's total memory when
    # there is not -- so it is the same number `docker stats` prints in MEM%,
    # and it is answerable for every container.
    #
    # The LIMIT field is read from the configuration instead, and only when one
    # was actually set. Passing the counter's limit through would tell an
    # operator every container on this box is "limited to 31 GiB", which is a
    # limit nothing will ever enforce.
    limit = int(_num(memory.get("limit")))
    if limit > 0:
        usage.memory_used_pct = usage.memory_used_bytes / limit * 100.0
    configured_memory = int(_num(host_config.get("Memory")))
    if configured_memory > 0:
        usage.memory_limit_bytes = limit or configured_memory

    block_read, block_write = _block_totals(raw)
    if block_read is not None and block_write is not None:
        usage.block_read_bytes = block_read
        usage.block_write_bytes = block_write

    rx, tx = _network_totals(raw)
    if rx is not None and tx is not None:
        usage.network_rx_bytes = rx
        usage.network_tx_bytes = tx

    pids_limit = int(_num(pids.get("limit")))
    if pids_limit > 0:
        usage.pids_limit = pids_limit

    allocation = _cpu_allocation_cores(host_config)
    if allocation is not None:
        usage.cpu_allocation_cores = allocation
    shares = int(_num(host_config.get("CpuShares")))
    if shares > 0:
        usage.cpu_shares = shares

    if previous is not None:
        elapsed = sample.monotonic_s - previous.monotonic_s
        if elapsed > 0:
            usage.sample_window_s = elapsed

        cpu_delta = sample.cpu_total_ns - previous.cpu_total_ns
        system_delta = sample.system_cpu_ns - previous.system_cpu_ns
        # system_cpu_usage is the host's total busy time across all cores, so
        # the ratio is already per-host; multiplying by the core count converts
        # it to the "percent of one core" convention `docker stats` uses.
        if cpu_delta >= 0 and system_delta > 0 and online > 0:
            usage.cpu_load_pct = cpu_delta / system_delta * online * 100.0

        if block_read is not None and previous.block_read_bytes is not None:
            read_rate = _rate(block_read, previous.block_read_bytes, elapsed)
            if read_rate is not None:
                usage.block_read_bytes_per_second = read_rate
        if block_write is not None and previous.block_write_bytes is not None:
            write_rate = _rate(block_write, previous.block_write_bytes, elapsed)
            if write_rate is not None:
                usage.block_write_bytes_per_second = write_rate

        if rx is not None and previous.rx_bytes is not None:
            rx_rate = _rate(rx, previous.rx_bytes, elapsed)
            if rx_rate is not None:
                usage.network_rx_bytes_per_second = rx_rate
        if tx is not None and previous.tx_bytes is not None:
            tx_rate = _rate(tx, previous.tx_bytes, elapsed)
            if tx_rate is not None:
                usage.network_tx_bytes_per_second = tx_rate

    return usage, sample


def matches_any(name: str, globs: Iterable[str]) -> bool:
    return any(fnmatch.fnmatchcase(name, g) for g in globs)


def parse_log_line(chunk: bytes, stream: int) -> LogLine | None:
    """One line of Docker ``--timestamps`` output to a LogLine, or None if blank.

    The single place the wire format is understood, so the pull path (the
    ``logs`` RPC, via :func:`parse_log_stream`) and the follow path
    (:mod:`logs_follower`) cannot drift into parsing it two different ways.

    ``errors="replace"`` is deliberate: a multibyte sequence split at a buffer
    boundary is normal, and a plain ``.decode("utf-8")`` turned it into a
    UnicodeDecodeError that escaped the callback so the RPC never replied at all.
    """
    if not chunk.strip():
        return None
    match = _LOG_PREFIX.match(chunk)
    text, ts = (
        (match.group(2), parse_docker_time(match.group(1).decode("ascii", "replace")))
        if match
        else (chunk, None)
    )
    line = LogLine(
        stream=stream, text=text.decode("utf-8", errors="replace").rstrip("\r")
    )
    if ts is not None:
        line.time.CopyFrom(ts)
    return line


def parse_log_stream(raw: bytes, stream: int) -> list[LogLine]:
    """Split one stream's ``--timestamps`` output into tagged LogLines."""
    if not raw:
        return []
    parsed = (parse_log_line(chunk, stream) for chunk in raw.split(b"\n"))
    return [line for line in parsed if line is not None]


def merge_log_lines(
    stdout: Sequence[LogLine], stderr: Sequence[LogLine]
) -> list[LogLine]:
    """Interleave two streams by timestamp, keeping each stream's own order.

    A line without a timestamp (a continuation line, or a runtime that emitted
    none) inherits the previous timestamp in its own stream, so it sorts next to
    the line it belongs to instead of jumping to the front.
    """
    if not stderr:
        return list(stdout)
    if not stdout:
        return list(stderr)

    keyed: list[tuple[tuple[int, int], int, int, LogLine]] = []
    for stream_index, lines in enumerate((stdout, stderr)):
        carried = (0, 0)
        for position, line in enumerate(lines):
            if line.HasField("time"):
                carried = (line.time.seconds, line.time.nanos)
            keyed.append((carried, stream_index, position, line))
    keyed.sort(key=lambda item: (item[0], item[1], item[2]))
    return [item[3] for item in keyed]


def cap_log_lines(
    lines: Sequence[LogLine], *, max_lines: int, max_bytes: int
) -> tuple[list[LogLine], bool]:
    """Trim to the most recent window. Returns ``(lines, truncated)``.

    The OLDEST lines are dropped, so the caller always gets the tail -- which is
    what "tail the logs" means, and what a reader looking for the most recent
    failure needs.
    """
    truncated = False
    kept = list(lines)
    if max_lines > 0 and len(kept) > max_lines:
        kept = kept[-max_lines:]
        truncated = True
    if max_bytes > 0:
        total = 0
        start = len(kept)
        for index in range(len(kept) - 1, -1, -1):
            total += len(kept[index].text.encode("utf-8")) + 1
            if total > max_bytes:
                break
            start = index
        if start > 0:
            kept = kept[start:]
            truncated = True
    return kept, truncated
