#!/usr/bin/env python3

"""Keelson RPC responder exposing this host's containers as container_control/v1.

Serves six procedures -- list, logs, start, stop, restart, remove -- on the
keelson RPC key space:

    {realm}/@v0/{entity_id}/@rpc/container_control/v1/{procedure}/{source_id}

READ-ONLY BY DEFAULT. Mounting the Docker socket makes this process
root-equivalent on its host, so the mutating procedures refuse with
PERMISSION_DENIED until it is started with --allow-control and an explicit
--allow allow-list.

REMOVE IS OFF EVEN THEN. --allow-control covers the reversible verbs only;
`remove` needs --allow-remove GLOB on top of it. Upgrading a responder that has
had control enabled for months must not hand it the power to delete containers
because a new image happened to grow the procedure.
"""

from __future__ import annotations

import argparse
import logging
import sys
from contextlib import ExitStack
from pathlib import Path

import keelson
import zenoh
from keelson.scaffolding import (
    GracefulShutdown,
    add_common_arguments,
    create_zenoh_config,
    declare_liveliness,
    serve_rpc,
    setup_logging,
)

# Importable when run straight out of a checkout (`python bin/docker2keelson.py`).
# Guarded because the installed copy lives at /usr/local/bin, and inserting
# /usr/local unconditionally would put its lib/, bin/ and share/ directories at
# the front of sys.path as namespace packages, ahead of every real module.
_CONNECTOR_ROOT = Path(__file__).resolve().parent.parent
if (_CONNECTOR_ROOT / "container_control" / "__init__.py").is_file():
    sys.path.insert(0, str(_CONNECTOR_ROOT))

from container_control import (  # noqa: E402
    INTERFACE,
    VERSION,
    handlers,
    logs_follower,
    selfid,
    stats_publisher,
    status_publisher,
)
from container_control.backend import (  # noqa: E402
    BackendError,
    DockerBackend,
    snapshots_by_label,
)
from container_control.guard import ControlGuard  # noqa: E402

logger = logging.getLogger("docker2keelson")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="docker2keelson",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
        description=__doc__,
    )
    add_common_arguments(parser)

    parser.add_argument(
        "-r", "--realm", type=str, required=True, help="Keelson base path."
    )
    parser.add_argument("-e", "--entity-id", type=str, required=True)
    parser.add_argument(
        "-s", "--source-id", type=str, required=True, help="Responder id."
    )

    control = parser.add_argument_group("container control (off by default)")
    control.add_argument(
        "--allow-control",
        action="store_true",
        help=(
            "Permit start/stop/restart. Requires at least one --allow. Without "
            "it this responder answers list and logs only. Does NOT enable "
            "remove -- see --allow-remove."
        ),
    )
    control.add_argument(
        "--allow",
        metavar="GLOB",
        action="append",
        default=[],
        help=(
            "Container NAME glob that may be controlled; repeatable. Use '*' to "
            "mean every container, deliberately."
        ),
    )
    control.add_argument(
        "--allow-remove",
        metavar="GLOB",
        action="append",
        default=[],
        help=(
            "Container NAME glob that may be REMOVED; repeatable. Enabling this "
            "at all is what enables the remove procedure -- there is no separate "
            "boolean to get out of step with it. Requires --allow-control, and "
            "is matched independently of --allow, so 'restart anything, delete "
            "only the scratch containers' is sayable. Removal is the one action "
            "here no other call can undo."
        ),
    )
    control.add_argument(
        "--self-container-name",
        type=str,
        default=None,
        help=(
            "This responder's own container_name. Set it to the same literal as "
            "your compose file's container_name: so it can refuse to stop itself."
        ),
    )

    status = parser.add_argument_group("container status (on by default)")
    status.add_argument(
        "--publish-status",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=(
            "Publish the container set as the 'container_status' subject "
            "whenever it changes, so consoles subscribe instead of polling "
            "list() per host. ON BY DEFAULT, unlike --allow-control and "
            "--follow-logs: those are off because they are privileged (one "
            "mutates the host, the other republishes container stdout). This "
            "publishes precisely the bytes list() already hands to any bus "
            "participant who asks -- the same answer, on time, not a wider one."
        ),
    )
    status.add_argument(
        "--status-interval-s",
        type=float,
        default=5.0,
        help=(
            "How often the container set is snapshotted and compared. This is "
            "the worst-case latency of a state change reaching a console, and "
            "it must beat the poll it replaces to be an improvement."
        ),
    )
    status.add_argument(
        "--status-heartbeat-s",
        type=float,
        default=30.0,
        help=(
            "Republish unchanged state at most this often. Zenoh pub/sub does "
            "not backfill, so this bounds how stale a late subscriber's first "
            "value is."
        ),
    )

    stats = parser.add_argument_group("resource stats (off by default)")
    stats.add_argument(
        "--publish-stats",
        metavar="GLOB",
        action="append",
        default=[],
        help=(
            "Publish per-container CPU, memory, network, block I/O and CFS "
            "throttling as the 'container_stats' subject, for container names "
            "matching this glob; repeatable. Off entirely when unset. OFF BY "
            "DEFAULT, unlike --publish-status: that one republishes bytes "
            "list() already hands to any bus participant who asks, while this "
            "is new continuous telemetry -- every tick is a sample, by "
            "definition -- on a link that also carries navigation data."
        ),
    )
    stats.add_argument(
        "--stats-interval-s",
        type=float,
        default=10.0,
        help=(
            "How often every matching running container is sampled, and the "
            "window every rate on the wire is averaged over. Longer than "
            "--status-interval-s on purpose: a state change is news, a "
            "utilisation sample is a series."
        ),
    )

    capture = parser.add_argument_group("log capture (off by default)")
    capture.add_argument(
        "--follow-logs",
        metavar="GLOB",
        action="append",
        default=[],
        help=(
            "Follow the logs of containers whose NAME matches, publishing them "
            "as the 'log_message' subject so the fleet's MCAP recorder captures "
            "them. Repeatable. Deliberately independent of --allow-control: "
            "recording a container's output is a different decision from being "
            "allowed to restart it, and a read-only responder must still be "
            "able to capture."
        ),
    )
    capture.add_argument("--follow-rescan-s", type=float, default=10.0)
    capture.add_argument(
        "--follow-max-lines-per-s",
        type=int,
        default=200,
        help="Per-container ceiling. Excess is dropped and reported in-band.",
    )
    capture.add_argument("--follow-queue-size", type=int, default=10_000)
    capture.add_argument(
        "--follow-tail",
        type=int,
        default=0,
        help="Lines of history to replay when a follow starts. 0 starts at the end.",
    )

    limits = parser.add_argument_group("limits")
    limits.add_argument("--stop-timeout-s", type=int, default=10)
    limits.add_argument("--default-tail-lines", type=int, default=200)
    limits.add_argument("--max-tail-lines", type=int, default=5000)
    limits.add_argument("--max-log-bytes", type=int, default=1_000_000)

    return parser


def build_guard(args: argparse.Namespace, backend: DockerBackend) -> ControlGuard:
    """Resolve self-identity and assemble the guard, or exit."""
    if not args.allow_control:
        # Read-only needs no self-identity: nothing is controllable anyway.
        return ControlGuard(control_enabled=False)

    identity, how = selfid.resolve(
        args.self_container_name,
        lookup=backend.get,
        list_by_label=lambda label: snapshots_by_label(backend, label),
    )
    if not identity:
        sys.exit(
            "Cannot determine which container is my own, and --allow-control is set:\n"
            "a stop/restart of this container would kill the responder mid-call and it\n"
            "would not come back until its restart policy fired. Pass\n"
            "--self-container-name <the container_name: from your compose file>."
        )

    logger.info(
        "Self-container resolved via %s as: %s", how, ", ".join(sorted(identity))
    )
    return ControlGuard(
        control_enabled=True,
        allow_globs=tuple(args.allow),
        remove_globs=tuple(args.allow_remove),
        self_identity=identity,
    )


def _log_addresses(args: argparse.Namespace, published_subjects: list[str]) -> None:
    """Print, at startup, every key this process answers or publishes on.

    WHY THIS IS WORTH FOUR LOG LINES. When a console does not list this host,
    the question is always the same -- "is it not running, or is the console not
    finding it?" -- and answering it used to mean querying the bus by hand.
    Worse, the usual answer is the second, and it is INVISIBLE: a console
    discovers a responder from its RPC-interface liveliness token, and a token
    that does not arrive renders as nothing at all. No error, no row, no
    explanation. One deployment sat in exactly that state while publishing
    container_status every five seconds.

    So the first thing an operator reaches for -- ``docker logs`` -- now names
    the keys, and the entity/source in them can be compared against what the
    console is looking for without touching zenoh.

    Built with keelson's own key constructors, never an f-string: the RPC key
    gained interface and version chunks in 0.6.0, and hand-built keys are how
    consumers end up addressing a shape the responder never served.
    """
    logger.info(
        "Serving %s/%s at %s",
        INTERFACE,
        VERSION,
        # Positional, exactly as cli.py calls it -- the keyword names differ
        # from construct_pubsub_key's and guessing them is a TypeError at
        # startup, on the one code path that exists to help someone debugging.
        keelson.construct_rpc_key(
            args.realm, args.entity_id, INTERFACE, VERSION, "*", args.source_id
        ),
    )
    for subject in published_subjects:
        logger.info(
            "Publishing %s at %s",
            subject,
            keelson.construct_pubsub_key(
                base_path=args.realm,
                entity_id=args.entity_id,
                subject=subject,
                source_id=args.source_id,
            ),
        )
    if not published_subjects:
        logger.warning(
            "Publishing nothing. This host is then discoverable ONLY by its RPC "
            "liveliness token or by being written into a console's registry by "
            "hand, and a token that fails to propagate is invisible. "
            "--publish-status is on by default; something turned it off."
        )


def run(
    session: zenoh.Session, args: argparse.Namespace, ctx: handlers.Context
) -> None:
    procedures, summarizers = handlers.build(ctx)

    # A subject-level token per published subject, and only when we actually
    # publish -- a responder that captures nothing must not advertise that it
    # might. (serve_rpc declares the interface-level token itself, so it is not
    # passed here.)
    published_subjects = []
    if args.publish_status:
        published_subjects.append(status_publisher.SUBJECT)
    if args.publish_stats:
        published_subjects.append(stats_publisher.SUBJECT)
    if args.follow_logs:
        published_subjects.append(logs_follower.SUBJECT)

    _log_addresses(args, published_subjects)

    with declare_liveliness(
        session,
        args.realm,
        args.entity_id,
        args.source_id,
        pubsub_subjects=published_subjects,
    ):
        serve_rpc(
            session,
            base_path=args.realm,
            entity_id=args.entity_id,
            responder_id=args.source_id,
            interface=INTERFACE,
            version=VERSION,
            handlers=procedures,
            summarizers=summarizers,
            log=logger,
        )

        if ctx.guard.control_enabled:
            logger.warning(
                "Container control is ENABLED for names matching: %s",
                ", ".join(ctx.guard.allow_globs),
            )
        else:
            logger.info(
                "Read-only: start/stop/restart will reply PERMISSION_DENIED. "
                "Pass --allow-control with one or more --allow GLOB to enable them."
            )

        # Its own line at its own level. Control is recoverable and removal is
        # not, so an operator reading a startup log should not have to infer the
        # destructive half from the sentence above it.
        if ctx.guard.remove_enabled:
            logger.warning(
                "Container REMOVAL is ENABLED for names matching: %s -- these can be "
                "deleted over the bus, and no other procedure undoes that.",
                ", ".join(ctx.guard.remove_globs),
            )
        else:
            logger.info(
                "Removal is off: remove will reply PERMISSION_DENIED. "
                "Pass --allow-remove GLOB (with --allow-control) to enable it."
            )

        # Inside the liveliness context, so the subject token is up before the
        # first line is published and comes down after the last.
        with ExitStack() as stack:
            if args.publish_status:
                stack.enter_context(
                    status_publisher.ContainerStatusPublisher(
                        ctx.backend,
                        ctx.guard,
                        session,
                        base_path=args.realm,
                        entity_id=args.entity_id,
                        source_id=args.source_id,
                        interval_s=args.status_interval_s,
                        heartbeat_s=args.status_heartbeat_s,
                    )
                )
            if args.publish_stats:
                stack.enter_context(
                    stats_publisher.ContainerStatsPublisher(
                        ctx.backend,
                        session,
                        base_path=args.realm,
                        entity_id=args.entity_id,
                        source_id=args.source_id,
                        globs=tuple(args.publish_stats),
                        interval_s=args.stats_interval_s,
                    )
                )
            if args.follow_logs:
                stack.enter_context(
                    logs_follower.LogFollower(
                        ctx.backend,
                        session,
                        base_path=args.realm,
                        entity_id=args.entity_id,
                        source_id=args.source_id,
                        globs=tuple(args.follow_logs),
                        rescan_s=args.follow_rescan_s,
                        max_lines_per_s=args.follow_max_lines_per_s,
                        queue_size=args.follow_queue_size,
                        tail=args.follow_tail,
                    )
                )

            # zenoh serves the queryables on its own callback threads; this
            # thread exists only to hold the session open until asked to stop.
            # SIGTERM (what `docker stop` sends) reaches GracefulShutdown, so
            # the liveliness tokens are retracted instead of expiring by lease.
            with GracefulShutdown() as shutdown:
                while not shutdown.is_requested():
                    shutdown.wait(1.0)

    logger.info("Shutting down")


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    # Fail at startup, not on the first refused call: a responder started with
    # --allow-control and no globs would look enabled and refuse everything.
    if args.allow_control and not args.allow:
        parser.error("--allow-control requires at least one --allow GLOB")

    # Removal without control is not a posture anyone wants, and it would skip
    # build_guard's self-identity resolution -- leaving the responder able to
    # delete its own container.
    if args.allow_remove and not args.allow_control:
        parser.error("--allow-remove requires --allow-control")

    setup_logging(level=args.log_level)
    zenoh.init_log_from_env_or(logging.getLevelName(args.log_level))

    backend = DockerBackend()
    try:
        backend.ping()
    except BackendError as exc:
        sys.exit(
            f"{exc.message}\n"
            "This responder needs the Docker socket: mount "
            "/var/run/docker.sock.\nWhen running as a non-root user, also add the "
            "socket's group (compose group_add: DOCKER_GID).\nSee "
            "connectors/container_control/README.md."
        )

    ctx = handlers.Context(
        backend=backend,
        guard=build_guard(args, backend),
        limits=handlers.Limits(
            stop_timeout_s=args.stop_timeout_s,
            default_tail_lines=args.default_tail_lines,
            max_tail_lines=args.max_tail_lines,
            max_log_bytes=args.max_log_bytes,
        ),
    )

    zconf = create_zenoh_config(
        mode=args.mode,
        connect=args.connect,
        listen=args.listen,
        zenoh_config=args.zenoh_config,
    )

    logger.info("Opening Zenoh session...")
    with zenoh.open(zconf) as session:
        try:
            run(session, args, ctx)
        except KeyboardInterrupt:
            logger.info("Shutting down on user request")


if __name__ == "__main__":
    main()
