#!/usr/bin/env python3

"""Command-line client for container_control/v1 -- verification and ops.

Lists containers, tails logs and starts, stops, restarts or removes one, against
a docker2keelson responder.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import keelson
import zenoh
from google.protobuf.message import DecodeError
from keelson.interfaces.ErrorResponse_pb2 import ErrorResponse
from keelson.scaffolding import add_common_arguments, create_zenoh_config, setup_logging

from keelson.interfaces.ContainerControl_pb2 import (
    ContainerActionResponse,
    GetLogsRequest,
    GetLogsResponse,
    ListContainersRequest,
    ListContainersResponse,
    LogStream,
    LogStreamSelector,
    RemoveContainerRequest,
    RemoveContainerResponse,
    RestartContainerRequest,
    StartContainerRequest,
    StopContainerRequest,
)
from keelson.payloads.ContainerHost_pb2 import ContainerState

# Importable when run straight out of a checkout; guarded for the same reason as
# in docker2keelson.py.
_CONNECTOR_ROOT = Path(__file__).resolve().parent.parent
if (_CONNECTOR_ROOT / "container_control" / "__init__.py").is_file():
    sys.path.insert(0, str(_CONNECTOR_ROOT))

from container_control import INTERFACE, VERSION  # noqa: E402

logger = logging.getLogger("container-control-cli")

_STREAMS = {
    "both": LogStreamSelector.LOG_STREAM_SELECTOR_BOTH,
    "stdout": LogStreamSelector.LOG_STREAM_SELECTOR_STDOUT,
    "stderr": LogStreamSelector.LOG_STREAM_SELECTOR_STDERR,
}


def call(
    session: zenoh.Session,
    args: argparse.Namespace,
    procedure: str,
    request,
    response_cls,
):
    key = keelson.construct_rpc_key(
        args.realm, args.entity_id, INTERFACE, VERSION, procedure, args.source_id
    )
    logger.debug("GET %s", key)
    for reply in session.get(
        key, payload=request.SerializeToString(), timeout=args.timeout
    ):
        if reply.ok is not None:
            return response_cls.FromString(reply.ok.payload.to_bytes())
        sys.exit(describe_error(reply.err.payload.to_bytes()))
    sys.exit(f"No reply from {key} within {args.timeout}s -- is the responder running?")


def describe_error(raw: bytes) -> str:
    """Render an error reply.

    Not every error reply is one of ours: a timeout or a missing route produces
    a zenoh-level ReplyError carrying a plain string, and parsing that as a
    protobuf ErrorResponse raises a DecodeError that buries the actual problem
    under a traceback.
    """
    try:
        err = ErrorResponse.FromString(raw)
        return f"ERROR {ErrorResponse.Code.Name(err.code)}: {err.error_description}"
    except (DecodeError, ValueError):
        return f"ERROR (transport): {raw.decode('utf-8', errors='replace')}"


def _fmt_time(message, field: str) -> str:
    if not message.HasField(field):
        return "-"
    return getattr(message, field).ToDatetime().isoformat(sep=" ", timespec="seconds")


def print_containers(response: ListContainersResponse) -> None:
    if not response.control_enabled:
        print("responder is READ-ONLY (start/stop/restart will be refused)\n")
    elif not response.remove_enabled:
        # Said explicitly, because "control is on" is exactly the assumption
        # that would make a refused remove look like a bug.
        print("responder allows start/stop/restart but NOT remove\n")
    header = f"{'NAME':<34} {'STATE':<12} {'CTRL':<5} {'RM':<5} {'STARTED':<20} IMAGE"
    print(header)
    print("-" * len(header))
    for c in response.containers:
        state = ContainerState.Name(c.state).replace("CONTAINER_STATE_", "").lower()
        print(
            f"{c.name:<34} {state:<12} {'yes' if c.controllable else 'no':<5} "
            f"{'yes' if c.removable else 'no':<5} "
            f"{_fmt_time(c, 'started_at'):<20} {c.image}"
        )
    print(f"\n{len(response.containers)} container(s)")


def print_logs(response: GetLogsResponse) -> None:
    if response.truncated:
        print(f"[... older lines dropped; showing the last {len(response.lines)} ...]")
    for line in response.lines:
        stamp = _fmt_time(line, "time")
        tag = "E" if line.stream == LogStream.LOG_STREAM_STDERR else " "
        print(f"{stamp} {tag} {line.text}")


def print_action(verb: str, response: ContainerActionResponse) -> None:
    c = response.container
    state = ContainerState.Name(c.state).replace("CONTAINER_STATE_", "").lower()
    print(f"{verb} {c.name}: now {state} (restart_count={c.restart_count})")


def print_remove(response: RemoveContainerResponse) -> None:
    killed = " (was running; force killed it)" if response.force_applied else ""
    print(f"removed {response.name}: id {response.id[:12]} is gone{killed}")


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="container-control-cli",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
        description=__doc__,
    )
    add_common_arguments(parser)
    parser.add_argument("-r", "--realm", type=str, required=True)
    parser.add_argument("-e", "--entity-id", type=str, required=True)
    parser.add_argument("-s", "--source-id", type=str, required=True)
    # Generous by default: `stop` and `restart` legitimately take the
    # container's full SIGTERM grace period (10s unless --timeout-s says
    # otherwise), so a 10s client timeout races the very calls most likely to
    # be slow.
    parser.add_argument("--timeout", type=float, default=30.0)

    sub = parser.add_subparsers(dest="command", required=True)

    p_list = sub.add_parser("list", help="List containers on the responder's host.")
    p_list.add_argument("--running-only", action="store_true")
    p_list.add_argument("--name-glob", type=str, default="")

    p_logs = sub.add_parser("logs", help="Tail one container's logs.")
    p_logs.add_argument("--name", type=str, required=True)
    p_logs.add_argument(
        "--tail", type=int, default=0, help="0 uses the responder's default."
    )
    p_logs.add_argument("--stream", choices=sorted(_STREAMS), default="both")

    for verb in ("start", "stop", "restart"):
        p = sub.add_parser(verb, help=f"{verb.capitalize()} one container.")
        p.add_argument("--name", type=str, required=True)
        if verb != "start":
            p.add_argument("--timeout-s", type=int, default=0)

    p_remove = sub.add_parser(
        "remove",
        help="Delete one container. Not undoable by any other procedure here.",
    )
    p_remove.add_argument("--name", type=str, required=True)
    p_remove.add_argument(
        "--force",
        action="store_true",
        help="Kill it first if it is running. Without this a running container is refused.",
    )
    p_remove.add_argument(
        "--volumes",
        action="store_true",
        help="Also delete its ANONYMOUS volumes. Named volumes are never touched.",
    )

    args = parser.parse_args()
    setup_logging(level=args.log_level)

    zconf = create_zenoh_config(
        mode=args.mode,
        connect=args.connect,
        listen=args.listen,
        zenoh_config=args.zenoh_config,
    )
    with zenoh.open(zconf) as session:
        if args.command == "list":
            print_containers(
                call(
                    session,
                    args,
                    "list",
                    ListContainersRequest(
                        running_only=args.running_only, name_glob=args.name_glob
                    ),
                    ListContainersResponse,
                )
            )
        elif args.command == "logs":
            print_logs(
                call(
                    session,
                    args,
                    "logs",
                    GetLogsRequest(
                        name=args.name,
                        tail_lines=args.tail,
                        stream=_STREAMS[args.stream],
                    ),
                    GetLogsResponse,
                )
            )
        elif args.command == "remove":
            print_remove(
                call(
                    session,
                    args,
                    "remove",
                    RemoveContainerRequest(
                        name=args.name, force=args.force, remove_volumes=args.volumes
                    ),
                    RemoveContainerResponse,
                )
            )
        else:
            request_cls = {
                "start": StartContainerRequest,
                "stop": StopContainerRequest,
                "restart": RestartContainerRequest,
            }[args.command]
            kwargs = {"name": args.name}
            if args.command != "start":
                kwargs["timeout_s"] = args.timeout_s
            print_action(
                args.command,
                call(
                    session,
                    args,
                    args.command,
                    request_cls(**kwargs),
                    ContainerActionResponse,
                ),
            )


if __name__ == "__main__":
    main()
