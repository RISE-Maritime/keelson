"""The docker-attrs -> protobuf translation, including every shape that used to
crash the old implementation."""

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
            == ContainerRestartPolicy.RESTART_POLICY_NO
        )

    def test_on_failure_carries_its_retry_ceiling(self):
        result = info(restart_policy="on-failure", max_retries=3)
        assert result.restart_policy == ContainerRestartPolicy.RESTART_POLICY_ON_FAILURE
        assert result.restart_policy_max_retries == 3

    def test_no_health_check_is_none_not_unspecified(self):
        assert info().health == ContainerHealthStatus.HEALTH_STATUS_NONE

    def test_health_check_result(self):
        assert (
            info(health="unhealthy").health
            == ContainerHealthStatus.HEALTH_STATUS_UNHEALTHY
        )

    def test_unknown_health_string_is_unspecified(self):
        assert (
            info(health="weird").health
            == ContainerHealthStatus.HEALTH_STATUS_UNSPECIFIED
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
