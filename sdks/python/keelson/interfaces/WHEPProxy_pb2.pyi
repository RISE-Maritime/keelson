from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from typing import ClassVar as _ClassVar, Optional as _Optional

DESCRIPTOR: _descriptor.FileDescriptor

class WHEPRequest(_message.Message):
    __slots__ = ("path", "sdp")
    PATH_FIELD_NUMBER: _ClassVar[int]
    SDP_FIELD_NUMBER: _ClassVar[int]
    path: str
    sdp: str
    def __init__(self, path: _Optional[str] = ..., sdp: _Optional[str] = ...) -> None: ...

class WHEPResponse(_message.Message):
    __slots__ = ("sdp",)
    SDP_FIELD_NUMBER: _ClassVar[int]
    sdp: str
    def __init__(self, sdp: _Optional[str] = ...) -> None: ...
