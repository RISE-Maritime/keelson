"""Test doubles. No docker daemon and no zenoh session are involved anywhere."""

from __future__ import annotations

from dataclasses import dataclass, field

from keelson.interfaces.ErrorResponse_pb2 import ErrorResponse

from container_control.backend import BackendError
from container_control.model import ContainerSnapshot


def make_attrs(
    *,
    status: str = "running",
    created: str = "2026-01-02T03:04:05.123456789Z",
    started: str = "2026-01-02T03:04:06.000000000Z",
    finished: str = "0001-01-01T00:00:00Z",
    exit_code: int = 0,
    restart_policy: str = "unless-stopped",
    max_retries: int = 0,
    restart_count: int = 0,
    image: str = "ghcr.io/rise-maritime/thing:1.2.3",
    health: str | None = None,
    tty: bool = False,
    labels: dict | None = None,
    host_config: dict | None = None,
) -> dict:
    state: dict = {
        "Status": status,
        "StartedAt": started,
        "FinishedAt": finished,
        "ExitCode": exit_code,
    }
    if health is not None:
        state["Health"] = {"Status": health}
    return {
        "Created": created,
        "State": state,
        "RestartCount": restart_count,
        "Config": {"Image": image, "Tty": tty, "Labels": labels or {}},
        "HostConfig": {
            "RestartPolicy": {"Name": restart_policy, "MaximumRetryCount": max_retries},
            # The resource keys the Engine always reports, at the values it
            # reports for an UNCONSTRAINED container: 0 and "", never absent.
            # A test for "no limit configured" has to run against those exact
            # values, because that is what every container on an ordinary host
            # looks like.
            "NanoCpus": 0,
            "CpuQuota": 0,
            "CpuPeriod": 0,
            "CpuShares": 0,
            "CpusetCpus": "",
            "Memory": 0,
            **(host_config or {}),
        },
    }


def snapshot(
    name: str = "thing",
    container_id: str = "a" * 64,
    *,
    image_tags: tuple[str, ...] = ("ghcr.io/rise-maritime/thing:1.2.3",),
    image_id: str = "sha256:deadbeef",
    labels: dict | None = None,
    host_config: dict | None = None,
    **attr_kwargs,
) -> ContainerSnapshot:
    return ContainerSnapshot(
        name=name,
        id=container_id,
        attrs=make_attrs(labels=labels, host_config=host_config, **attr_kwargs),
        image_tags=image_tags,
        image_id=image_id,
        labels=labels or {},
    )


#: A live one-shot sample, trimmed. Captured from Engine API 1.55 on cgroup v2,
#: which is why `precpu_stats` is absent and `inactive_file` rather than
#: `total_inactive_file` is the reclaimable-cache key: that IS what a one-shot
#: call returns, and a fixture that quietly supplied a precpu block would test a
#: payload this responder never sees.
def stats_raw(
    *,
    cpu_total: int = 7_063_210_830_000,
    system_cpu: int = 755_412_800_000_000,
    online_cpus: int = 16,
    memory_usage: int = 26_820_608,
    inactive_file: int = 151_552,
    memory_limit: int = 33_364_172_800,
    pids_current: int = 9,
    pids_limit: int | None = 34_676,
    rx_bytes: int | None = 581_000,
    tx_bytes: int | None = 126,
    block_read: int | None = 16_474_112,
    block_write: int | None = 0,
    block_op_case: str = "lower",
    throttled_periods: int = 0,
    throttled_time: int = 0,
) -> dict:
    raw: dict = {
        "cpu_stats": {
            "cpu_usage": {"total_usage": cpu_total},
            "system_cpu_usage": system_cpu,
            "online_cpus": online_cpus,
            "throttling_data": {
                "periods": 0,
                "throttled_periods": throttled_periods,
                "throttled_time": throttled_time,
            },
        },
        "memory_stats": {
            "usage": memory_usage,
            "stats": {"inactive_file": inactive_file},
            "limit": memory_limit,
        },
        "pids_stats": {"current": pids_current},
        "blkio_stats": {},
    }
    if pids_limit is not None:
        raw["pids_stats"]["limit"] = pids_limit
    if rx_bytes is not None or tx_bytes is not None:
        raw["networks"] = {
            "eth0": {"rx_bytes": rx_bytes or 0, "tx_bytes": tx_bytes or 0}
        }
    if block_read is not None or block_write is not None:
        read_op, write_op = (
            ("read", "write") if block_op_case == "lower" else ("Read", "Write")
        )
        raw["blkio_stats"]["io_service_bytes_recursive"] = [
            {"major": 259, "minor": 2, "op": read_op, "value": block_read or 0},
            {"major": 259, "minor": 2, "op": write_op, "value": block_write or 0},
        ]
    return raw


#: What the Engine answers for a container that has stopped: 200, and a body
#: with the shape but none of the content. Published naively it is a row reading
#: "0% CPU, 0 bytes" for a container that is not running at all.
STOPPED_STATS_RAW = {
    "read": "0001-01-01T00:00:00Z",
    "cpu_stats": {"cpu_usage": {"total_usage": 0}, "throttling_data": {}},
    "memory_stats": {},
    "pids_stats": {},
    "blkio_stats": {"io_service_bytes_recursive": None},
}


class FakeBackend:
    """Records every call, so a test can assert that a refused action never
    reached the daemon at all."""

    def __init__(
        self, snapshots=(), raises: Exception | None = None, logs=(b"", b"", False)
    ):
        self.snapshots = list(snapshots)
        self.raises = raises
        self._logs = logs
        self.calls: list[tuple] = []
        #: Per-container raw samples, by id. A test advances a counter by
        #: rewriting the entry between ticks.
        self.stats_by_id: dict[str, dict] = {}
        #: Ids whose stats call should blow up, so "one container fails
        #: mid-sweep" is expressible without failing the whole listing.
        self.stats_raises: dict[str, Exception] = {}

    def _maybe_raise(self):
        if self.raises is not None:
            raise self.raises

    def list(self, *, running_only=False, label=None):
        self.calls.append(("list", running_only, label))
        self._maybe_raise()
        if running_only:
            return [
                s for s in self.snapshots if s.attrs["State"]["Status"] == "running"
            ]
        return list(self.snapshots)

    def get(self, name_or_id):
        self.calls.append(("get", name_or_id))
        self._maybe_raise()
        for s in self.snapshots:
            if name_or_id in (s.name, s.id):
                return s
        # Same shape the real backend produces, so handler tests exercise the
        # mapping the caller actually sees.
        raise BackendError(
            ErrorResponse.Code.NOT_FOUND, f"no such container: {name_or_id}"
        )

    def stats(self, name_or_id):
        self.calls.append(("stats", name_or_id))
        if name_or_id in self.stats_raises:
            raise self.stats_raises[name_or_id]
        self._maybe_raise()
        return self.stats_by_id.get(name_or_id, stats_raw())

    def logs(self, name, *, tail, since=None, want_stdout=True, want_stderr=True):
        self.calls.append(("logs", name, tail, since, want_stdout, want_stderr))
        self._maybe_raise()
        out, err, tty = self._logs
        return out, err, tty, self.get(name)

    def _act(self, verb, name, timeout_s=0):
        self.calls.append((verb, name, timeout_s))
        self._maybe_raise()
        current = self.get(name)
        # Flip the state so a test can prove the reply carries POST-action state.
        attrs = dict(current.attrs)
        attrs["State"] = dict(attrs["State"])
        attrs["State"]["Status"] = "exited" if verb == "stop" else "running"
        return ContainerSnapshot(
            name=current.name,
            id=current.id,
            attrs=attrs,
            image_tags=current.image_tags,
            image_id=current.image_id,
            labels=current.labels,
        )

    def start(self, name, timeout_s=0):
        return self._act("start", name, timeout_s)

    def stop(self, name, timeout_s=0):
        return self._act("stop", name, timeout_s)

    def restart(self, name, timeout_s=0):
        return self._act("restart", name, timeout_s)

    def remove(self, name, *, force=False, remove_volumes=False):
        """Mirrors the real backend's contract, refusal included.

        Returns ``(snapshot, was_running)`` and raises INVALID_STATE for a
        running container without ``force`` -- a fake that always succeeded
        would let the handler drop the running check and no test would notice.
        """
        self.calls.append(("remove", name, force, remove_volumes))
        self._maybe_raise()
        current = self.get(name)
        was_running = current.attrs["State"]["Status"] == "running"
        if was_running and not force:
            raise BackendError(
                ErrorResponse.Code.INVALID_STATE,
                f"{name!r} is running; stop it first, or repeat with force to "
                "kill and remove it in one step",
            )
        self.snapshots = [s for s in self.snapshots if s.name != current.name]
        return current, was_running


@dataclass
class FakeOp:
    """Stands in for keelson.scaffolding.rpc.RpcOp."""

    procedure: str = "list"
    request_bytes: bytes = b""
    reply_key: str = "test/key"
    ok: object = None
    err: tuple[str, int] | None = None
    replies: int = field(default=0)

    def reply_ok(self, response=b""):
        self.replies += 1
        self.ok = response

    def reply_err(self, description: str, code: int = ErrorResponse.Code.UNSPECIFIED):
        self.replies += 1
        self.err = (description, code)
