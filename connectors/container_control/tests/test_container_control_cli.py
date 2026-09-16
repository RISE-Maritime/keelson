"""The command-line surface of the three binaries.

Two halves. The ops client's rendering, including reply shapes it does not
control, is exercised by importing ``bin/container-control-cli.py``. The startup
gates of ``docker2keelson`` -- argparse validation and the socket pre-flight --
only exist in ``main()``, so they run the script as a real process; every case
here exits before a Zenoh session opens. The one case that gets past the gates
and opens a session lives with the other e2e tests.

No Docker daemon is required: the failure branch points ``DOCKER_HOST`` at a
socket that does not exist, and the success branch at a stub Engine.
"""

import os
import subprocess

import pytest

from keelson.interfaces.ErrorResponse_pb2 import ErrorResponse

from container_control_bins import bin_command, load_bin
from keelson.payloads.ContainerHost_pb2 import ContainerInfo

_cli = load_bin("container-control-cli")
describe_error = _cli.describe_error
_fmt_time = _cli._fmt_time


def test_a_typed_error_is_named_by_its_code():
    raw = ErrorResponse(
        error_description="not in the allow-list",
        code=ErrorResponse.Code.PERMISSION_DENIED,
    ).SerializeToString()
    assert describe_error(raw) == "ERROR PERMISSION_DENIED: not in the allow-list"


def test_a_transport_error_is_reported_not_parsed_as_protobuf():
    # A timeout or a missing route produces a zenoh-level ReplyError carrying a
    # plain string. Parsing that as an ErrorResponse raises DecodeError and
    # buries the real problem under a traceback.
    assert "timeout" in describe_error(b"query timeout: no reply")
    assert describe_error(b"query timeout: no reply").startswith("ERROR (transport)")


def test_undecodable_bytes_do_not_raise():
    assert describe_error(b"\xff\xfe\x00 garbage").startswith("ERROR")


def test_a_valid_error_response_is_preferred_over_the_text_fallback():
    raw = ErrorResponse(
        error_description="", code=ErrorResponse.Code.NOT_FOUND
    ).SerializeToString()
    assert describe_error(raw).startswith("ERROR NOT_FOUND")


def test_unset_timestamps_render_as_a_dash():
    assert _fmt_time(ContainerInfo(), "started_at") == "-"


def test_set_timestamps_render_as_a_datetime():
    info = ContainerInfo()
    info.started_at.FromJsonString("2026-01-02T03:04:05Z")
    assert _fmt_time(info, "started_at") == "2026-01-02 03:04:05"


BASE = bin_command("docker2keelson", "-r", "test", "-e", "entity", "-s", "source")


def run(args, env=None, timeout=60):
    return subprocess.run(
        args, capture_output=True, text=True, timeout=timeout, env=env, check=False
    )


class TestArgumentValidation:
    def test_allow_control_without_a_glob_is_rejected(self):
        # A responder started with control "enabled" and nothing allowed would
        # look enabled and refuse everything. argparse.error exits 2, before
        # logging setup or any socket contact.
        result = run([*BASE, "--allow-control"])
        assert result.returncode == 2
        assert "--allow-control requires at least one --allow GLOB" in result.stderr

    def test_allow_control_with_a_glob_gets_past_argument_parsing(
        self, fake_docker_env
    ):
        # Same flags, one --allow added: it must now fail (or not) for reasons
        # other than argument validation.
        result = run([*BASE, "--allow-control", "--allow", "x-*"], env=fake_docker_env)
        assert result.returncode != 2
        assert "requires at least one --allow" not in result.stderr

    def test_allow_remove_without_allow_control_is_rejected(self):
        # Removal without control would also skip build_guard's self-identity
        # resolution, leaving the responder able to delete its own container.
        result = run([*BASE, "--allow-remove", "scratch-*"])
        assert result.returncode == 2
        assert "--allow-remove requires --allow-control" in result.stderr

    def test_allow_remove_alongside_control_gets_past_argument_parsing(
        self, fake_docker_env
    ):
        result = run(
            [*BASE, "--allow-control", "--allow", "x-*", "--allow-remove", "x-*"],
            env=fake_docker_env,
        )
        assert result.returncode != 2
        assert "--allow-remove requires" not in result.stderr

    @pytest.mark.parametrize("missing", ["-r", "-e", "-s"])
    def test_identity_arguments_are_required(self, missing):
        args = list(BASE)
        index = args.index(missing)
        del args[index : index + 2]
        result = run(args)
        assert result.returncode == 2

    def test_help_lists_the_control_flags(self):
        result = run(bin_command("docker2keelson", "--help"))
        assert result.returncode == 0
        for flag in (
            "--allow-control",
            "--allow",
            "--allow-remove",
            "--self-container-name",
        ):
            assert flag in result.stdout

    def test_help_lists_the_stats_flags(self):
        result = run(bin_command("docker2keelson", "--help"))
        assert result.returncode == 0
        for flag in ("--publish-stats", "--stats-interval-s"):
            assert flag in result.stdout


class TestSocketPreflight:
    def test_an_unreachable_socket_exits_with_actionable_advice(self):
        # The container's most likely misconfiguration by far: the socket is
        # not mounted, or uid 10001 is not in the docker group. Exiting at
        # startup beats serving and answering UNAVAILABLE to every call.
        result = run(
            [*BASE],
            env={**os.environ, "DOCKER_HOST": "unix:///nonexistent/docker.sock"},
        )
        assert result.returncode == 1
        assert "cannot reach the container runtime" in result.stderr
        # The message has to name the two things the operator can act on.
        assert "DOCKER_GID" in result.stderr
        assert "/var/run/docker.sock" in result.stderr


@pytest.mark.parametrize(
    "binary",
    ["docker2keelson", "container-control-cli", "container-control-healthcheck"],
)
def test_every_binary_answers_help(binary):
    # The image smoke test and the README dumps both depend on this.
    result = run(bin_command(binary, "--help"))
    assert result.returncode == 0
    assert result.stdout.startswith(f"usage: {binary}")


def test_the_healthcheck_reports_an_unreachable_socket():
    result = run(
        bin_command("container-control-healthcheck", "--timeout-s", "2"),
        env={**os.environ, "DOCKER_HOST": "unix:///nonexistent/docker.sock"},
    )
    assert result.returncode == 1
    assert "cannot reach the container runtime" in result.stderr
