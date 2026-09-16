"""Work out which container this process is running in.

Needed so the responder cannot be asked to stop or restart itself: that call
would kill the responder mid-flight, the caller would see a transport timeout
rather than an answer, and nothing would come back until the restart policy
fired -- if it has one.

Three strategies, tried in order. Each returns every spelling it can establish
(name, full id, short id) because callers address containers by name while
docker reports ids.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable, Iterable
from pathlib import Path

logger = logging.getLogger("container_control.selfid")

#: Docker bind-mounts /etc/hostname, /etc/hosts and /etc/resolv.conf out of
#: /var/lib/docker/containers/<full-id>/, so the id is sitting in mountinfo.
_CONTAINER_ID = re.compile(r"/containers/([0-9a-f]{64})[/\b]")
_CGROUP_ID = re.compile(r"\b([0-9a-f]{64})\b")

#: Set by this repo's compose file on its own service, as the last-resort route
#: that survives both host networking and a renamed container.
SELF_LABEL = "keelson.container_control.self"


def _spellings(name: str = "", container_id: str = "") -> set[str]:
    out = {value for value in (name, container_id) if value}
    if len(container_id) >= 12:
        out.add(container_id[:12])
    return out


def _read(path: str) -> str:
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def id_from_proc(
    mountinfo_path: str = "/proc/self/mountinfo",
    cgroup_path: str = "/proc/self/cgroup",
) -> str:
    """The full container id as read out of this process's own /proc, or "".

    Deliberately NOT ``$HOSTNAME``. That is the short container id only when
    Docker sets the container's hostname -- and every deployment of this
    interface runs ``network_mode: host``, where the container shares the host's
    UTS namespace and inherits the *host's* hostname. Trusting it there would
    silently resolve "self" to something that is not a container at all.
    """
    match = _CONTAINER_ID.search(_read(mountinfo_path))
    if match:
        return match.group(1)
    # cgroup v1 and non-systemd cgroup v2 carry the id too. On a namespaced
    # cgroup v2 this reads "0::/" and yields nothing, which is why it is second.
    match = _CGROUP_ID.search(_read(cgroup_path))
    return match.group(1) if match else ""


def resolve(
    explicit_name: str | None,
    *,
    lookup: Callable[[str], object] | None = None,
    list_by_label: Callable[[str], Iterable[object]] | None = None,
    mountinfo_path: str = "/proc/self/mountinfo",
    cgroup_path: str = "/proc/self/cgroup",
) -> tuple[frozenset[str], str]:
    """Return ``(identity, how)``; ``identity`` is empty when unresolved.

    ``lookup`` maps a name or id to a snapshot with ``.name``/``.id`` (normally
    ``DockerBackend.get``); ``list_by_label`` returns the containers carrying a
    label. Both are injected so this module stays independent of docker-py.
    """
    if explicit_name:
        identity = _spellings(name=explicit_name)
        if lookup is not None:
            try:
                snapshot = lookup(explicit_name)
                identity |= _spellings(snapshot.name, snapshot.id)
            except Exception:
                # A name that does not resolve today is still authoritative --
                # the operator said so, and the container may appear later.
                logger.debug(
                    "Could not look up --self-container-name %r",
                    explicit_name,
                    exc_info=True,
                )
        return frozenset(identity), "--self-container-name"

    container_id = id_from_proc(mountinfo_path, cgroup_path)
    if container_id:
        identity = _spellings(container_id=container_id)
        if lookup is not None:
            try:
                snapshot = lookup(container_id)
                identity |= _spellings(snapshot.name, snapshot.id)
            except Exception:
                logger.debug(
                    "Could not look up self id %s", container_id[:12], exc_info=True
                )
        return frozenset(identity), "/proc self-inspection"

    if list_by_label is not None:
        try:
            matches = list(list_by_label(f"{SELF_LABEL}=1"))
        except Exception:
            logger.debug("Could not list by %s", SELF_LABEL, exc_info=True)
            matches = []
        if len(matches) == 1:
            snapshot = matches[0]
            return (
                frozenset(_spellings(snapshot.name, snapshot.id)),
                f"{SELF_LABEL} label",
            )
        if len(matches) > 1:
            # Two candidates means the label is on something other than us as
            # well. Guessing here risks protecting the wrong container and
            # leaving this one stoppable.
            logger.warning(
                "%d containers carry %s=1; refusing to guess which one is me",
                len(matches),
                SELF_LABEL,
            )

    return frozenset(), ""
