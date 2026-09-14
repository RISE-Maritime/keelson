#!/usr/bin/env python3

"""Command line utility for monitoring the host it runs on and publishing its
health to Keelson/Zenoh.

Samples CPU load and temperature, memory and swap, disk usage and network
interface state with psutil, and publishes each quantity as its own Keelson
subject. Runs on Linux, macOS and Windows; metrics the platform does not expose
are simply not published rather than reported as zero.

Two cadences: --interval for live metrics, --info-interval for host identity,
which barely changes.

Publishes (source_id is the --source-id base plus an instance suffix):
  {realm}/@v0/{entity_id}/pubsub/host_name/{source_id}
  {realm}/@v0/{entity_id}/pubsub/host_boot_time/{source_id}
  {realm}/@v0/{entity_id}/pubsub/cpu_load_pct/{source_id}
  {realm}/@v0/{entity_id}/pubsub/cpu_temperature_celsius/{source_id}/sensor/{chip}/{label}
  {realm}/@v0/{entity_id}/pubsub/memory_used_pct/{source_id}
  {realm}/@v0/{entity_id}/pubsub/swap_used_pct/{source_id}
  {realm}/@v0/{entity_id}/pubsub/disk_used_pct/{source_id}/disk/{mount}
  {realm}/@v0/{entity_id}/pubsub/disk_free_bytes/{source_id}/disk/{mount}
  {realm}/@v0/{entity_id}/pubsub/network_interface_up/{source_id}/net/{nic}
"""

import argparse
import logging
import pathlib
import sys
import time

import psutil
import zenoh

from keelson.scaffolding import (
    GracefulShutdown,
    add_common_arguments,
    create_zenoh_config,
    declare_liveliness,
    setup_logging,
)

# Importable when run straight out of a checkout (`python bin/pc2keelson.py`).
# Guarded because the installed copy lives at /usr/local/bin, and inserting
# /usr/local unconditionally would put its lib/, bin/ and share/ directories at
# the front of sys.path as namespace packages, ahead of every real module.
_CONNECTOR_ROOT = pathlib.Path(__file__).resolve().parent.parent
if (_CONNECTOR_ROOT / "pc" / "__init__.py").is_file():
    sys.path.insert(0, str(_CONNECTOR_ROOT))

from pc.collectors import (  # noqa: E402
    DEFAULT_FSTYPE_EXCLUDE,
    SUBJECTS_BY_GROUP,
    Sampler,
    collect_host_info,
)
from pc.publishing import Publisher  # noqa: E402

logger = logging.getLogger("pc2keelson")

GROUPS = ("host_info", "cpu", "memory", "disk", "network", "sensors")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pc2keelson",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
        description="Monitor this computer and publish its health to Keelson/Zenoh",
    )

    add_common_arguments(parser)

    parser.add_argument(
        "-r",
        "--realm",
        type=str,
        required=True,
        help="Realm/base path to publish under, ex. rise",
    )
    parser.add_argument(
        "-e",
        "--entity-id",
        type=str,
        required=True,
        help="Unique id of the entity within the realm, ex. nuc01",
    )
    parser.add_argument(
        "-s",
        "--source-id",
        type=str,
        default="pc",
        help="Source-id base; per-instance suffixes are appended "
        "to it, ex. pc/disk/data",
    )

    parser.add_argument(
        "--interval",
        type=float,
        default=5.0,
        help="Seconds between samples of the live metrics",
    )
    parser.add_argument(
        "--info-interval",
        type=float,
        default=60.0,
        help="Seconds between publishes of host identity",
    )

    parser.add_argument(
        "--no-host-info",
        dest="host_info",
        action="store_false",
        help="Do not publish host name or boot time",
    )
    parser.add_argument(
        "--no-cpu", dest="cpu", action="store_false", help="Do not publish CPU load"
    )
    parser.add_argument(
        "--no-memory",
        dest="memory",
        action="store_false",
        help="Do not publish memory or swap usage",
    )
    parser.add_argument(
        "--no-disk", dest="disk", action="store_false", help="Do not publish disk usage"
    )
    parser.add_argument(
        "--no-network",
        dest="network",
        action="store_false",
        help="Do not publish network interface state",
    )
    parser.add_argument(
        "--no-sensors",
        dest="sensors",
        action="store_false",
        help="Do not publish CPU temperature",
    )

    parser.add_argument(
        "--disk-mountpoint",
        dest="disk_mountpoints",
        action="append",
        metavar="PATH",
        default=None,
        help="Report only this mountpoint; repeatable. Default is "
        "every real filesystem psutil reports",
    )
    parser.add_argument(
        "--disk-fstype-exclude",
        type=str,
        default=",".join(DEFAULT_FSTYPE_EXCLUDE),
        metavar="FSTYPES",
        help="Comma-separated filesystem types to skip when "
        "auto-detecting mountpoints",
    )

    parser.add_argument(
        "--procfs-path",
        type=str,
        default=None,
        metavar="PATH",
        help="Read /proc from here instead (Linux). Set this to the "
        "host's /proc when running in a container, ex. /host/proc",
    )
    parser.add_argument(
        "--host-root",
        type=str,
        default=None,
        metavar="PATH",
        help="Bind-mount prefix to strip from mountpoint labels so a "
        "containerised run names host paths as the host does, "
        "ex. /host/root",
    )

    return parser


def make_sampler(args: argparse.Namespace) -> Sampler:
    return Sampler(
        cpu=args.cpu,
        memory=args.memory,
        disk=args.disk,
        network=args.network,
        sensors=args.sensors,
        disk_mountpoints=args.disk_mountpoints,
        disk_fstype_exclude=[
            f.strip() for f in args.disk_fstype_exclude.split(",") if f.strip()
        ],
        host_root=args.host_root,
    )


def enabled_subjects(args: argparse.Namespace) -> list:
    """The subjects this run can publish, for its liveliness tokens.

    A token is a statement of capability, so a group switched off by flag is
    not advertised. Tokens sit on the --source-id base; per-instance source ids
    (disks, NICs, sensors) are discovered at runtime and share that prefix.
    """
    return sorted(
        subject
        for group in GROUPS
        if getattr(args, group)
        for subject in SUBJECTS_BY_GROUP[group]
    )


def run(session: zenoh.Session, args: argparse.Namespace, shutdown: GracefulShutdown):
    publisher = Publisher(session, args.realm, args.entity_id, args.source_id)
    sampler = make_sampler(args)
    sampler.prime()

    # Host info goes out immediately rather than waiting a whole info-interval,
    # so a subscriber that joins at startup learns what machine this is at once.
    next_info = 0.0

    while not shutdown.is_requested():
        timestamp_ns = time.time_ns()
        now = time.monotonic()

        readings = sampler.sample()
        if args.host_info and now >= next_info:
            readings.extend(collect_host_info())
            next_info = now + args.info_interval

        published = publisher.publish(readings, timestamp_ns)
        logger.debug("Published %d readings", published)

        # Interruptible: a plain sleep would hold SIGTERM for a whole interval.
        shutdown.wait(timeout=args.interval)

    publisher.undeclare()


def main() -> int:
    args = build_parser().parse_args()
    setup_logging(level=args.log_level)

    if args.interval <= 0 or args.info_interval <= 0:
        logger.error("--interval and --info-interval must be greater than 0")
        return 2

    if args.procfs_path:
        # Only meaningful on Linux; psutil ignores the attribute elsewhere.
        psutil.PROCFS_PATH = args.procfs_path
        logger.info("Reading procfs from %s", args.procfs_path)

    conf = create_zenoh_config(
        mode=args.mode,
        connect=args.connect,
        listen=args.listen,
        zenoh_config=args.zenoh_config,
    )

    logger.info("Opening Zenoh session...")
    with zenoh.open(conf) as session:
        with (
            declare_liveliness(
                session,
                args.realm,
                args.entity_id,
                args.source_id,
                pubsub_subjects=enabled_subjects(args),
            ),
            GracefulShutdown() as shutdown,
        ):
            try:
                run(session, args, shutdown)
            except KeyboardInterrupt:
                logger.info("Program ended due to user request (Ctrl-C)")

    logger.info("Shutdown complete")
    return 0


if __name__ == "__main__":
    sys.exit(main())
