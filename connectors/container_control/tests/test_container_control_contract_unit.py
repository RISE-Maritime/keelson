"""The wire contract, pinned.

The keelson SDK now generates both codecs for `container_control/v1` from
interfaces/ContainerControl.proto and messages/payloads/ContainerHost.proto, so
this file no longer holds two copies of a schema together. What it still pins
is the MEANING that a tidy-up could change without breaking a build: field
numbers already on the wire, the procedure list serve_rpc advertises, and which
fields must keep explicit presence. crowsnest-dev pins the same field numbers in
scripts/checks/containerControl.mjs until it moves onto the SDK codec.
"""

import re

import keelson
import pytest
from keelson.interfaces.ContainerControl_pb2 import (
    DESCRIPTOR,
    GetLogsResponse,
    ListContainersResponse,
    LogLine,
    RemoveContainerRequest,
    RemoveContainerResponse,
)
from keelson.payloads.ContainerHost_pb2 import DESCRIPTOR as PAYLOAD_DESCRIPTOR
from keelson.payloads.ContainerHost_pb2 import (
    ContainerHostStats,
    ContainerHostStatus,
    ContainerInfo,
    ContainerResourceUsage,
    ContainerState,
    ContainerStatusTrigger,
)

from container_control import INTERFACE, VERSION

# serve_rpc advertises the COMPLETE interface through one liveliness token, so a
# procedure declared here without a handler would be advertised and never answer.
# test_container_control_handlers_unit.py asserts the other half of this equality.
PROCEDURES = ["list", "logs", "start", "stop", "restart", "remove"]

CONTAINER_INFO_FIELDS = {
    "name": 1,
    "id": 2,
    "image": 3,
    "state": 4,
    "raw_state": 5,
    "created_at": 6,
    "started_at": 7,
    "finished_at": 8,
    "exit_code": 9,
    "restart_policy": 10,
    "restart_policy_max_retries": 11,
    "restart_count": 12,
    "health": 13,
    "controllable": 14,
    "compose_project": 15,
    "compose_service": 16,
    "removable": 17,
    "env": 18,
    "ports": 19,
    "networks": 20,
    "mounts": 21,
    "command": 22,
    "entrypoint": 23,
}

#: The four deployment-detail messages. Pinned for the same reason as above: a
#: renumbering here breaks every client silently.
DETAIL_FIELDS = {
    "ContainerEnvVar": {"name": 1, "value": 2},
    "ContainerPortBinding": {
        "container_port": 1,
        "protocol": 2,
        "host_ip": 3,
        "host_port": 4,
    },
    "ContainerNetworkAttachment": {"name": 1, "ip_address": 2, "aliases": 3},
    "ContainerMount": {"type": 1, "source": 2, "destination": 3, "read_only": 4},
}


def field_numbers(message_cls) -> dict[str, int]:
    return {f.name: f.number for f in message_cls.DESCRIPTOR.fields}


def test_the_service_declares_exactly_the_six_procedures_in_order():
    service = DESCRIPTOR.services_by_name["ContainerControl"]
    assert [m.name for m in service.methods] == PROCEDURES


def test_container_info_field_numbers_are_pinned():
    assert field_numbers(ContainerInfo) == CONTAINER_INFO_FIELDS


def test_response_field_numbers_are_pinned():
    assert field_numbers(ListContainersResponse) == {
        "containers": 1,
        "observed_at": 2,
        "control_enabled": 3,
        "remove_enabled": 4,
    }
    assert field_numbers(GetLogsResponse) == {
        "name": 1,
        "id": 2,
        "lines": 3,
        "truncated": 4,
        "tail_lines": 5,
    }
    assert field_numbers(LogLine) == {"time": 1, "stream": 2, "text": 3}
    assert field_numbers(RemoveContainerRequest) == {
        "name": 1,
        "force": 2,
        "remove_volumes": 3,
    }
    assert field_numbers(RemoveContainerResponse) == {
        "name": 1,
        "id": 2,
        "force_applied": 3,
    }


def test_remove_answers_its_own_message_not_container_action_response():
    """A ContainerInfo cannot describe a container that no longer exists.

    Reusing ContainerActionResponse here would have been the smaller diff and a
    lie on the wire: its `container` field promises post-action state, and after
    a removal there is none to read.
    """
    service = DESCRIPTOR.services_by_name["ContainerControl"]
    methods = {m.name: m for m in service.methods}
    assert methods["remove"].output_type.name == "RemoveContainerResponse"
    assert methods["restart"].output_type.name == "ContainerActionResponse"


def test_removable_is_a_field_of_its_own_not_an_alias_for_controllable():
    """The two gates are separate all the way to the wire.

    A client greys its Remove button on `removable`; if this ever collapses into
    `controllable`, every read-only-for-removal responder starts advertising a
    button that each call refuses.
    """
    fields = {f.name for f in ContainerInfo.DESCRIPTOR.fields}
    assert {"controllable", "removable"} <= fields
    assert field_numbers(ContainerInfo)["removable"] == 17


def test_the_deployment_detail_messages_keep_their_field_numbers():
    from keelson.payloads import ContainerHost_pb2

    for name, expected in DETAIL_FIELDS.items():
        assert field_numbers(getattr(ContainerHost_pb2, name)) == expected, name


def test_an_env_value_has_presence_so_withheld_is_not_empty():
    """The whole reason ContainerEnvVar is a message and not a map<string, string>."""
    from keelson.payloads.ContainerHost_pb2 import ContainerEnvVar

    value = ContainerEnvVar.DESCRIPTOR.fields_by_name["value"]
    assert value.has_presence
    assert not ContainerEnvVar(name="API_TOKEN").HasField("value")
    assert ContainerEnvVar(name="EMPTY", value="").HasField("value")


def test_the_packages_follow_the_monorepo_split():
    # Request/response wrappers are interface-scoped; the nouns a subject
    # publishes are keelson domain payloads, as Mission.proto is for
    # vehicle_mission.
    assert DESCRIPTOR.package == "keelson.interfaces.container_control"
    assert PAYLOAD_DESCRIPTOR.package == "keelson"
    assert ContainerInfo.DESCRIPTOR.full_name == "keelson.ContainerInfo"


def test_exit_code_is_optional_so_zero_is_distinguishable_from_absent():
    assert ContainerInfo.DESCRIPTOR.fields_by_name["exit_code"].has_presence


def test_enum_zero_values_are_unspecified():
    # proto3 hands back the zero value for an absent field; it must not name a
    # real state.
    assert ContainerState.Name(0) == "CONTAINER_STATE_UNSPECIFIED"


def upper_snake(type_name: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", type_name).upper()


@pytest.mark.parametrize("enum", PAYLOAD_DESCRIPTOR.enum_types_by_name.values())
def test_enum_values_carry_the_full_type_name(enum):
    # proto3 enum values are scoped to the package, not the enum: a short prefix
    # such as HEALTH_STATUS_ claims that name for all of keelson, next to
    # HealthLevel's HEALTH_*. Names are not on the wire; numbers are pinned above.
    prefix = upper_snake(enum.name) + "_"
    assert [v.name for v in enum.values if not v.name.startswith(prefix)] == []
    assert enum.values_by_number[0].name == prefix + "UNSPECIFIED"


class TestInterfaceRegistration:
    """Against the registry the SDK ships, not a local shim."""

    def test_the_registry_names_this_service(self):
        assert keelson.is_interface_well_known(f"{INTERFACE}/{VERSION}")
        assert (
            keelson.get_interface_service(f"{INTERFACE}/{VERSION}")
            == f"{DESCRIPTOR.package}.ContainerControl"
        )

    @pytest.mark.parametrize("procedure", PROCEDURES)
    def test_the_rpc_key_has_the_shape_the_consumer_builds(self, procedure):
        assert (
            keelson.construct_rpc_key(
                "rise", "masslab", INTERFACE, VERSION, procedure, "masslab-4"
            )
            == f"rise/@v0/masslab/@rpc/container_control/v1/{procedure}/masslab-4"
        )


class TestPublishedState:
    """The published half: pinned because these numbers are already on the wire."""

    def test_container_host_status_field_numbers_are_pinned(self):

        assert field_numbers(ContainerHostStatus) == {
            "containers": 1,
            "observed_at": 2,
            "control_enabled": 3,
            "trigger": 4,
            "sequence": 5,
            "remove_enabled": 6,
        }

    def test_the_first_three_numbers_match_the_rpc_response(self):
        # Not a coincidence to be tidied away later: the two carry the same three
        # things, and a reader diffing them should see them line up.

        published = field_numbers(ContainerHostStatus)
        answered = field_numbers(ListContainersResponse)
        for name in ("containers", "observed_at", "control_enabled"):
            assert published[name] == answered[name]

    def test_and_the_alignment_deliberately_stops_at_remove_enabled(self):
        # 4 and 5 were already spent here on trigger and sequence, which the RPC
        # response has no equivalent of. Pinned so the divergence reads as a
        # decision rather than as drift someone should "fix" by renumbering a
        # field that is already on the wire.

        assert field_numbers(ContainerHostStatus)["remove_enabled"] == 6
        assert field_numbers(ListContainersResponse)["remove_enabled"] == 4

    def test_status_trigger_distinguishes_a_change_from_a_keep_alive(self):

        assert ContainerStatusTrigger.Name(0) == "CONTAINER_STATUS_TRIGGER_UNSPECIFIED"
        assert ContainerStatusTrigger.Value("CONTAINER_STATUS_TRIGGER_CHANGE") == 1
        assert ContainerStatusTrigger.Value("CONTAINER_STATUS_TRIGGER_HEARTBEAT") == 2

    def test_publishing_added_no_procedure(self):
        # A continuous stream is pub/sub and no `rpc` expresses it, so this
        # message must not have grown one. `remove` IS a procedure and is in
        # PROCEDURES; container_status is not and never will be.
        service = DESCRIPTOR.services_by_name["ContainerControl"]
        assert [m.name for m in service.methods] == PROCEDURES
        assert "container_status" not in [m.name for m in service.methods]


class TestSubjectRegistration:
    def test_the_registry_names_this_payload(self):

        assert keelson.is_subject_well_known("container_status")
        assert (
            keelson.get_subject_schema("container_status")
            == "keelson.ContainerHostStatus"
        )

    def test_the_pubsub_key_is_one_per_host(self):

        # No trailing container chunk -- unlike log_message, which is one key per
        # container. See ContainerHostStatus's comment for why removal is the
        # reason.
        assert (
            keelson.construct_pubsub_key("rise", "crab", "container_status", "big")
            == "rise/@v0/crab/pubsub/container_status/big"
        )

    def test_the_registry_can_actually_decode_the_payload(self):
        # The registry builds a FRESH DescriptorPool, so the descriptor set must
        # embed google/protobuf/timestamp.proto (protoc --include_imports).
        # Without it this raises rather than returning a message.

        msg = ContainerHostStatus(control_enabled=True, sequence=3)
        msg.observed_at.FromNanoseconds(1_700_000_000_000_000_000)
        msg.containers.add(name="nginx")

        decoded = keelson.decode_protobuf_payload_from_type_name(
            msg.SerializeToString(), "keelson.ContainerHostStatus"
        )
        assert decoded.containers[0].name == "nginx"
        assert decoded.sequence == 3
        assert decoded.HasField("observed_at")


class TestPublishedStats:
    """The utilisation half. Pinned like the rest, plus one thing the other
    messages do not need: which fields are allowed to be ABSENT."""

    def test_container_resource_usage_field_numbers_are_pinned(self):

        assert field_numbers(ContainerResourceUsage) == {
            "name": 1,
            "id": 2,
            "cpu_load_pct": 3,
            "online_cpus": 4,
            "memory_used_bytes": 5,
            "memory_limit_bytes": 6,
            "memory_used_pct": 7,
            "network_rx_bytes": 8,
            "network_tx_bytes": 9,
            "network_rx_bytes_per_second": 10,
            "network_tx_bytes_per_second": 11,
            "block_read_bytes": 12,
            "block_write_bytes": 13,
            "block_read_bytes_per_second": 14,
            "block_write_bytes_per_second": 15,
            "pids_current": 16,
            "pids_limit": 17,
            "cpu_allocation_cores": 18,
            "cpu_shares": 19,
            "cpuset_cpus": 20,
            "cpu_throttled_periods": 21,
            "cpu_throttled_time_ns": 22,
            "sample_window_s": 23,
        }

    def test_container_host_stats_field_numbers_are_pinned(self):

        assert field_numbers(ContainerHostStats) == {
            "containers": 1,
            "observed_at": 2,
            "sequence": 3,
        }

    def test_the_first_two_numbers_match_the_published_state(self):
        # Same reason ContainerHostStatus lines up with ListContainersResponse:
        # the two carry the same two things, and a reader diffing them should
        # see them line up rather than wonder what the difference means.

        stats = field_numbers(ContainerHostStats)
        status = field_numbers(ContainerHostStatus)
        for name in ("containers", "observed_at"):
            assert stats[name] == status[name]

    def test_there_is_no_trigger_because_every_sample_is_a_change(self):

        assert "trigger" not in field_numbers(ContainerHostStats)

    @pytest.mark.parametrize(
        "field",
        [
            "cpu_load_pct",
            "memory_limit_bytes",
            "memory_used_pct",
            "network_rx_bytes",
            "network_tx_bytes",
            "network_rx_bytes_per_second",
            "network_tx_bytes_per_second",
            "block_read_bytes",
            "block_write_bytes",
            "block_read_bytes_per_second",
            "block_write_bytes_per_second",
            "online_cpus",
            "cpu_throttled_periods",
            "cpu_throttled_time_ns",
            "pids_limit",
            "cpu_allocation_cores",
            "cpu_shares",
            "sample_window_s",
        ],
    )
    def test_the_unknowable_fields_keep_explicit_presence(self, field):
        # THE TEST THAT STOPS A TIDY-UP FROM CHANGING THE MEANING. Dropping
        # `optional` compiles, passes every other test, and silently converts
        # "no reading yet" into "zero" -- a flat green line drawn through a gap.
        # Every field here has a case where nobody can know the value: a first
        # sample, a restart that zeroed the counters, a host-networked
        # container, an unconstrained one.

        assert ContainerResourceUsage.DESCRIPTOR.fields_by_name[field].has_presence

    def test_the_registry_names_this_payload(self):

        assert keelson.is_subject_well_known("container_stats")
        assert (
            keelson.get_subject_schema("container_stats")
            == "keelson.ContainerHostStats"
        )
        assert (
            keelson.construct_pubsub_key("rise", "crab", "container_stats", "big")
            == "rise/@v0/crab/pubsub/container_stats/big"
        )

    def test_the_registry_can_actually_decode_the_payload(self):

        msg = ContainerHostStats(sequence=7)
        msg.observed_at.FromNanoseconds(1_700_000_000_000_000_000)
        msg.containers.add(name="nginx", cpu_load_pct=250.5)

        decoded = keelson.decode_protobuf_payload_from_type_name(
            msg.SerializeToString(), "keelson.ContainerHostStats"
        )
        assert decoded.containers[0].name == "nginx"
        assert decoded.containers[0].cpu_load_pct == pytest.approx(250.5)
        assert decoded.sequence == 7

    def test_publishing_added_no_procedure(self):
        # The second published message, and the same guarantee: publishing is
        # not an interface change.
        service = DESCRIPTOR.services_by_name["ContainerControl"]
        assert [m.name for m in service.methods] == PROCEDURES
        assert "container_stats" not in [m.name for m in service.methods]
