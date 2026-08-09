"""Runtime introspection registry for keelson RPC interfaces.

The pubsub side of keelson has always had a complete descriptor pipeline
(subjects.yaml + bundled FileDescriptorSet + ``get_subject_schema`` /
``decode_protobuf_payload_from_type_name``). This module gives the RPC
interface side the symmetric surface: generic clients (debug UIs,
bridges, third-party tools) can enumerate interfaces and procedures,
resolve request/response schemas, and invoke procedures — all without
static knowledge of any interface.

Connectors implementing a *specific* interface should keep importing the
generated stubs (``keelson.interfaces.X_pb2``) directly; this registry
is the introspection path, not a replacement for them.

The registry is derived, not authored: interface names and versions come
from the bundled ``interfaces.yaml``; procedures and schemas come from
the bundled interface ``FileDescriptorSet``
(``keelson/interfaces/protobuf_file_descriptor_set.bin``), which the SDK
generator emits from ``interfaces/*.proto``.
"""

from functools import lru_cache
from pathlib import Path
from typing import Any, List, Optional, Tuple

from google.protobuf import descriptor_pool, message_factory
from google.protobuf.descriptor import Descriptor, ServiceDescriptor
from google.protobuf.descriptor_pb2 import FileDescriptorSet
from google.protobuf.message import Message

import keelson

_PACKAGE_ROOT = Path(__file__).parent
_DESCRIPTOR_SET_PATH = _PACKAGE_ROOT / "interfaces" / "protobuf_file_descriptor_set.bin"


class RpcError(Exception):
    """A typed error reply (``ErrorResponse``) from an RPC callee."""

    def __init__(self, code: int, code_name: str, description: str):
        super().__init__(f"{code_name}: {description}")
        self.code = code
        self.code_name = code_name
        self.description = description


@lru_cache(maxsize=1)
def _pool() -> descriptor_pool.DescriptorPool:
    """Private descriptor pool built from the bundled interface
    FileDescriptorSet (kept separate from the default pool, which already
    holds the generated ``*_pb2`` registrations)."""
    pool = descriptor_pool.DescriptorPool()
    with _DESCRIPTOR_SET_PATH.open("rb") as fh:
        fds = FileDescriptorSet.FromString(fh.read())
    for file_proto in fds.file:
        pool.Add(file_proto)
    return pool


def list_interfaces() -> List[Tuple[str, str]]:
    """All well-known ``(interface, version)`` pairs of this SDK release."""
    out = []
    for key in keelson._INTERFACES:
        interface, _, version = key.rpartition("/")
        out.append((interface, version))
    return sorted(out)


def get_interface_descriptor(interface: str, version: str) -> ServiceDescriptor:
    """The protobuf ServiceDescriptor for ``(interface, version)``."""
    service_full_name = keelson.get_interface_service(f"{interface}/{version}")
    return _pool().FindServiceByName(service_full_name)


def get_procedures(interface: str, version: str) -> List[str]:
    """Procedure (method) names defined by ``(interface, version)``."""
    return [m.name for m in get_interface_descriptor(interface, version).methods]


def get_procedure_schemas(
    interface: str, version: str, procedure: str
) -> Tuple[Descriptor, Descriptor]:
    """The ``(request, response)`` message Descriptors of one procedure."""
    service = get_interface_descriptor(interface, version)
    method = service.methods_by_name[procedure]
    return method.input_type, method.output_type


@lru_cache(maxsize=None)
def get_procedure_message_classes(
    interface: str, version: str, procedure: str
) -> Tuple[type, type]:
    """The generated-message-equivalent ``(request, response)`` classes of
    one procedure, built dynamically from the registry pool."""
    request_desc, response_desc = get_procedure_schemas(interface, version, procedure)
    return (
        message_factory.GetMessageClass(request_desc),
        message_factory.GetMessageClass(response_desc),
    )


def get_interfaces_file_descriptor_set() -> bytes:
    """The raw bundled interface FileDescriptorSet bytes (e.g. for
    protocols that carry descriptors, like Foxglove service
    advertisements)."""
    return _DESCRIPTOR_SET_PATH.read_bytes()


def invoke_procedure(
    session,
    base_path: str,
    entity_id: str,
    interface: str,
    version: str,
    procedure: str,
    responder_id: str,
    request: Any = b"",
    timeout: Optional[float] = 10.0,
) -> Message:
    """Call one RPC procedure and return the decoded response message.

    Builds the queryable key per the protocol specification, sends the
    serialized ``request`` (a protobuf message or pre-serialized bytes),
    awaits the reply, and decodes it per the registered response schema.

    Single-reply semantics: the first reply wins (every keelson interface
    currently defines single-reply procedures). No reply within
    ``timeout`` raises ``TimeoutError``; a ``reply_err`` carrying an
    ``ErrorResponse`` raises :class:`RpcError`.
    """
    from keelson.interfaces.ErrorResponse_pb2 import ErrorResponse

    key = keelson.construct_rpc_key(
        base_path, entity_id, interface, version, procedure, responder_id
    )
    payload = (
        request
        if isinstance(request, (bytes, bytearray))
        else request.SerializeToString()
    )
    _, response_class = get_procedure_message_classes(interface, version, procedure)

    get_kwargs = {"payload": payload}
    if timeout is not None:
        get_kwargs["timeout"] = timeout

    for reply in session.get(key, **get_kwargs):
        if reply.ok is not None:
            return response_class.FromString(reply.ok.payload.to_bytes())
        err = ErrorResponse()
        try:
            err.ParseFromString(reply.err.payload.to_bytes())
        except Exception:
            raise RpcError(0, "UNSPECIFIED", "<undecodable ErrorResponse>") from None
        raise RpcError(
            err.code, ErrorResponse.Code.Name(err.code), err.error_description
        )

    raise TimeoutError(f"No reply on {key} within {timeout}s")
