"""Who is allowed to start, stop, restart or remove what.

Mounting the Docker socket makes this process root-equivalent on its host:
anything that can reach the Engine API through it can start a privileged
container with ``/`` bind-mounted. The previous version of this interface
exposed that to any peer that could reach one zenoh key, with no check of any
kind. This module is the check.

TWO GATES, NOT ONE. ``--allow-control`` covers the reversible verbs; ``remove``
has its own switch and its own allow-list. The distinction is not fussiness: a
deployment that turned control on months ago must not silently acquire the
power to delete containers the day it pulls a new image, and the operator who
wants "restart anything, delete nothing" -- which is nearly everyone -- has no
way to express that with a single flag.

Pure -- imports neither ``docker`` nor ``zenoh`` -- so the policy is testable
without a daemon or a bus.
"""

from __future__ import annotations

from dataclasses import dataclass

from keelson.interfaces.ErrorResponse_pb2 import ErrorResponse

from .model import matches_any


@dataclass(frozen=True)
class Decision:
    """The answer to "may I mutate this container?".

    ``reason`` names *which* guard fired, because the operator's next action
    differs: turn the responder's control on, widen its allow-list, or stop
    trying to restart the thing that would answer them.
    """

    allowed: bool
    reason: str = ""
    code: int = ErrorResponse.Code.PERMISSION_DENIED


ALLOWED = Decision(allowed=True)


@dataclass(frozen=True)
class ControlGuard:
    """Read-only unless told otherwise, then only for named containers."""

    #: False (the default) makes start/stop/restart reply PERMISSION_DENIED.
    control_enabled: bool = False
    #: fnmatch globs against the container NAME. Never empty when control is on:
    #: ``app.py`` rejects that combination at startup rather than presenting a
    #: responder that looks enabled and refuses everything.
    allow_globs: tuple[str, ...] = ()
    #: fnmatch globs naming what ``remove`` may delete. EMPTY IS THE SWITCH:
    #: there is no separate ``remove_enabled`` boolean to disagree with it, so
    #: the failure mode ``--allow-control`` needed a startup check for -- enabled
    #: but matching nothing -- cannot be expressed here at all.
    #:
    #: Independent of :attr:`allow_globs` rather than a subset of it: "restart
    #: anything, delete only the scratch containers" is the configuration people
    #: actually want, and it is unsayable if one list has to contain the other.
    remove_globs: tuple[str, ...] = ()
    #: Every resolved spelling of this responder's own container -- explicit
    #: name, full id, short id. See :mod:`selfid`.
    self_identity: frozenset[str] = frozenset()

    @property
    def remove_enabled(self) -> bool:
        """What ``ListContainersResponse.remove_enabled`` should say.

        Derived, not stored. ``remove`` additionally requires control to be on
        (``app.py`` refuses the combination at startup), so this reads both.
        """
        return self.control_enabled and bool(self.remove_globs)

    def decide(
        self, name: str, container_id: str = "", verb: str = "control"
    ) -> Decision:
        """Whether ``verb`` -- one of the reversible ones -- may be performed.

        Evaluated *before* the container is looked up, so a denied call cannot
        be used to probe whether a given container exists on the host.
        """
        if not self.control_enabled:
            return Decision(
                allowed=False,
                reason=(
                    "container control is disabled on this responder "
                    "(start it with --allow-control)"
                ),
            )
        return self._against(self.allow_globs, "control", name, container_id, verb)

    def decide_remove(self, name: str, container_id: str = "") -> Decision:
        """Whether ``remove`` may delete the named container.

        Never falls through to :meth:`decide`. Sharing the code would be sharing
        the allow-list, and the whole point of this method is that the two lists
        are different questions.
        """
        if not self.control_enabled:
            return Decision(
                allowed=False,
                reason=(
                    "container control is disabled on this responder "
                    "(start it with --allow-control and --allow-remove)"
                ),
            )

        if not self.remove_globs:
            return Decision(
                allowed=False,
                reason=(
                    "container removal is disabled on this responder; enabling "
                    "start/stop/restart does not enable it "
                    "(start it with --allow-remove GLOB)"
                ),
            )

        return self._against(self.remove_globs, "remove", name, container_id, "remove")

    def _against(
        self,
        globs: tuple[str, ...],
        list_name: str,
        name: str,
        container_id: str,
        verb: str,
    ) -> Decision:
        if self.is_self(name, container_id):
            return Decision(
                allowed=False,
                reason=(
                    f"refusing to {verb} {name!r}: that is this responder's own "
                    "container, and stopping it would kill this call mid-flight"
                ),
            )

        if not matches_any(name, globs):
            allowed = ", ".join(globs) or "<none>"
            return Decision(
                allowed=False,
                reason=(
                    f"{name!r} is not in this responder's {list_name} allow-list ({allowed})"
                ),
            )

        return ALLOWED

    def is_self(self, name: str, container_id: str = "") -> bool:
        if name and name in self.self_identity:
            return True
        if container_id and container_id in self.self_identity:
            return True
        # Callers see the full 64-hex id; --self-container-name may have been
        # resolved from a short id, or vice versa.
        return bool(container_id) and any(
            len(known) >= 12
            and (container_id.startswith(known) or known.startswith(container_id))
            for known in self.self_identity
        )

    def controllable(self, name: str, container_id: str = "") -> bool:
        """What ``ContainerInfo.controllable`` should say for this container.

        Kept in terms of :meth:`decide` so the flag a client greys a button out
        on cannot drift from the answer it would actually get.
        """
        return self.decide(name, container_id).allowed

    def removable(self, name: str, container_id: str = "") -> bool:
        """What ``ContainerInfo.removable`` should say for this container.

        In terms of :meth:`decide_remove` for the same reason, and reported
        separately from :meth:`controllable` because a client that conflates
        them offers a Remove button that every call refuses.
        """
        return self.decide_remove(name, container_id).allowed
