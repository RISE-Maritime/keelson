"""Collector behaviour against a faked psutil.

The interesting cases are all things that only happen on a machine the test
host is not: a Windows drive letter, a Linux thermal chip, a sealed macOS
volume.
"""

from types import SimpleNamespace

import psutil
import pytest

from pc.collectors import Sampler, collect_host_info, sanitise, strip_host_root


def values(readings, subject):
    return [r.value for r in readings if r.subject == subject]


def suffixes(readings, subject):
    return [r.source_suffix for r in readings if r.subject == subject]


# --- key sanitising -------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("/", "root"),
        ("", "root"),
        ("///", "root"),
        ("/mnt/data", "mnt_data"),
        ("C:\\", "c"),
        ("D:\\Data", "d_data"),
        ("/System/Volumes/Data", "system_volumes_data"),
        ("en0", "en0"),
        ("veth0@if12", "veth0_if12"),
        ("Wi-Fi", "wi-fi"),
        # Zenoh pattern syntax must never survive into a key
        ("weird*name?", "weird_name"),
        ("$store#1", "store_1"),
    ],
)
def test_sanitise(raw, expected):
    assert sanitise(raw) == expected


@pytest.mark.parametrize(
    "mountpoint,host_root,expected",
    [
        ("/host/root", "/host/root", "/"),
        ("/host/root/", "/host/root/", "/"),
        ("/host/root/var/log", "/host/root", "/var/log"),
        ("/var", None, "/var"),
        # A path outside the prefix is left alone rather than mangled
        ("/other", "/host/root", "/other"),
        # Not a prefix match despite the shared leading text
        ("/host/rootfs", "/host/root", "/host/rootfs"),
    ],
)
def test_strip_host_root(mountpoint, host_root, expected):
    assert strip_host_root(mountpoint, host_root) == expected


# --- host info, cpu, memory -----------------------------------------------


def test_host_info_boot_time_is_nanoseconds(monkeypatch):
    monkeypatch.setattr(psutil, "boot_time", lambda: 1_700_000_000.5)
    readings = collect_host_info()
    assert values(readings, "host_boot_time") == [1_700_000_000_500_000_000]
    assert len(values(readings, "host_name")) == 1


def test_cpu_and_memory_are_whole_host(monkeypatch):
    monkeypatch.setattr(psutil, "cpu_percent", lambda **kw: 12.5)
    monkeypatch.setattr(psutil, "virtual_memory", lambda: SimpleNamespace(percent=75.0))
    monkeypatch.setattr(psutil, "swap_memory", lambda: SimpleNamespace(percent=3.0))

    sampler = Sampler(disk=False, network=False, sensors=False)
    readings = sampler.sample()
    assert values(readings, "cpu_load_pct") == [12.5]
    assert values(readings, "memory_used_pct") == [75.0]
    assert values(readings, "swap_used_pct") == [3.0]
    assert {r.source_suffix for r in readings} == {""}


# --- disk -----------------------------------------------------------------


def test_windows_drive_letters_become_valid_key_chunks(monkeypatch):
    monkeypatch.setattr(
        psutil,
        "disk_partitions",
        lambda all=False: [
            SimpleNamespace(mountpoint="C:\\", fstype="NTFS", device="C:"),
            SimpleNamespace(mountpoint="D:\\Data", fstype="NTFS", device="D:"),
        ],
    )
    monkeypatch.setattr(
        psutil,
        "disk_usage",
        lambda mp: SimpleNamespace(free=250_107_862_016, percent=50.0),
    )

    readings = Sampler().collect_disk()
    assert suffixes(readings, "disk_used_pct") == ["disk/c", "disk/d_data"]
    assert values(readings, "disk_free_bytes") == [250_107_862_016] * 2


def test_disk_free_bytes_exceeds_int32(monkeypatch):
    """A disk does not fit in a TimestampedInt; this is the regression guard
    for the Int64 subject type pinned in test_pc_subjects.py."""
    monkeypatch.setattr(
        psutil, "disk_usage", lambda mp: SimpleNamespace(free=4 * 2**40, percent=1.0)
    )
    readings = Sampler(disk_mountpoints=["/data"]).collect_disk()
    assert values(readings, "disk_free_bytes") == [4 * 2**40]
    assert 4 * 2**40 > 2**31 - 1


def test_pseudo_filesystems_are_excluded(monkeypatch):
    monkeypatch.setattr(
        psutil,
        "disk_partitions",
        lambda all=False: [
            SimpleNamespace(mountpoint="/", fstype="ext4", device="/dev/sda1"),
            SimpleNamespace(mountpoint="/run", fstype="tmpfs", device="tmpfs"),
            SimpleNamespace(
                mountpoint="/snap/core", fstype="squashfs", device="/dev/loop0"
            ),
        ],
    )
    monkeypatch.setattr(
        psutil, "disk_usage", lambda mp: SimpleNamespace(free=60, percent=40.0)
    )
    assert suffixes(Sampler().collect_disk(), "disk_used_pct") == ["disk/root"]


def test_unreadable_mountpoint_is_skipped_once(monkeypatch):
    """macOS sealed volumes and container-visible host mounts raise. Re-probing
    them every cycle burns syscalls on something that will not start working."""
    calls = []

    def usage(mountpoint):
        calls.append(mountpoint)
        raise PermissionError("nope")

    monkeypatch.setattr(
        psutil,
        "disk_partitions",
        lambda all=False: [
            SimpleNamespace(mountpoint="/sealed", fstype="apfs", device="d")
        ],
    )
    monkeypatch.setattr(psutil, "disk_usage", usage)

    sampler = Sampler()
    assert sampler.collect_disk() == []
    assert sampler.collect_disk() == []
    assert calls == ["/sealed"]


def test_explicit_mountpoints_override_autodetection(monkeypatch):
    monkeypatch.setattr(
        psutil, "disk_partitions", lambda all=False: pytest.fail("should not enumerate")
    )
    monkeypatch.setattr(
        psutil, "disk_usage", lambda mp: SimpleNamespace(free=9, percent=10.0)
    )
    readings = Sampler(disk_mountpoints=["/data"]).collect_disk()
    assert suffixes(readings, "disk_used_pct") == ["disk/data"]


def test_host_root_prefix_is_stripped_from_labels(monkeypatch):
    monkeypatch.setattr(
        psutil, "disk_usage", lambda mp: SimpleNamespace(free=9, percent=10.0)
    )
    sampler = Sampler(
        disk_mountpoints=["/host/root", "/host/root/var"],
        host_root="/host/root",
    )
    assert suffixes(sampler.collect_disk(), "disk_used_pct") == [
        "disk/root",
        "disk/var",
    ]


# --- network --------------------------------------------------------------


def test_network_interface_up_per_nic(monkeypatch):
    monkeypatch.setattr(
        psutil,
        "net_if_stats",
        lambda: {
            "eth0": SimpleNamespace(isup=True, speed=1000),
            "wlan0": SimpleNamespace(isup=False, speed=0),
        },
    )
    readings = Sampler().collect_network()
    assert suffixes(readings, "network_interface_up") == ["net/eth0", "net/wlan0"]
    assert values(readings, "network_interface_up") == [True, False]


# --- sensors --------------------------------------------------------------
#
# monkeypatch passes raising=False because psutil defines sensors_temperatures
# only on Linux. On a macOS or Windows test host there is no attribute to
# replace -- which is the very platform difference collect_sensors() guards.


def _temp(label, current):
    return SimpleNamespace(label=label, current=current, high=None, critical=None)


def test_only_cpu_chips_are_published(monkeypatch):
    """One sensor, one subject: non-CPU chips would belong on
    integrated_circuit_temperature_celsius, which this connector does not
    publish, so they are dropped rather than mislabelled."""
    monkeypatch.setattr(
        psutil,
        "sensors_temperatures",
        raising=False,
        value=lambda: {
            "coretemp": [_temp("Package id 0", 51.0), _temp("Core 0", None)],
            "nvme": [_temp("Composite", 38.0)],
        },
    )

    readings = Sampler().collect_sensors()
    assert values(readings, "cpu_temperature_celsius") == [51.0]
    assert suffixes(readings, "cpu_temperature_celsius") == [
        "sensor/coretemp/package_id_0"
    ]
    assert {r.subject for r in readings} == {"cpu_temperature_celsius"}


def test_temperatures_absent_on_macos_and_windows(monkeypatch):
    """Nothing is published rather than a zero, which would read as a very
    cold CPU."""
    monkeypatch.delattr(psutil, "sensors_temperatures", raising=False)
    assert Sampler().collect_sensors() == []


# --- sample() resilience --------------------------------------------------


def test_a_failing_collector_does_not_cost_the_other_groups_their_cycle(monkeypatch):
    def boom():
        raise RuntimeError("kernel said no")

    sampler = Sampler(disk=False, network=False, sensors=False)
    monkeypatch.setattr(sampler, "collect_cpu", boom)
    monkeypatch.setattr(psutil, "virtual_memory", lambda: SimpleNamespace(percent=60.0))
    monkeypatch.setattr(psutil, "swap_memory", lambda: SimpleNamespace(percent=0.0))

    readings = sampler.sample()
    assert values(readings, "memory_used_pct") == [60.0]
    assert values(readings, "cpu_load_pct") == []


def test_disabled_groups_are_not_sampled(monkeypatch):
    monkeypatch.setattr(psutil, "cpu_percent", lambda **kw: pytest.fail("cpu disabled"))
    sampler = Sampler(cpu=False, memory=False, disk=False, network=False, sensors=False)
    sampler.prime()
    assert sampler.sample() == []
