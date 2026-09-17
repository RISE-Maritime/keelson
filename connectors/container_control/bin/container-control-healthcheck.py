#!/usr/bin/env python3

"""Container healthcheck probe.

``docker2keelson`` pings the Docker socket once at startup and exits if it cannot be
reached. That covers a container that never worked; it does not cover a socket
that goes away *afterwards* -- a daemon restart, a changed docker gid, a
revoked mount. In that state the process keeps serving, every RPC returns
UNAVAILABLE, and the container still reports healthy. This probe is what makes
that visible from outside.

Imports ONLY :mod:`container_control.backend` -- never zenoh -- so a probe
every 30 seconds does not pay to open a bus it has no use for. And it calls the very
same :meth:`DockerBackend.ping` the startup gate calls, so the two can never
disagree about what "reachable" means.

Note this is VISIBILITY, not recovery: ``restart: unless-stopped`` reacts to a
container *exiting*, not to it going unhealthy. Nothing here auto-heals.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Importable when run straight out of a checkout; guarded for the same reason as
# in docker2keelson.py.
_CONNECTOR_ROOT = Path(__file__).resolve().parent.parent
if (_CONNECTOR_ROOT / "container_control" / "__init__.py").is_file():
    sys.path.insert(0, str(_CONNECTOR_ROOT))

from container_control.backend import BackendError, DockerBackend  # noqa: E402

#: Client-side budget for the probe. Must stay below the compose healthcheck
#: `timeout`, so a hung daemon produces a readable message here rather
#: than an unexplained SIGKILL with empty Health.Log[].Output.
PING_TIMEOUT_S = 5


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="container-control-healthcheck",
        description=(
            "Exit 0 if the Docker socket answers, 1 with the reason on stderr if "
            "not. For a compose healthcheck on a docker2keelson container."
        ),
    )
    parser.add_argument(
        "--timeout-s",
        type=float,
        default=PING_TIMEOUT_S,
        help="Client-side budget for the ping (default: %(default)s).",
    )
    args = parser.parse_args(argv)
    try:
        DockerBackend(timeout=args.timeout_s).ping()
    except BackendError as exc:
        # Goes to `docker inspect --format '{{json .State.Health}}'`, which is
        # the only place anyone will read it.
        print(exc.message, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
