"""Tests for the container-fault acknowledgement (protocol-specification.md §7.4a, #369).

The schema half of §7.4a: the subject is registered and stored-shaped, the occurrence travels in the
record, an exit code of 0 is distinguishable from no exit code, and every client derives the same
`fault_id` from the same occurrence (the spec's reference vectors, recomputed here).
"""

import hashlib

import keelson

from keelson import qos
from keelson.payloads.ContainerFaultAcknowledgement_pb2 import (
    ContainerFaultAcknowledgement,
)
from keelson.payloads.ContainerHost_pb2 import ContainerHealthStatus


def fault_id(
    host_entity, host_source, container, health, exit_code, finished_at, restart_count
):
    """§7.4a.2, written out independently of any client."""
    parts = [
        host_entity,
        host_source,
        container,
        ContainerHealthStatus.Name(health),
        "" if exit_code is None else str(exit_code),
        finished_at or "",
        str(restart_count),
    ]
    return hashlib.sha256("\n".join(parts).encode("utf-8")).digest()[:16].hex()


def test_the_subject_resolves_to_its_type():
    assert keelson.is_subject_well_known("container_fault_ack")
    assert (
        keelson.get_subject_schema("container_fault_ack")
        == "keelson.ContainerFaultAcknowledgement"
    )


def test_it_travels_elevated_like_the_handover_record():
    assert qos.profile_name_for("container_fault_ack") == qos.profile_name_for(
        "checklist_handover"
    )


def test_the_record_carries_the_occurrence():
    names = {f.name for f in ContainerFaultAcknowledgement.DESCRIPTOR.fields}
    assert {
        "timestamp",
        "fault_id",
        "host_entity",
        "host_source",
        "container",
        "health",
        "exit_code",
        "finished_at",
        "restart_count",
        "acknowledged",
        "operator_id",
    } <= names


def test_the_fault_id_is_a_single_string_token():
    field = ContainerFaultAcknowledgement.DESCRIPTOR.fields_by_name["fault_id"]
    assert field.type == field.TYPE_STRING


def test_an_exit_code_of_zero_is_not_an_absent_one():
    record = ContainerFaultAcknowledgement()
    assert not record.HasField("exit_code")
    record.exit_code = 0
    assert record.HasField("exit_code")
    decoded = ContainerFaultAcknowledgement.FromString(record.SerializeToString())
    assert decoded.HasField("exit_code") and decoded.exit_code == 0


def test_the_spec_reference_vectors():
    unhealthy = ContainerHealthStatus.Value("CONTAINER_HEALTH_STATUS_UNHEALTHY")
    assert (
        fault_id(
            "slipway-ted-usv-alpha",
            "container-control",
            "rutx-modem",
            unhealthy,
            143,
            "2026-11-05T12:00:00.000Z",
            3,
        )
        == "3666e9be238bd3a1f0f06f47091ec2ef"
    )
    assert (
        fault_id(
            "slipway-ted-usv-alpha",
            "container-control",
            "rutx-modem",
            unhealthy,
            None,
            None,
            0,
        )
        == "8c9049a757cb00efda597aa49e67afcc"
    )


def test_a_new_occurrence_is_a_new_fault_id():
    unhealthy = ContainerHealthStatus.Value("CONTAINER_HEALTH_STATUS_UNHEALTHY")
    base = ("h", "s", "c", unhealthy, 143, "2026-11-05T12:00:00.000Z", 3)
    seen = {fault_id(*base)}
    for i, changed in [(4, 255), (5, "2026-11-05T13:00:00.000Z"), (6, 4)]:
        variant = list(base)
        variant[i] = changed
        seen.add(fault_id(*variant))
    assert len(seen) == 4
    # exit code 0 and no exit code are different occurrences
    assert fault_id("h", "s", "c", unhealthy, 0, None, 0) != fault_id(
        "h", "s", "c", unhealthy, None, None, 0
    )


def test_an_undo_is_a_write_not_a_delete():
    record = ContainerFaultAcknowledgement(
        fault_id="x", acknowledged=False, operator_id="a@ROC-A"
    )
    decoded = ContainerFaultAcknowledgement.FromString(record.SerializeToString())
    assert decoded.acknowledged is False and decoded.operator_id == "a@ROC-A"
