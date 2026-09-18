"""The docker-attrs -> protobuf translation, including every shape that used to
crash the old implementation."""

import pytest

from container_control import model
from keelson.interfaces.ContainerControl_pb2 import LogStream
from keelson.payloads.ContainerHost_pb2 import (
    ContainerHealthStatus,
    ContainerInfo,
    ContainerRestartPolicy,
    ContainerState,
)

from container_control_fakes import snapshot


def info(**kwargs) -> ContainerInfo:
    return model.build_container_info(
        snapshot(**kwargs), controllable=False, removable=False
    )


class TestImageReference:
    def test_prefers_the_running_tag(self):
        assert info().image == "ghcr.io/rise-maritime/thing:1.2.3"

    def test_untagged_image_falls_back_to_the_configured_reference(self):
        # The old code was `container.image.tags[0]`, which raised IndexError
        # here and took the WHOLE listing down, not one row.
        assert (
            info(image_tags=(), image="repo/app@sha256:abc").image
            == "repo/app@sha256:abc"
        )

    def test_falls_back_to_the_image_id_when_nothing_else_is_known(self):
        snap = snapshot(image_tags=(), image_id="sha256:cafe")
        snap.attrs["Config"]["Image"] = ""
        assert model.image_reference(snap) == "sha256:cafe"


class TestTimes:
    def test_nine_fractional_digits_parse(self):
        # datetime.fromisoformat rejects these; Docker emits them always.
        assert info().created_at.ToJsonString().startswith("2026-01-02T03:04:05.123456")

    def test_never_started_leaves_started_at_unset(self):
        # Docker reports the zero instant, which renders as a year-1 date.
        assert not info(started="0001-01-01T00:00:00Z").HasField("started_at")

    def test_empty_string_leaves_the_field_unset(self):
        assert not info(started="").HasField("started_at")

    def test_running_container_has_no_finished_at(self):
        assert not info().HasField("finished_at")

    def test_garbage_is_dropped_rather_than_raising(self):
        assert model.parse_docker_time("not-a-time") is None


class TestExitCode:
    def test_absent_while_the_container_has_never_exited(self):
        # A running container's 0 would read as "exited cleanly".
        assert not info().HasField("exit_code")

    def test_present_once_it_has(self):
        result = info(status="exited", finished="2026-01-03T00:00:00Z", exit_code=137)
        assert result.HasField("exit_code") and result.exit_code == 137


class TestEnums:
    def test_known_state(self):
        assert info(status="running").state == ContainerState.CONTAINER_STATE_RUNNING

    def test_unknown_state_is_unspecified_but_the_raw_value_survives(self):
        result = info(status="hibernating")
        assert result.state == ContainerState.CONTAINER_STATE_UNSPECIFIED
        assert result.raw_state == "hibernating"

    def test_empty_restart_policy_means_no(self):
        # The Engine API spells "no restart policy" as "", which is
        # indistinguishable from "not reported" once it reaches a UI.
        assert (
            info(restart_policy="").restart_policy
            == ContainerRestartPolicy.CONTAINER_RESTART_POLICY_NO
        )

    def test_on_failure_carries_its_retry_ceiling(self):
        result = info(restart_policy="on-failure", max_retries=3)
        assert (
            result.restart_policy
            == ContainerRestartPolicy.CONTAINER_RESTART_POLICY_ON_FAILURE
        )
        assert result.restart_policy_max_retries == 3

    def test_no_health_check_is_none_not_unspecified(self):
        assert info().health == ContainerHealthStatus.CONTAINER_HEALTH_STATUS_NONE

    def test_health_check_result(self):
        assert (
            info(health="unhealthy").health
            == ContainerHealthStatus.CONTAINER_HEALTH_STATUS_UNHEALTHY
        )

    def test_unknown_health_string_is_unspecified(self):
        assert (
            info(health="weird").health
            == ContainerHealthStatus.CONTAINER_HEALTH_STATUS_UNSPECIFIED
        )


def test_compose_labels_are_surfaced():
    result = info(
        labels={
            model.COMPOSE_PROJECT_LABEL: "slipway",
            model.COMPOSE_SERVICE_LABEL: "router",
        }
    )
    assert (result.compose_project, result.compose_service) == ("slipway", "router")


def test_missing_sections_do_not_raise():
    assert (
        model.build_container_info(
            model.ContainerSnapshot(name="x", id="y", attrs={}),
            controllable=True,
            removable=False,
        ).name
        == "x"
    )


class TestLogParsing:
    def test_timestamp_prefix_is_split_off(self):
        lines = model.parse_log_stream(
            b"2026-01-02T03:04:05.123456789Z hello world\n", LogStream.LOG_STREAM_STDOUT
        )
        assert len(lines) == 1
        assert lines[0].text == "hello world"
        assert lines[0].stream == LogStream.LOG_STREAM_STDOUT
        assert lines[0].HasField("time")

    def test_line_without_a_parseable_timestamp_is_kept_untimed(self):
        lines = model.parse_log_stream(b"  continuation\n", LogStream.LOG_STREAM_STDERR)
        assert len(lines) == 1 and not lines[0].HasField("time")

    def test_invalid_utf8_is_replaced_not_raised(self):
        # A multibyte sequence split at the tail boundary is normal; the old
        # .decode("utf-8") turned it into an escaped UnicodeDecodeError and the
        # RPC never replied.
        lines = model.parse_log_stream(
            b"2026-01-02T03:04:05Z caf\xc3\n", LogStream.LOG_STREAM_STDOUT
        )
        assert lines[0].text.startswith("caf")

    def test_blank_lines_are_dropped(self):
        assert model.parse_log_stream(b"\n\n  \n", LogStream.LOG_STREAM_STDOUT) == []

    def test_streams_merge_in_timestamp_order(self):
        out = model.parse_log_stream(
            b"2026-01-01T00:00:01Z one\n2026-01-01T00:00:03Z three\n",
            LogStream.LOG_STREAM_STDOUT,
        )
        err = model.parse_log_stream(
            b"2026-01-01T00:00:02Z two\n", LogStream.LOG_STREAM_STDERR
        )
        assert [line.text for line in model.merge_log_lines(out, err)] == [
            "one",
            "two",
            "three",
        ]

    def test_untimed_continuation_stays_with_its_own_stream(self):
        out = model.parse_log_stream(
            b"2026-01-01T00:00:01Z head\n  tail-of-head\n", LogStream.LOG_STREAM_STDOUT
        )
        err = model.parse_log_stream(
            b"2026-01-01T00:00:09Z later\n", LogStream.LOG_STREAM_STDERR
        )
        # Leading whitespace survives: indentation is meaningful in the kind of
        # multi-line output (tracebacks) that produces continuation lines.
        assert [line.text for line in model.merge_log_lines(out, err)] == [
            "head",
            "  tail-of-head",
            "later",
        ]

    def test_one_empty_stream_short_circuits(self):
        out = model.parse_log_stream(
            b"2026-01-01T00:00:01Z only\n", LogStream.LOG_STREAM_STDOUT
        )
        assert [line.text for line in model.merge_log_lines(out, [])] == ["only"]


class TestCapping:
    def _lines(self, n):
        raw = b"".join(
            b"2026-01-01T00:00:0%dZ line%d\n" % (i % 10, i) for i in range(n)
        )
        return model.parse_log_stream(raw, LogStream.LOG_STREAM_STDOUT)

    def test_line_cap_keeps_the_most_recent(self):
        kept, truncated = model.cap_log_lines(self._lines(10), max_lines=3, max_bytes=0)
        assert truncated
        assert [line.text for line in kept] == ["line7", "line8", "line9"]

    def test_byte_cap_drops_the_oldest(self):
        kept, truncated = model.cap_log_lines(
            self._lines(10), max_lines=0, max_bytes=20
        )
        assert truncated
        assert kept[-1].text == "line9"
        assert len(kept) < 10

    def test_under_both_caps_is_untouched(self):
        kept, truncated = model.cap_log_lines(
            self._lines(3), max_lines=100, max_bytes=10_000
        )
        assert not truncated and len(kept) == 3


def test_glob_matching_is_case_sensitive():
    assert model.matches_any("keelson-router", ["keelson-*"])
    assert not model.matches_any("Keelson-router", ["keelson-*"])
    assert model.matches_any("anything", ["*"])
    assert not model.matches_any("anything", [])


class TestDeploymentDetail:
    """Ports, networks and mounts -- and, as importantly, what is NOT reported.

    All of them are absent from the ordinary fixture attrs rather than empty,
    which is the shape every other test runs against -- so "reports nothing"
    is the first thing each has to get right.
    """

    @staticmethod
    def build(snap, *, include_detail=True) -> ContainerInfo:
        return model.build_container_info(
            snap, controllable=False, removable=False, include_detail=include_detail
        )

    @staticmethod
    def wired_snapshot():
        snap = snapshot()
        snap.attrs["Config"]["Env"] = ["API_TOKEN=hunter2"]
        snap.attrs["Config"]["Cmd"] = ["--password", "swordfish"]
        snap.attrs["Config"]["Entrypoint"] = ["/entry", "postgres://u:letmein@db"]
        snap.attrs["NetworkSettings"] = {
            "Ports": {"8080/tcp": [{"HostIp": "0.0.0.0", "HostPort": "18080"}]},
            "Networks": {"keelson": {"IPAddress": "172.18.0.4"}},
        }
        snap.attrs["Mounts"] = [{"Type": "volume", "Name": "data", "Destination": "/d"}]
        return snap

    def test_a_container_with_no_detail_reports_empty_lists(self):
        got = self.build(snapshot())
        assert list(got.ports) == []
        assert list(got.networks) == []
        assert list(got.mounts) == []

    def test_missing_sections_report_empty_lists_rather_than_raising(self):
        got = self.build(
            model.ContainerSnapshot(name="bare", id="b" * 64, attrs={"Config": None})
        )
        assert (list(got.ports), list(got.networks), list(got.mounts)) == ([], [], [])

    def test_detail_is_off_unless_asked_for(self):
        # The default is the one that leaks nothing: a call site that forgets
        # the keyword gets no deployment detail at all.
        got = model.build_container_info(
            self.wired_snapshot(), controllable=False, removable=False
        )
        assert (list(got.ports), list(got.networks), list(got.mounts)) == ([], [], [])

    def test_detail_is_filled_when_asked_for(self):
        got = self.build(self.wired_snapshot())
        assert [p.host_port for p in got.ports] == [18080]
        assert [n.name for n in got.networks] == ["keelson"]
        assert [m.source for m in got.mounts] == ["data"]

    # -- what is never reported ----------------------------------------------
    #
    # THE ONE THAT MATTERS. Environment, command and entrypoint are where
    # tokens, passwords and connection strings live, and the bus carries no
    # per-key authorization. They are not modelled at all.

    def test_env_command_and_entrypoint_are_not_fields(self):
        fields = ContainerInfo.DESCRIPTOR.fields_by_name
        assert not {"env", "command", "entrypoint"} & set(fields)

    @pytest.mark.parametrize("include_detail", [False, True])
    def test_no_secret_reaches_the_wire(self, include_detail):
        wire = self.build(
            self.wired_snapshot(), include_detail=include_detail
        ).SerializeToString()
        for secret in (b"API_TOKEN", b"hunter2", b"swordfish", b"letmein"):
            assert secret not in wire

    # -- ports ---------------------------------------------------------------

    def test_a_published_port_carries_both_halves(self):
        snap = snapshot()
        snap.attrs["NetworkSettings"] = {
            "Ports": {"8080/tcp": [{"HostIp": "0.0.0.0", "HostPort": "18080"}]}
        }
        (port,) = self.build(snap).ports
        assert (port.container_port, port.protocol) == (8080, "tcp")
        assert (port.host_ip, port.host_port) == ("0.0.0.0", 18080)

    def test_ipv4_and_ipv6_bindings_are_separate_rows(self):
        snap = snapshot()
        snap.attrs["NetworkSettings"] = {
            "Ports": {
                "9000/tcp": [
                    {"HostIp": "0.0.0.0", "HostPort": "9000"},
                    {"HostIp": "::", "HostPort": "9000"},
                ]
            }
        }
        assert [p.host_ip for p in self.build(snap).ports] == ["0.0.0.0", "::"]

    def test_an_exposed_but_unpublished_port_is_reported_without_a_host_port(self):
        # Docker maps these to None. Dropping the row would say "no ports" about
        # a container that exposes one.
        snap = snapshot()
        snap.attrs["NetworkSettings"] = {"Ports": {"7447/udp": None}}
        (port,) = self.build(snap).ports
        assert (port.container_port, port.protocol, port.host_port) == (7447, "udp", 0)

    def test_an_unparseable_port_key_is_skipped(self):
        snap = snapshot()
        snap.attrs["NetworkSettings"] = {"Ports": {"nonsense": None, "80/tcp": None}}
        assert [p.container_port for p in self.build(snap).ports] == [80]

    # -- networks ------------------------------------------------------------

    def test_networks_carry_their_address_and_aliases(self):
        snap = snapshot()
        snap.attrs["NetworkSettings"] = {
            "Networks": {"keelson": {"IPAddress": "172.18.0.4", "Aliases": ["router"]}}
        }
        (net,) = self.build(snap).networks
        assert (net.name, net.ip_address, list(net.aliases)) == (
            "keelson",
            "172.18.0.4",
            ["router"],
        )

    def test_host_networking_attaches_to_nothing(self):
        # The same fact ContainerResourceUsage states by omitting its network
        # counters. Empty here is an answer, not a gap.
        snap = snapshot()
        snap.attrs["NetworkSettings"] = {"Networks": {}}
        assert list(self.build(snap).networks) == []

    # -- mounts --------------------------------------------------------------

    def test_a_read_only_bind_mount_round_trips(self):
        snap = snapshot()
        snap.attrs["Mounts"] = [
            {
                "Type": "bind",
                "Source": "/etc/keelson",
                "Destination": "/config",
                "RW": False,
            }
        ]
        (mount,) = self.build(snap).mounts
        assert (mount.type, mount.source, mount.destination) == (
            "bind",
            "/etc/keelson",
            "/config",
        )
        assert mount.read_only is True

    def test_rw_defaults_to_read_write_the_way_docker_does(self):
        snap = snapshot()
        snap.attrs["Mounts"] = [
            {"Type": "volume", "Name": "data", "Destination": "/var/lib"}
        ]
        (mount,) = self.build(snap).mounts
        # Source falls back to Name, which is where a named volume's identity is.
        assert (mount.source, mount.read_only) == ("data", False)
