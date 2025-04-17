from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from typing import ClassVar as _ClassVar, Optional as _Optional

DESCRIPTOR: _descriptor.FileDescriptor

class SuccessResponse(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class ErrorResponse(_message.Message):
    __slots__ = ("error_description",)
    ERROR_DESCRIPTION_FIELD_NUMBER: _ClassVar[int]
    error_description: str
    def __init__(self, error_description: _Optional[str] = ...) -> None: ...
