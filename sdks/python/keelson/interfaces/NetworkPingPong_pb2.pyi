from google.protobuf import timestamp_pb2 as _timestamp_pb2
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from typing import ClassVar as _ClassVar, Mapping as _Mapping, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class NetworkPing(_message.Message):
    __slots__ = ("sent_at", "payload")
    SENT_AT_FIELD_NUMBER: _ClassVar[int]
    PAYLOAD_FIELD_NUMBER: _ClassVar[int]
    sent_at: _timestamp_pb2.Timestamp
    payload: bytes
    def __init__(self, sent_at: _Optional[_Union[_timestamp_pb2.Timestamp, _Mapping]] = ..., payload: _Optional[bytes] = ...) -> None: ...

class NetworkPong(_message.Message):
    __slots__ = ("sent_at", "ping", "ping_received_at")
    SENT_AT_FIELD_NUMBER: _ClassVar[int]
    PING_FIELD_NUMBER: _ClassVar[int]
    PING_RECEIVED_AT_FIELD_NUMBER: _ClassVar[int]
    sent_at: _timestamp_pb2.Timestamp
    ping: NetworkPing
    ping_received_at: _timestamp_pb2.Timestamp
    def __init__(self, sent_at: _Optional[_Union[_timestamp_pb2.Timestamp, _Mapping]] = ..., ping: _Optional[_Union[NetworkPing, _Mapping]] = ..., ping_received_at: _Optional[_Union[_timestamp_pb2.Timestamp, _Mapping]] = ...) -> None: ...
