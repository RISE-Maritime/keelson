"""Shared fixtures for container_control connector tests."""

import os
import sys
from pathlib import Path

import pytest

# Make the container_control package importable for unit tests.
PKG_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PKG_ROOT))

from container_control.guard import ControlGuard  # noqa: E402
from container_control.handlers import Context, Limits  # noqa: E402
from container_control_fakes import FakeBackend, snapshot  # noqa: E402


@pytest.fixture
def snapshots():
    return [
        snapshot("keelson-router", "r" * 64, status="running"),
        snapshot(
            "grafana",
            "g" * 64,
            status="exited",
            finished="2026-01-03T00:00:00Z",
            exit_code=137,
        ),
        snapshot("keelson-container-control", "s" * 64, status="running"),
    ]


@pytest.fixture
def backend(snapshots):
    return FakeBackend(snapshots)


@pytest.fixture
def readonly_ctx(backend):
    return Context(backend=backend, guard=ControlGuard(), limits=Limits())


@pytest.fixture
def control_ctx(backend):
    return Context(
        backend=backend,
        guard=ControlGuard(
            control_enabled=True,
            allow_globs=("keelson-*",),
            self_identity=frozenset({"keelson-container-control", "s" * 64}),
        ),
        limits=Limits(),
    )


@pytest.fixture
def remove_ctx(backend):
    """Control on, and removal on for a NARROWER set than control.

    Deliberately not the same globs: every test that asserts removal is gated
    separately would pass vacuously if the two lists were equal.
    """
    return Context(
        backend=backend,
        guard=ControlGuard(
            control_enabled=True,
            allow_globs=("keelson-*", "grafana"),
            remove_globs=("grafana",),
            self_identity=frozenset({"keelson-container-control", "s" * 64}),
        ),
        limits=Limits(),
    )


@pytest.fixture
def fake_engine():
    """A stub Docker Engine on a unix socket.

    Lets the subprocess tests run a real ``docker2keelson`` process on
    a machine with no Docker daemon -- and in CI, where mounting the runner's
    socket into a test would be both unavailable and a bad idea.
    """
    from container_control_fake_engine import FakeEngine

    with FakeEngine() as engine:
        yield engine


@pytest.fixture
def fake_docker_env(fake_engine):
    """``os.environ`` plus a DOCKER_HOST pointing at :func:`fake_engine`.

    DOCKER_HOST rather than a ``--docker-host`` flag: it is docker-py's own
    documented knob and ``docker.from_env()`` already honours it, so the
    responder needs no production surface that exists only for tests.
    """
    return {**os.environ, "DOCKER_HOST": fake_engine.docker_host}
