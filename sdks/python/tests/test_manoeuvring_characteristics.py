"""ManoeuvringCharacteristics: the wheelhouse poster, measured, with where it came from.

The rules a `.proto` cannot state on its own are held here: absence is not zero
(every measured figure is optional), the provenance is structural, and a record
survives an envelope round trip.
"""

import keelson
from keelson.payloads.ManoeuvringCharacteristics_pb2 import (
    ManoeuvreTrackPoint,
    ManoeuvringCharacteristics,
)

M = ManoeuvringCharacteristics


def _example():
    m = M(entity_id="sim-bulk-alfa")
    m.provenance.kind = M.Provenance.KIND_SIMULATION
    m.provenance.method = "l2-mmg-seakeeping"
    m.condition.name = "design"
    turn = m.turning_circles.add(
        side=M.SIDE_STARBOARD, rudder_deg=35.0, approach_speed_knots=11.0
    )
    turn.advance_m = 163.7
    turn.tactical_diameter_m = 159.7
    turn.track.append(
        ManoeuvreTrackPoint(
            t_s=0.0, along_m=0.0, across_m=0.0, heading_deg=0.0, speed_knots=11.0
        )
    )
    m.zigzags.add(angle_deg=10.0, first_overshoot_deg=9.3)
    stop = m.stopping.add(manoeuvre="engine stopped", approach_speed_knots=11.0)
    stop.head_reach_m = 554.9
    m.speed_table.add(engine_order="100 %", shaft_rpm=284.4, speed_knots=11.0)
    return m


def test_a_figure_not_measured_is_absent_not_zero():
    turn = _example().turning_circles[0]
    assert turn.HasField("tactical_diameter_m")
    assert not turn.HasField(
        "time_to_360_deg_s"
    ), "a turn that was not timed to 360 says nothing"
    turn.transfer_m = 0.0
    assert turn.HasField("transfer_m"), "a measured zero survives as a zero"
    zigzag = _example().zigzags[0]
    assert not zigzag.HasField("third_overshoot_deg")


def test_every_measured_figure_is_optional():
    for message in (M.TurningCircle, M.ZigZag, M.Stopping, M.SpeedRow, M.Condition):
        for field in message.DESCRIPTOR.fields:
            if field.type == field.TYPE_DOUBLE and field.name not in (
                "rudder_deg",
                "approach_speed_knots",
                "angle_deg",
            ):
                assert (
                    field.has_presence
                ), f"{message.DESCRIPTOR.name}.{field.name} must be optional"


def test_provenance_is_structural_and_unspecified_is_zero():
    assert M.Provenance.KIND_UNSPECIFIED == 0
    assert M().provenance.kind == M.Provenance.KIND_UNSPECIFIED
    assert {
        "KIND_SEA_TRIAL",
        "KIND_MODEL_TEST",
        "KIND_SIMULATION",
        "KIND_ESTIMATE",
    } <= set(M.Provenance.Kind.keys())


def test_it_survives_an_envelope_round_trip():
    original = _example()
    _, _, payload = keelson.uncover(keelson.enclose(original.SerializeToString()))
    back = M()
    back.ParseFromString(payload)
    assert back == original
    assert back.stopping[0].head_reach_m == 554.9


def test_the_subject_is_well_known():
    assert (
        keelson.get_subject_schema("manoeuvring_characteristics")
        == "keelson.ManoeuvringCharacteristics"
    )
