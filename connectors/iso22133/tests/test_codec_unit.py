"""ISO 22133 MONR codec and state machine.

The field layout under test was read from RI-SE/iso22133 (include/monr.h,
header.h, footer.h, defines.h), so these tests are checking this module against
that layout — they are NOT evidence of interoperability with a third-party
object, which needs a real object on the wire.
"""

import pytest

from iso22133_connector.codec import (
    decode_monr,
    encode_monr,
    decode_error_flags,
    DecodeError,
    ISO_SYNC_WORD,
    MONR_SIZE,
    MESSAGE_ID_MONR,
)
from iso22133_connector import states


class TestFraming:
    def test_monr_is_sixty_bytes(self):
        """18 byte header + 40 byte body + 2 byte CRC footer."""
        assert MONR_SIZE == 60
        assert len(encode_monr(transmitter_id=1)) == 60

    def test_sync_word_and_message_id(self):
        frame = encode_monr(transmitter_id=7)
        assert int.from_bytes(frame[0:2], "little") == ISO_SYNC_WORD
        assert int.from_bytes(frame[16:18], "little") == MESSAGE_ID_MONR

    def test_rejects_a_short_frame(self):
        with pytest.raises(DecodeError, match="too short"):
            decode_monr(b"\x7f\x7e" + b"\x00" * 10)

    def test_rejects_a_bad_sync_word(self):
        frame = bytearray(encode_monr(transmitter_id=1))
        frame[0:2] = (0x1234).to_bytes(2, "little")
        with pytest.raises(DecodeError, match="sync word"):
            decode_monr(bytes(frame))

    def test_rejects_another_message_type(self):
        """A HEAB arriving on the MONR port must not be half-parsed."""
        frame = bytearray(encode_monr(transmitter_id=1))
        frame[16:18] = (0x0005).to_bytes(2, "little")  # MESSAGE_ID_HEAB
        with pytest.raises(DecodeError, match="not a MONR"):
            decode_monr(bytes(frame))


class TestScaling:
    """Positions are millimetres, speeds centimetres/second, angles centidegrees."""

    def test_position_round_trips_in_metres(self):
        monr = decode_monr(encode_monr(transmitter_id=1, x_m=12.345, y_m=-6.789))
        assert monr.x_m == pytest.approx(12.345, abs=1e-3)
        assert monr.y_m == pytest.approx(-6.789, abs=1e-3)

    def test_speed_round_trips(self):
        monr = decode_monr(encode_monr(transmitter_id=1, longitudinal_speed_mps=4.5))
        assert monr.longitudinal_speed_mps == pytest.approx(4.5, abs=0.01)

    def test_yaw_round_trips(self):
        monr = decode_monr(encode_monr(transmitter_id=1, yaw_deg=123.45))
        assert monr.yaw_deg == pytest.approx(123.45, abs=0.01)

    def test_unavailable_is_none_not_zero(self):
        """The distinction the whole codec turns on: a sentinel is not a value.

        Flattening 'unavailable' to 0.0 would put a test object at the origin
        of the test area, moving at zero, pointing north — a plausible-looking
        reading that was never measured.
        """
        monr = decode_monr(
            encode_monr(
                transmitter_id=1,
                x_m=None,
                y_m=None,
                z_m=None,
                yaw_deg=None,
                longitudinal_speed_mps=None,
            )
        )
        assert monr.x_m is None
        assert monr.y_m is None
        assert monr.yaw_deg is None
        assert monr.longitudinal_speed_mps is None

    def test_zero_is_still_zero(self):
        monr = decode_monr(
            encode_monr(transmitter_id=1, x_m=0.0, longitudinal_speed_mps=0.0)
        )
        assert monr.x_m == 0.0
        assert monr.longitudinal_speed_mps == 0.0


class TestErrorFlags:
    def test_each_bit(self):
        assert decode_error_flags(0x80)["abort_request"]
        assert decode_error_flags(0x40)["outside_geofence"]
        assert decode_error_flags(0x20)["bad_positioning_accuracy"]
        assert decode_error_flags(0x10)["engine_fault"]
        assert decode_error_flags(0x08)["battery_fault"]
        assert decode_error_flags(0x04)["other"]
        assert decode_error_flags(0x02)["sync_point_ended"]
        assert decode_error_flags(0x01)["vendor_specific"]

    def test_clear(self):
        assert not any(decode_error_flags(0x00).values())

    def test_several_at_once(self):
        flags = decode_error_flags(0x80 | 0x08)
        assert flags["abort_request"] and flags["battery_fault"]
        assert not flags["outside_geofence"]

    def test_raw_byte_survives_decode(self):
        """Kept so a bit this build does not know about is not lost."""
        monr = decode_monr(encode_monr(transmitter_id=1, error_status=0xC1))
        assert monr.error_status == 0xC1


class TestStates:
    def test_values_match_the_standard(self):
        """Renumbering these would silently corrupt every stored test record."""
        assert (states.OFF, states.INIT, states.ARMED, states.DISARMED) == (0, 1, 2, 3)
        assert (states.RUNNING, states.POSTRUN, states.REMOTE_CONTROLLED) == (4, 5, 6)
        assert (states.ABORTING, states.PRE_ARMING, states.PRE_RUNNING) == (7, 8, 9)
        assert states.UNAVAILABLE == 255

    def test_state_survives_a_round_trip(self):
        monr = decode_monr(encode_monr(transmitter_id=1, state=states.PRE_ARMING))
        assert monr.state == states.PRE_ARMING

    @pytest.mark.parametrize(
        "previous,current",
        [
            (states.OFF, states.INIT),
            (states.INIT, states.DISARMED),
            (states.DISARMED, states.PRE_ARMING),
            (states.PRE_ARMING, states.ARMED),
            (states.ARMED, states.PRE_RUNNING),
            (states.PRE_RUNNING, states.RUNNING),
            (states.RUNNING, states.POSTRUN),
            (states.POSTRUN, states.DISARMED),
        ],
    )
    def test_the_normal_run_is_legal(self, previous, current):
        assert states.is_legal_transition(previous, current)

    @pytest.mark.parametrize(
        "previous",
        [
            states.INIT,
            states.DISARMED,
            states.PRE_ARMING,
            states.ARMED,
            states.PRE_RUNNING,
            states.RUNNING,
            states.POSTRUN,
            states.REMOTE_CONTROLLED,
        ],
    )
    def test_abort_is_always_reachable(self, previous):
        """An abort some state could refuse would not be an abort."""
        assert states.is_legal_transition(previous, states.ABORTING)

    @pytest.mark.parametrize(
        "previous,current",
        [
            (states.DISARMED, states.ARMED),  # must pass through PRE_ARMING
            (states.ARMED, states.RUNNING),  # must pass through PRE_RUNNING
            (states.RUNNING, states.ARMED),  # cannot go back
            (states.OFF, states.RUNNING),
        ],
    )
    def test_illegal_transitions_are_caught(self, previous, current):
        assert not states.is_legal_transition(previous, current)

    def test_holding_the_same_state_is_legal(self):
        """MONR streams at rate, so most samples repeat the previous state."""
        assert states.is_legal_transition(states.RUNNING, states.RUNNING)

    def test_first_sample_is_not_a_violation(self):
        """A connector restart has nothing to compare against; calling that a
        violation would put a fault on the record every time it is bounced."""
        assert states.is_legal_transition(states.UNAVAILABLE, states.RUNNING)


class TestGeodetics:
    def test_origin_maps_to_the_origin(self):
        lat, lon = states.enu_to_wgs84(57.7731, 12.7708, 0.0, 0.0)
        assert lat == pytest.approx(57.7731)
        assert lon == pytest.approx(12.7708)

    def test_north_increases_latitude(self):
        lat, _ = states.enu_to_wgs84(57.7731, 12.7708, 0.0, 111.32)
        assert lat > 57.7731
        assert lat == pytest.approx(57.7741, abs=1e-3)

    def test_east_increases_longitude(self):
        _, lon = states.enu_to_wgs84(57.7731, 12.7708, 100.0, 0.0)
        assert lon > 12.7708

    def test_longitude_scaling_accounts_for_latitude(self):
        """100 m east is a bigger longitude step at 58N than at the equator."""
        _, near_pole = states.enu_to_wgs84(57.7731, 0.0, 100.0, 0.0)
        _, at_equator = states.enu_to_wgs84(0.0, 0.0, 100.0, 0.0)
        assert near_pole > at_equator

    def test_knots(self):
        assert states.speed_to_knots(1.0) == pytest.approx(1.9438, abs=1e-3)
        assert states.speed_to_knots(None) is None

    def test_heading_is_normalised(self):
        assert states.heading_from_yaw(370.0) == pytest.approx(10.0)
        assert states.heading_from_yaw(-10.0) == pytest.approx(350.0)
        assert states.heading_from_yaw(None) is None
