"""The resource-utilisation arithmetic: what is derivable, and what is not.

Separate from test_model.py, which is about translating a container's
DESCRIPTION. This is about translating its COUNTERS, and the two have different
failure modes. Almost every test here is about the same thing said once per
quantity: a number nobody can know yet must be ABSENT, not zero. A dashboard
cannot tell 0.0 from "no reading" -- it draws a flat line through the gap and an
alert rule reads it as healthy -- so absence is asserted with HasField(), never
with `== 0.0`.
"""

from __future__ import annotations

import pytest

from container_control import model

from container_control_fakes import STOPPED_STATS_RAW, snapshot, stats_raw

pytestmark = pytest.mark.unit


def build(raw, previous=None, *, at=100.0, snap=None):
    return model.build_resource_usage(snap or snapshot(), raw, previous, monotonic_s=at)


def sample_from(raw, *, at=100.0):
    return model.read_stats_sample(raw, monotonic_s=at)


class TestFirstSample:
    """The tick a container appears on. Everything cumulative is knowable;
    everything derived from a pair of readings is not."""

    def test_the_derived_values_are_absent_not_zero(self):
        usage, sample = build(stats_raw())

        assert sample is not None
        for field in (
            "cpu_load_pct",
            "sample_window_s",
            "network_rx_bytes_per_second",
            "network_tx_bytes_per_second",
            "block_read_bytes_per_second",
            "block_write_bytes_per_second",
        ):
            assert not usage.HasField(
                field
            ), f"{field} should be absent on a first sample"

    def test_the_cumulative_values_are_present(self):
        usage, _ = build(stats_raw(block_read=16_474_112, block_write=4096))

        assert usage.block_read_bytes == 16_474_112
        assert usage.block_write_bytes == 4096
        assert usage.network_rx_bytes == 581_000
        assert usage.pids_current == 9
        assert usage.online_cpus == 16


class TestCpuLoad:
    def test_one_busy_core_out_of_sixteen_reads_as_one_hundred_percent(self):
        # Docker's own convention: 100.0 is ONE core, so the ceiling on this
        # host is 1600. The container burned 1s of CPU while the host burned
        # 16s across its 16 cores -- which is exactly one core's worth.
        first = sample_from(
            stats_raw(cpu_total=1_000_000_000, system_cpu=16_000_000_000)
        )
        usage, _ = build(
            stats_raw(cpu_total=2_000_000_000, system_cpu=32_000_000_000),
            first,
            at=101.0,
        )

        assert usage.cpu_load_pct == pytest.approx(100.0)

    def test_a_container_can_exceed_one_hundred_percent(self):
        first = sample_from(
            stats_raw(cpu_total=1_000_000_000, system_cpu=16_000_000_000)
        )
        usage, _ = build(
            stats_raw(cpu_total=5_000_000_000, system_cpu=32_000_000_000),
            first,
            at=101.0,
        )

        assert usage.cpu_load_pct == pytest.approx(400.0)

    def test_a_restart_in_place_unsets_it_rather_than_reporting_a_spike(self):
        # `docker restart` keeps the id and zeroes the cgroup counters. The
        # cache still holds the pre-restart totals, and differencing against
        # them would publish the entire counter as one interval's work.
        before = sample_from(stats_raw(cpu_total=7_000_000_000_000))
        usage, _ = build(
            stats_raw(cpu_total=12_000_000, system_cpu=755_412_900_000_000),
            before,
            at=101.0,
        )

        assert not usage.HasField("cpu_load_pct")

    def test_a_system_total_that_did_not_advance_unsets_it(self):
        first = sample_from(stats_raw())
        usage, _ = build(stats_raw(cpu_total=8_000_000_000_000), first, at=101.0)

        assert not usage.HasField("cpu_load_pct")

    def test_an_unreported_core_count_is_absent_not_zero(self):
        # It is the divisor for normalising the percentage to 0-100. A zero
        # denominator fails silently where an absent one fails visibly.
        raw = stats_raw(online_cpus=0)

        usage, _ = build(raw)

        assert not usage.HasField("online_cpus")

    def test_online_cpus_falls_back_to_the_per_cpu_list(self):
        # Older daemons omit online_cpus and report percpu_usage instead. The
        # count is the multiplier in the percentage, so getting it wrong scales
        # every number on the host.
        raw = stats_raw(online_cpus=0)
        raw["cpu_stats"]["cpu_usage"]["percpu_usage"] = [1, 2, 3, 4]

        usage, _ = build(raw)

        assert usage.online_cpus == 4


class TestRates:
    def test_bytes_per_second_divide_by_the_measured_window(self):
        first = sample_from(stats_raw(rx_bytes=1000, block_read=2000), at=100.0)
        usage, _ = build(
            stats_raw(rx_bytes=3000, block_read=6000),
            first,
            at=102.0,  # two seconds, not the configured interval
        )

        assert usage.sample_window_s == pytest.approx(2.0)
        assert usage.network_rx_bytes_per_second == pytest.approx(1000.0)
        assert usage.block_read_bytes_per_second == pytest.approx(2000.0)

    def test_a_counter_that_went_backwards_unsets_only_its_own_rate(self):
        # A restart zeroes the counters independently of one another, and a
        # single "something reset" flag would throw away the good values with
        # the bad.
        first = sample_from(stats_raw(rx_bytes=5000, block_read=2000), at=100.0)
        usage, _ = build(
            stats_raw(rx_bytes=10, block_read=6000),
            first,
            at=101.0,
        )

        assert not usage.HasField("network_rx_bytes_per_second")
        assert usage.block_read_bytes_per_second == pytest.approx(4000.0)

    def test_two_samples_at_the_same_instant_produce_no_rates(self):
        first = sample_from(stats_raw(), at=100.0)
        usage, _ = build(stats_raw(), first, at=100.0)

        assert not usage.HasField("sample_window_s")
        assert not usage.HasField("block_read_bytes_per_second")


class TestNetwork:
    def test_a_host_networked_container_reports_no_network_fields_at_all(self):
        # network_mode: host -- the Engine omits `networks` entirely, because
        # the container has no interface of its own. Zero would be a claim that
        # it moved no traffic, which is the opposite of true.
        first = sample_from(
            stats_raw(
                rx_bytes=None,
                tx_bytes=None,
                cpu_total=1_000_000_000,
                system_cpu=16_000_000_000,
            ),
            at=100.0,
        )
        usage, _ = build(
            stats_raw(
                rx_bytes=None,
                tx_bytes=None,
                cpu_total=2_000_000_000,
                system_cpu=32_000_000_000,
            ),
            first,
            at=101.0,
        )

        for field in (
            "network_rx_bytes",
            "network_tx_bytes",
            "network_rx_bytes_per_second",
            "network_tx_bytes_per_second",
        ):
            assert not usage.HasField(field)
        # ...while everything not about interfaces is still reported.
        assert usage.HasField("cpu_load_pct")
        assert usage.memory_used_bytes > 0

    def test_interfaces_are_summed(self):
        raw = stats_raw()
        raw["networks"] = {
            "eth0": {"rx_bytes": 100, "tx_bytes": 10},
            "eth1": {"rx_bytes": 200, "tx_bytes": 20},
        }

        usage, _ = build(raw)

        assert usage.network_rx_bytes == 300
        assert usage.network_tx_bytes == 30


class TestBlockIo:
    def test_cgroup_v1_spells_the_operations_with_capitals(self):
        # A case-sensitive match silently sums to zero on the other cgroup
        # version, which looks like a container doing no I/O rather than a bug.
        usage, _ = build(
            stats_raw(block_read=1234, block_write=99, block_op_case="upper")
        )

        assert usage.block_read_bytes == 1234
        assert usage.block_write_bytes == 99

    def test_a_runtime_reporting_no_blkio_accounting_reports_nothing(self):
        # Not zero. Some storage drivers count no block I/O at all, and "wrote
        # nothing" is a different claim from "nobody counted" -- the same
        # distinction the rates already draw, which the totals used to lose.
        raw = stats_raw()
        raw["blkio_stats"]["io_service_bytes_recursive"] = None

        usage, _ = build(raw)

        assert not usage.HasField("block_read_bytes")
        assert not usage.HasField("block_write_bytes")

    def test_a_container_that_has_written_nothing_still_reports_a_zero(self):
        # The other half: the runtime DID count, and counted zero. Present-and-0
        # is the honest answer, and it must be distinguishable from the case
        # above.
        usage, _ = build(stats_raw(block_read=0, block_write=0))

        assert usage.HasField("block_write_bytes")
        assert usage.block_write_bytes == 0


class TestMemory:
    def test_the_working_set_excludes_reclaimable_cache(self):
        # What `docker stats` shows. Raw usage counts page cache the kernel
        # would drop on demand, so a container that read a large file looks
        # like one leaking.
        usage, _ = build(stats_raw(memory_usage=1000, inactive_file=400))

        assert usage.memory_used_bytes == 600

    def test_the_cgroup_v1_key_wins_when_both_are_present(self):
        raw = stats_raw(memory_usage=1000)
        raw["memory_stats"]["stats"] = {
            "inactive_file": 100,
            "total_inactive_file": 400,
        }

        usage, _ = build(raw)

        assert usage.memory_used_bytes == 600

    def test_a_nonsensical_cache_value_falls_back_to_raw_usage(self):
        raw = stats_raw(memory_usage=1000)
        raw["memory_stats"]["stats"] = {"inactive_file": 5000}

        usage, _ = build(raw)

        assert usage.memory_used_bytes == 1000

    def test_an_unconstrained_container_has_a_percentage_but_no_limit(self):
        # THE POINT OF SPLITTING THE TWO. The Engine reports host memory as the
        # `limit` of an unconstrained container, so the percentage is
        # answerable -- it is what `docker stats` prints -- but publishing that
        # number as "the limit" would tell an operator every container on a
        # 31 GiB box is limited to 31 GiB.
        usage, _ = build(
            stats_raw(memory_usage=1000, inactive_file=0, memory_limit=100_000)
        )

        assert not usage.HasField("memory_limit_bytes")
        assert usage.memory_used_pct == pytest.approx(1.0)

    def test_a_configured_limit_is_published(self):
        snap = snapshot(host_config={"Memory": 100_000})

        usage, _ = build(
            stats_raw(memory_usage=1000, inactive_file=0, memory_limit=100_000),
            snap=snap,
        )

        assert usage.memory_limit_bytes == 100_000
        assert usage.memory_used_pct == pytest.approx(1.0)


class TestConfiguredAllocation:
    def test_nothing_is_reported_for_an_unconstrained_container(self):
        usage, _ = build(stats_raw())

        assert not usage.HasField("cpu_allocation_cores")
        assert not usage.HasField("cpu_shares")
        assert usage.cpuset_cpus == ""

    def test_nano_cpus_is_reported_in_cores(self):
        usage, _ = build(
            stats_raw(), snap=snapshot(host_config={"NanoCpus": 1_500_000_000})
        )

        assert usage.cpu_allocation_cores == pytest.approx(1.5)

    def test_the_raw_cfs_pair_says_the_same_thing(self):
        # Compose writes one spelling or the other depending on which key the
        # deployment used; both mean half a core.
        usage, _ = build(
            stats_raw(),
            snap=snapshot(host_config={"CpuQuota": 50_000, "CpuPeriod": 100_000}),
        )

        assert usage.cpu_allocation_cores == pytest.approx(0.5)

    def test_shares_and_cpuset_are_passed_through(self):
        usage, _ = build(
            stats_raw(),
            snap=snapshot(host_config={"CpuShares": 512, "CpusetCpus": "0-3"}),
        )

        assert usage.cpu_shares == 512
        assert usage.cpuset_cpus == "0-3"

    @pytest.mark.parametrize("value", [None, 0, -1])
    def test_an_uncapped_pid_limit_is_absent(self, value):
        usage, _ = build(stats_raw(pids_limit=value))

        assert not usage.HasField("pids_limit")


class TestThrottling:
    def test_throttling_counters_are_carried(self):
        # The counter that says a CPU limit is actually biting, and the one
        # thing here `docker stats` does not show at all.
        usage, _ = build(stats_raw(throttled_periods=17, throttled_time=4_000_000))

        assert usage.cpu_throttled_periods == 17
        assert usage.cpu_throttled_time_ns == 4_000_000

    def test_never_throttled_is_published_as_an_explicit_zero(self):
        # THE CASE THE `optional` KEYWORD EXISTS FOR. Zero throttling is the
        # normal, healthy reading, so a plain proto3 uint64 would omit these on
        # exactly the containers that are fine -- and a consumer decoding a hard
        # 0 could not tell "measured, never throttled" from "not reported".
        usage, _ = build(stats_raw(throttled_periods=0, throttled_time=0))

        assert usage.HasField("cpu_throttled_periods")
        assert usage.cpu_throttled_periods == 0

    def test_a_runtime_reporting_no_throttling_data_reports_nothing(self):
        raw = stats_raw()
        raw["cpu_stats"]["throttling_data"] = {}

        usage, _ = build(raw)

        assert not usage.HasField("cpu_throttled_periods")
        assert not usage.HasField("cpu_throttled_time_ns")


class TestAStoppedContainer:
    def test_its_empty_body_produces_no_row(self):
        # The Engine answers 200 for a container that stopped between the
        # listing and the stats call. Building a row from it would publish
        # "0% CPU, 0 bytes" -- indistinguishable from an idle container.
        usage, sample = build(STOPPED_STATS_RAW)

        assert usage is None
        assert sample is None

    def test_it_is_not_cached_as_a_predecessor(self):
        assert sample_from(STOPPED_STATS_RAW) is None
