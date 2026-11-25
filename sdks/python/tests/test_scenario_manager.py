"""Tests for the scenario manager: stations on every command, and replay.

A run has one scenario manager and any number of stations commanding it. These
pin what a .proto cannot state on its own:

- every command names its station, and the change from google.protobuf.Empty
  to a request message is wire-compatible (an old caller is read as station "");
- the record of a command (ScenarioEvent) and the broadcast of who may command
  (ScenarioStatus.stations) have their field numbers;
- replay is commanded through the manager: LoadRequest.recording, and
  scenario_replay/v1 registered beside scenario_control/v1.
"""

import keelson
import pytest
from google.protobuf.empty_pb2 import Empty

from keelson.interfaces import get_interface_descriptor
from keelson.interfaces.ScenarioCheckpoint_pb2 import RestoreRequest, SaveRequest
from keelson.interfaces.ScenarioControl_pb2 import (
    LoadRequest,
    PauseRequest,
    ResetRequest,
    SetRateRequest,
    StartRequest,
    StepRequest,
    StopRequest,
)
from keelson.interfaces.ScenarioReplay_pb2 import (
    SeekRequest,
    SetLoopRequest,
    SetSpeedRequest,
)
from keelson.payloads.ScenarioEvent_pb2 import ScenarioEvent
from keelson.payloads.ScenarioStatus_pb2 import ScenarioStatus

COMMANDS = {
    "start": StartRequest,
    "pause": PauseRequest,
    "stop": StopRequest,
    "reset": ResetRequest,
}


@pytest.mark.parametrize("procedure, request_type", sorted(COMMANDS.items()))
def test_an_old_empty_request_reads_as_no_station(procedure, request_type):
    """A caller still sending google.protobuf.Empty parses, as station ""."""
    request = request_type.FromString(Empty().SerializeToString())
    assert request.station_id == ""


@pytest.mark.parametrize("procedure, request_type", sorted(COMMANDS.items()))
def test_the_lifecycle_commands_take_their_own_request(procedure, request_type):
    service = get_interface_descriptor("scenario_control", "v1")
    method = service.methods_by_name[procedure]
    assert method.input_type.full_name == request_type.DESCRIPTOR.full_name


@pytest.mark.parametrize(
    "message, number",
    [
        (StartRequest, 1),
        (PauseRequest, 1),
        (StopRequest, 1),
        (ResetRequest, 1),
        (LoadRequest, 3),
        (StepRequest, 2),
        (SetRateRequest, 2),
        (SaveRequest, 4),
        (RestoreRequest, 2),
        (SeekRequest, 2),
        (SetSpeedRequest, 2),
        (SetLoopRequest, 2),
    ],
)
def test_every_command_names_its_station(message, number):
    field = message.DESCRIPTOR.fields_by_name["station_id"]
    assert field.number == number
    assert field.type == field.TYPE_STRING


def test_load_leaves_field_2_for_start_time():
    """#330 adds LoadRequest.start_time as field 2; this PR's fields take
    neither it nor each other's number."""
    by_number = {f.number: f.name for f in LoadRequest.DESCRIPTOR.fields}
    assert by_number.get(2) in (None, "start_time")
    assert LoadRequest.DESCRIPTOR.fields_by_name["recording"].number == 4


def test_the_record_names_the_station():
    field = ScenarioEvent.DESCRIPTOR.fields_by_name["station_id"]
    assert field.number == 9
    event = ScenarioEvent(action="pause", station_id="roc-1")
    assert ScenarioEvent.FromString(event.SerializeToString()).station_id == "roc-1"


def test_the_status_says_who_may_command():
    field = ScenarioStatus.DESCRIPTOR.fields_by_name["stations"]
    assert field.number == 18  # 17 is ScenarioStatus.cast (open PR #375)
    assert field.is_repeated
    assert list(ScenarioStatus().stations) == []  # empty means open


def test_replay_is_a_mode_of_the_status():
    assert ScenarioStatus.Mode.Value("REPLAY") == 4


def test_scenario_replay_is_registered_beside_scenario_control():
    assert keelson.is_interface_well_known("scenario_replay/v1")
    service = get_interface_descriptor("scenario_replay", "v1")
    assert [m.name for m in service.methods] == [
        "list_recordings",
        "seek",
        "set_speed",
        "set_loop",
    ]
    # A read takes no station: list_recordings is open to anyone.
    request = service.methods_by_name["list_recordings"].input_type
    assert "station_id" not in request.fields_by_name
