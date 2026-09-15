"""Argument parsing, the wiring from flags to a Sampler, and liveliness."""

import pytest

from pc.collectors import DEFAULT_FSTYPE_EXCLUDE, EMITTED_SUBJECTS


def parse(pc2keelson, *argv):
    return pc2keelson.build_parser().parse_args(["-r", "rise", "-e", "nuc01", *argv])


def test_realm_and_entity_are_required(pc2keelson):
    with pytest.raises(SystemExit):
        pc2keelson.build_parser().parse_args([])


def test_defaults(pc2keelson):
    args = parse(pc2keelson)
    assert args.source_id == "pc"
    assert args.interval == 5.0
    assert args.info_interval == 60.0
    assert args.disk_mountpoints is None
    assert args.procfs_path is None
    assert args.host_root is None
    assert all(getattr(args, group) for group in pc2keelson.GROUPS)


@pytest.mark.parametrize(
    "flag,attribute",
    [
        ("--no-host-info", "host_info"),
        ("--no-cpu", "cpu"),
        ("--no-memory", "memory"),
        ("--no-disk", "disk"),
        ("--no-network", "network"),
        ("--no-sensors", "sensors"),
    ],
)
def test_every_group_can_be_disabled(pc2keelson, flag, attribute):
    assert getattr(parse(pc2keelson, flag), attribute) is False


def test_zenoh_config_flag_is_available(pc2keelson):
    args = parse(pc2keelson, "--zenoh-config", "/tmp/local.json5")
    assert args.zenoh_config == "/tmp/local.json5"


def test_disk_mountpoint_is_repeatable(pc2keelson):
    args = parse(pc2keelson, "--disk-mountpoint", "/", "--disk-mountpoint", "/data")
    assert args.disk_mountpoints == ["/", "/data"]


def test_make_sampler_passes_the_flags_through(pc2keelson):
    args = parse(
        pc2keelson,
        "--no-network",
        "--disk-mountpoint",
        "/data",
        "--host-root",
        "/host/root",
    )
    sampler = pc2keelson.make_sampler(args)
    assert sampler.network is False
    assert sampler.disk_mountpoints == ["/data"]
    assert sampler.host_root == "/host/root"


def test_fstype_exclusions_are_split_on_commas(pc2keelson):
    sampler = pc2keelson.make_sampler(parse(pc2keelson))
    assert sampler.disk_fstype_exclude == set(DEFAULT_FSTYPE_EXCLUDE)

    sampler = pc2keelson.make_sampler(
        parse(pc2keelson, "--disk-fstype-exclude", "tmpfs, overlay ,")
    )
    assert sampler.disk_fstype_exclude == {"tmpfs", "overlay"}


def test_liveliness_advertises_every_subject_by_default(pc2keelson):
    assert set(pc2keelson.enabled_subjects(parse(pc2keelson))) == EMITTED_SUBJECTS


def test_liveliness_omits_disabled_groups(pc2keelson):
    """A token is a statement of capability; a switched-off group has none."""
    subjects = pc2keelson.enabled_subjects(
        parse(pc2keelson, "--no-disk", "--no-sensors")
    )
    assert "disk_used_pct" not in subjects
    assert "disk_free_bytes" not in subjects
    assert "cpu_temperature_celsius" not in subjects
    assert "cpu_load_pct" in subjects


@pytest.mark.parametrize("argv", [["--interval", "0"], ["--info-interval", "-1"]])
def test_invalid_values_exit_nonzero_without_opening_a_session(
    pc2keelson, monkeypatch, argv
):
    monkeypatch.setattr("sys.argv", ["pc2keelson", "-r", "rise", "-e", "nuc01", *argv])
    monkeypatch.setattr(
        pc2keelson.zenoh,
        "open",
        lambda *a, **kw: pytest.fail("must not open a session"),
    )
    assert pc2keelson.main() == 2
