"""Host health collection.

Every collector returns ``list[Reading]``. Nothing in this module imports zenoh
or keelson, so all of it is unit-testable by monkeypatching ``psutil``.

A ``Reading`` names a subject and the source-id suffix that distinguishes the
instance it came from; the payload type is resolved later from the subject
registry, so collectors never mention protobuf.

Scope is deliberately narrow: what a vessel-level consumer needs to make a
decision (is the box healthy, how long can it keep recording). Finer-grained
host metrics are the job of node-exporter or an equivalent running beside
Keelson, not of the bus.
"""

import logging
import platform
import re
from typing import Any, Dict, FrozenSet, List, NamedTuple, Optional, Sequence

import psutil

logger = logging.getLogger("pc2keelson")


class Reading(NamedTuple):
    """One measurement bound for one Keelson key.

    ``source_suffix`` is appended to the connector's ``--source-id`` base to
    form the source-id, so a whole-host reading leaves it empty and a per-disk
    reading carries ``disk/data``.
    """

    subject: str
    source_suffix: str
    value: Any


# Pseudo- and virtual filesystems. Without this a Linux host reports dozens of
# mounts that carry no storage anyone can run out of.
DEFAULT_FSTYPE_EXCLUDE = (
    "autofs",
    "binfmt_misc",
    "bpf",
    "cgroup",
    "cgroup2",
    "configfs",
    "debugfs",
    "devfs",
    "devpts",
    "devtmpfs",
    "fusectl",
    "hugetlbfs",
    "mqueue",
    "overlay",
    "proc",
    "pstore",
    "ramfs",
    "securityfs",
    "squashfs",
    "sysfs",
    "tmpfs",
    "tracefs",
)

# Sensor chips whose readings are the CPU package/core temperature. Other chips
# (NVMe, chipset, GPU) are not published: they would belong on
# integrated_circuit_temperature_celsius, which is outside this connector's
# scope, and one sensor must never appear on both subjects.
CPU_TEMP_CHIPS = frozenset(
    {
        "acpitz",
        "coretemp",
        "cpu-thermal",
        "cpu_thermal",
        "k10temp",
        "k8temp",
        "soc_thermal",
        "zenpower",
    }
)

# Loopback is always up and says nothing about connectivity; publishing it
# would put a permanently-green NIC on every host.
DEFAULT_NIC_EXCLUDE = ("lo", "lo0")

_UNSAFE_KEY_CHARS = re.compile(r"[^a-z0-9_-]+")


def sanitise(value: str, *, fallback: str = "unknown") -> str:
    """Reduce an arbitrary label to a single safe Keelson key chunk.

    Zenoh treats ``/`` as a separator and ``* ? $ #`` as pattern syntax, so a
    mountpoint or interface name cannot be dropped into a key as-is. ``/``
    becomes ``root``, ``/mnt/data`` becomes ``mnt_data``, and on Windows
    ``C:\\`` becomes ``c``.
    """
    lowered = value.strip().lower().replace("\\", "/").strip("/")
    if not lowered:
        return "root"
    cleaned = _UNSAFE_KEY_CHARS.sub("_", lowered).strip("_")
    return cleaned or fallback


def strip_host_root(mountpoint: str, host_root: Optional[str]) -> str:
    """Undo a bind-mount prefix so a containerised run labels host paths the way
    the host sees them: with ``--host-root /host/root``, ``/host/root/var``
    reports as ``/var`` and ``/host/root`` itself as ``/``."""
    if not host_root:
        return mountpoint
    root = host_root.rstrip("/")
    if not root or mountpoint == root:
        return "/"
    if mountpoint.startswith(root + "/"):
        return mountpoint[len(root) :]
    return mountpoint


def collect_host_info() -> List[Reading]:
    """Host identity. Near-constant for the life of the process, so it rides
    the slow ``--info-interval`` cadence."""
    return [
        Reading("host_name", "", platform.node() or "unknown"),
        Reading("host_boot_time", "", int(psutil.boot_time() * 1e9)),
    ]


class Sampler:
    """Samples the enabled live metric groups.

    A class rather than free functions because two things carry state across
    cycles: ``psutil.cpu_percent(interval=None)`` reports utilisation *since the
    previous call* and returns ``0.0`` the first time, so ``prime()`` makes that
    throwaway call at startup; and mountpoints that raised once are remembered
    and not re-probed.
    """

    def __init__(
        self,
        *,
        cpu: bool = True,
        memory: bool = True,
        disk: bool = True,
        network: bool = True,
        sensors: bool = True,
        disk_mountpoints: Optional[Sequence[str]] = None,
        disk_fstype_exclude: Sequence[str] = DEFAULT_FSTYPE_EXCLUDE,
        nic_exclude: Sequence[str] = DEFAULT_NIC_EXCLUDE,
        host_root: Optional[str] = None,
    ):
        self.cpu = cpu
        self.memory = memory
        self.disk = disk
        self.network = network
        self.sensors = sensors
        self.disk_mountpoints = list(disk_mountpoints or [])
        self.disk_fstype_exclude = {f.lower() for f in disk_fstype_exclude}
        self.nic_exclude = {n.lower() for n in nic_exclude}
        self.host_root = host_root

        # Mountpoints that raised once. Re-probing them every cycle just burns
        # syscalls and log lines on something that will not start working.
        self._skip_mountpoints: set = set()

    # -- lifecycle ---------------------------------------------------------

    def prime(self) -> None:
        """Take the throwaway first reading of the since-last-call CPU counter."""
        if self.cpu:
            psutil.cpu_percent(interval=None)

    def sample(self) -> List[Reading]:
        """One cycle of every enabled live metric group.

        A failure in one group must not cost the others their cycle, so each is
        guarded separately and logged rather than raised.
        """
        readings: List[Reading] = []
        for enabled, collect, name in (
            (self.cpu, self.collect_cpu, "cpu"),
            (self.memory, self.collect_memory, "memory"),
            (self.disk, self.collect_disk, "disk"),
            (self.network, self.collect_network, "network"),
            (self.sensors, self.collect_sensors, "sensors"),
        ):
            if not enabled:
                continue
            try:
                readings.extend(collect())
            except Exception:  # pylint: disable=broad-except
                logger.exception("Collector %r failed; skipping this cycle", name)
        return readings

    # -- collectors ----------------------------------------------------------

    def collect_cpu(self) -> List[Reading]:
        return [Reading("cpu_load_pct", "", psutil.cpu_percent(interval=None))]

    def collect_memory(self) -> List[Reading]:
        return [
            Reading("memory_used_pct", "", psutil.virtual_memory().percent),
            Reading("swap_used_pct", "", psutil.swap_memory().percent),
        ]

    def _mountpoints(self) -> List[str]:
        if self.disk_mountpoints:
            return self.disk_mountpoints
        try:
            partitions = psutil.disk_partitions(all=False)
        except OSError:
            logger.exception("Could not enumerate disk partitions")
            return []
        return [
            p.mountpoint
            for p in partitions
            if (p.fstype or "").lower() not in self.disk_fstype_exclude
        ]

    def collect_disk(self) -> List[Reading]:
        readings: List[Reading] = []
        for mountpoint in self._mountpoints():
            if mountpoint in self._skip_mountpoints:
                continue
            try:
                usage = psutil.disk_usage(mountpoint)
            except OSError as exc:
                # Unreadable mounts are normal: macOS puts firmlinks and
                # sealed volumes in the partition list, and a container sees
                # host mounts it has no business reading.
                logger.debug("Skipping mountpoint %s: %s", mountpoint, exc)
                self._skip_mountpoints.add(mountpoint)
                continue
            suffix = f"disk/{sanitise(strip_host_root(mountpoint, self.host_root))}"
            readings.append(Reading("disk_used_pct", suffix, usage.percent))
            readings.append(Reading("disk_free_bytes", suffix, usage.free))
        return readings

    def collect_network(self) -> List[Reading]:
        try:
            stats = psutil.net_if_stats()
        except OSError:
            logger.debug("Network interface stats unavailable", exc_info=True)
            return []
        return [
            Reading("network_interface_up", f"net/{sanitise(nic)}", bool(stat.isup))
            for nic, stat in stats.items()
            if nic.lower() not in self.nic_exclude
        ]

    def collect_sensors(self) -> List[Reading]:
        """CPU temperatures.

        psutil defines ``sensors_temperatures`` only on Linux (and FreeBSD), so
        this checks for the attribute rather than swallowing an exception. On
        macOS and Windows the subject simply goes unpublished, which is the
        honest answer; publishing 0.0 would look like a very cold CPU.
        """
        if not hasattr(psutil, "sensors_temperatures"):
            return []

        readings: List[Reading] = []
        for chip, entries in (psutil.sensors_temperatures() or {}).items():
            if chip.lower() not in CPU_TEMP_CHIPS:
                continue
            for index, entry in enumerate(entries):
                if entry.current is None:
                    continue
                label = sanitise(entry.label or f"{chip}_{index}")
                readings.append(
                    Reading(
                        "cpu_temperature_celsius",
                        f"sensor/{sanitise(chip)}/{label}",
                        entry.current,
                    )
                )
        return readings


# The subjects each metric group can emit -- capability, not activity: a host
# with no thermal sensor still declares cpu_temperature_celsius. This feeds the
# per-subject liveliness tokens, and tests/test_pc_subjects.py holds it to the
# code above so it cannot drift.
SUBJECTS_BY_GROUP: Dict[str, FrozenSet[str]] = {
    "host_info": frozenset({"host_name", "host_boot_time"}),
    "cpu": frozenset({"cpu_load_pct"}),
    "memory": frozenset({"memory_used_pct", "swap_used_pct"}),
    "disk": frozenset({"disk_used_pct", "disk_free_bytes"}),
    "network": frozenset({"network_interface_up"}),
    "sensors": frozenset({"cpu_temperature_celsius"}),
}

EMITTED_SUBJECTS: FrozenSet[str] = frozenset().union(*SUBJECTS_BY_GROUP.values())
