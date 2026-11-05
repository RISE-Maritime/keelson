"""`vessel_outline_m`: a hull's outline as a static, body-frame subject."""

import pytest

import keelson
from keelson.payloads.VesselOutline_pb2 import VesselOutline

pytestmark = pytest.mark.unit

# A barge-like ring: x out of the bow, y to starboard, origin amidships.
RING = [(30.0, 0.0), (25.0, 6.0), (-30.0, 6.0), (-30.0, -6.0), (25.0, -6.0)]


def test_the_subject_is_well_known_and_carries_a_vessel_outline():
    assert keelson.is_subject_well_known("vessel_outline_m")
    assert keelson.get_subject_schema("vessel_outline_m") == "keelson.VesselOutline"


def test_an_outline_survives_the_envelope_point_for_point():
    outline = VesselOutline()
    outline.timestamp.FromNanoseconds(1_700_000_000_000_000_000)
    for x, y in RING:
        point = outline.points.add()
        point.x, point.y = x, y

    envelope = keelson.enclose(outline.SerializeToString())
    _, _, payload = keelson.uncover(envelope)
    message_class = keelson.get_protobuf_message_class_from_type_name(
        keelson.get_subject_schema("vessel_outline_m")
    )
    decoded = message_class.FromString(payload)

    assert [(p.x, p.y) for p in decoded.points] == RING
    assert decoded.timestamp.ToNanoseconds() == 1_700_000_000_000_000_000
