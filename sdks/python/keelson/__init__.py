import time
import logging
from typing import Tuple
from pathlib import Path

import yaml
import parse
from google.protobuf.message import Message
from google.protobuf.message_factory import GetMessages
from google.protobuf.descriptor_pb2 import FileDescriptorSet
from google.protobuf.descriptor import Descriptor, FileDescriptor

# from Envelope_pb2 import Envelope
from .Envelope_pb2 import Envelope
from . import payloads as payloads

_PACKAGE_ROOT = Path(__file__).parent

# KEY HELPER FUNCTIONS
KEELSON_BASE_KEY_FORMAT = "{base_path}/@v0/{entity_id}"
KEELSON_PUB_SUB_KEY_FORMAT = KEELSON_BASE_KEY_FORMAT + "/pubsub/{subject}/{source_id}"
KEELSON_REQ_REP_KEY_FORMAT = (
    KEELSON_BASE_KEY_FORMAT + "/@rpc/{interface}/{version}/{procedure}/{responder_id}"
)

# Legacy coarse liveliness token (pre three-tier). Kept for the
# transition window so aggregators can watch old connectors; new code
# declares the three-tier tokens below instead.
KEELSON_LIVELINESS_KEY_FORMAT = KEELSON_BASE_KEY_FORMAT + "/pubsub/*/{source_id}"

# Three-tier liveliness key formats:
# - Source-level: "process with a producing role is present". The * sits
#   in the category slot (pubsub / @rpc / ...), conveying presence
#   applicable to any category. Note: as a verbatim chunk, @rpc is never
#   matched by the wildcard — the RPC tier is discovered via its own
#   token, not through this one.
# - Pubsub subject-level: same shape as a published pubsub key, declared
#   as a liveliness token (capability, not activity).
# - RPC interface-level: one token per served (interface, version).
KEELSON_SOURCE_LIVELINESS_KEY_FORMAT = KEELSON_BASE_KEY_FORMAT + "/*/{source_id}"
KEELSON_RPC_INTERFACE_LIVELINESS_KEY_FORMAT = (
    KEELSON_BASE_KEY_FORMAT + "/@rpc/{interface}/{version}/*/{source_id}"
)

PUB_SUB_KEY_PARSER = parse.compile(KEELSON_PUB_SUB_KEY_FORMAT)
REQ_REP_KEY_PARSER = parse.compile(KEELSON_REQ_REP_KEY_FORMAT)
LIVELINESS_KEY_PARSER = parse.compile(
    "{base_path}/@v0/{entity_id}/pubsub/*/{source_id}"
)
SOURCE_LIVELINESS_KEY_PARSER = parse.compile(
    "{base_path}/@v0/{entity_id}/*/{source_id}"
)
RPC_INTERFACE_LIVELINESS_KEY_PARSER = parse.compile(
    "{base_path}/@v0/{entity_id}/@rpc/{interface}/{version}/*/{source_id}"
)

logger = logging.getLogger("keelson")


def _is_valid_interface_version(version: str) -> bool:
    """An interface version chunk is ``v{N}`` with N a positive integer."""
    return (
        isinstance(version, str)
        and len(version) > 1
        and version[0] == "v"
        and version[1:].isdigit()
        and version[1] != "0"
    )


def construct_pubsub_key(
    base_path: str,
    entity_id: str,
    subject: str,
    source_id: str,
    target_id: str = None,
):
    """
    Construct a key expression for a publish subscribe interaction (Observable).

    Args:
        realm (str): The realm of the entity.
        entity_id (str): The entity id.
        subject (str): The subject of the interaction.
        source_id (str): The source id of the entity.
        target_id (str) (Optional): The id of the (optionally) referred entity

    Returns:
        key_expression (str):
            The constructed key.

    """

    if not is_subject_well_known(subject):
        logger.warning("Subject: %s is NOT well-known!", subject)

    key = KEELSON_PUB_SUB_KEY_FORMAT.format(
        base_path=base_path,
        entity_id=entity_id,
        subject=subject,
        source_id=source_id,
    )

    return key if not target_id else f"{key}/@target/{target_id}"


def construct_rpc_key(
    base_path: str,
    entity_id: str,
    interface: str,
    version: str,
    procedure: str,
    responder_id: str,
):
    """
    Construct a key expression for a request reply interaction (Queryable/RPC).

    Args:
        base_path (str): The realm of the entity.
        entity_id (str): The entity id.
        interface (str): The well-known RPC interface name (snake_case),
            registered in interfaces.yaml.
        version (str): The interface version chunk, ``v{N}`` (e.g. "v1").
        procedure (str): The procedure being called, as defined in the
            protobuf service for this interface version.
        responder_id (str): The responder id of the entity being targeted

    Returns:
        key_expression (str):
            The constructed key.

    ## Well-known interfaces

    [GITHUB DOC INTERFACES](https://github.com/RISE-Maritime/keelson/blob/main/messages/interfaces.yaml)

    """
    if not _is_valid_interface_version(version):
        raise ValueError(
            f"Interface version {version!r} is not of the required form v{{N}}"
        )
    if not is_interface_well_known(f"{interface}/{version}"):
        logger.warning("Interface: %s/%s is NOT well-known!", interface, version)

    return KEELSON_REQ_REP_KEY_FORMAT.format(
        base_path=base_path,
        entity_id=entity_id,
        interface=interface,
        version=version,
        procedure=procedure,
        responder_id=responder_id,
    )


def parse_pubsub_key(key: str):
    """
    Parse a key expression for a publish subscribe interaction (Observable).

    Args:
        key (str): The key expression to parse.

    Returns:
        Dict (dict):
            The parsed key expression.

        Dictionary keys:
            base_path (str):
                The base path of the entity.
            entity_id (str):
                The entity id.
            subject (str):
                The subject of the interaction.
            source_id (str):
                The source id of the entity.
            target_id (str or None):
                The target id if @target extension is present, None otherwise.
    """
    # Check for @target extension
    target_marker = "/@target/"
    target_id = None

    if target_marker in key:
        # Split the key at the @target marker
        base_key, target_id = key.split(target_marker, 1)
    else:
        base_key = key

    if not (res := PUB_SUB_KEY_PARSER.parse(base_key)):
        raise ValueError(
            f"Provided key {key} did not have the expected format {KEELSON_PUB_SUB_KEY_FORMAT}"
        )

    result = res.named.copy()
    result["target_id"] = target_id
    return result


def parse_rpc_key(key: str):
    """
    Parse a key expression for a request reply interaction (Queryable).

    Args:
        key (str): The key expression to parse.

    Returns:
        Dict (dict):
            The parsed key expression.

        Dictionary keys:
            realm (str):
                The realm of the entity.
            entity_id (str):
                The entity id.
            procedure (str):
                The procedure being called.
            target_id (str):
                The target id of the entity being called.

    """

    if not (res := REQ_REP_KEY_PARSER.parse(key)):
        raise ValueError(
            f"Provided key {key} did not have the expected format {KEELSON_REQ_REP_KEY_FORMAT}"
        )

    return res.named


def get_subject_from_pubsub_key(key: str) -> str:
    """
    Get the subject from a key expression for a publish subscribe interaction (Observable).
    """
    return parse_pubsub_key(key)["subject"]


def construct_liveliness_key(
    base_path: str,
    entity_id: str,
    source_id: str,
) -> str:
    """
    Construct a key expression for a liveliness token.

    Args:
        base_path (str): The base path of the entity.
        entity_id (str): The entity id.
        source_id (str): The source id of the entity.

    Returns:
        key_expression (str):
            The constructed liveliness key.
    """
    return KEELSON_LIVELINESS_KEY_FORMAT.format(
        base_path=base_path,
        entity_id=entity_id,
        source_id=source_id,
    )


def parse_liveliness_key(key: str) -> dict:
    """
    Parse a liveliness key expression.

    Args:
        key (str): The key expression to parse.

    Returns:
        Dict (dict):
            The parsed key expression.

        Dictionary keys:
            base_path (str):
                The base path of the entity.
            entity_id (str):
                The entity id.
            source_id (str):
                The source id of the entity.
    """
    if not (res := LIVELINESS_KEY_PARSER.parse(key)):
        raise ValueError(
            f"Provided key {key} did not have the expected format {KEELSON_LIVELINESS_KEY_FORMAT}"
        )

    return res.named


def construct_source_liveliness_key(
    base_path: str,
    entity_id: str,
    source_id: str,
) -> str:
    """
    Construct the source-level liveliness token key: "the process
    identified by entity_id/source_id is present on the bus as a producer
    in some category". Declared by any process with a producing role
    (publishes pubsub data and/or serves RPC); pure consumers (sinks,
    recorders, bridges) must not declare liveliness at all.

    The ``*`` occupies the category slot (``pubsub``, ``@rpc``, ...);
    which categories, subjects and interfaces the source actually
    provides is conveyed by the per-capability tokens.

    Args:
        base_path (str): The base path of the entity.
        entity_id (str): The entity id.
        source_id (str): The source id of the entity.

    Returns:
        key_expression (str):
            The constructed liveliness key.
    """
    return KEELSON_SOURCE_LIVELINESS_KEY_FORMAT.format(
        base_path=base_path,
        entity_id=entity_id,
        source_id=source_id,
    )


def parse_source_liveliness_key(key: str) -> dict:
    """
    Parse a source-level liveliness key expression into ``base_path``,
    ``entity_id`` and ``source_id``.
    """
    res = SOURCE_LIVELINESS_KEY_PARSER.parse(key)
    # Entity ids are single-chunk; a match that swallowed slashes into
    # entity_id is some other key shape (e.g. a legacy coarse token
    # '{entity}/pubsub/*/{source}' backtracked into entity_id='.../pubsub').
    if not res or "/" in res.named["entity_id"]:
        raise ValueError(
            f"Provided key {key} did not have the expected format "
            f"{KEELSON_SOURCE_LIVELINESS_KEY_FORMAT}"
        )

    return res.named


def construct_rpc_interface_liveliness_key(
    base_path: str,
    entity_id: str,
    interface: str,
    version: str,
    source_id: str,
) -> str:
    """
    Construct the liveliness token key for one served RPC
    ``(interface, version)`` pair.

    The ``*`` in the procedure slot follows the keelson convention for
    "any procedure in this scope"; under the full-interface rule the token
    is also a claim of full coverage of the interface version.

    Args:
        base_path (str): The base path of the entity.
        entity_id (str): The entity id.
        interface (str): The well-known RPC interface name.
        version (str): The interface version chunk, ``v{N}``.
        source_id (str): The source id of the entity.

    Returns:
        key_expression (str):
            The constructed liveliness key.
    """
    if not _is_valid_interface_version(version):
        raise ValueError(
            f"Interface version {version!r} is not of the required form v{{N}}"
        )
    if not is_interface_well_known(f"{interface}/{version}"):
        logger.warning("Interface: %s/%s is NOT well-known!", interface, version)

    return KEELSON_RPC_INTERFACE_LIVELINESS_KEY_FORMAT.format(
        base_path=base_path,
        entity_id=entity_id,
        interface=interface,
        version=version,
        source_id=source_id,
    )


def parse_rpc_interface_liveliness_key(key: str) -> dict:
    """
    Parse an RPC interface-level liveliness key expression into
    ``base_path``, ``entity_id``, ``interface``, ``version`` and
    ``source_id``.
    """
    if not (res := RPC_INTERFACE_LIVELINESS_KEY_PARSER.parse(key)):
        raise ValueError(
            f"Provided key {key} did not have the expected format "
            f"{KEELSON_RPC_INTERFACE_LIVELINESS_KEY_FORMAT}"
        )

    return res.named


# ENVELOPE HELPER FUNCTIONS
def enclose(payload: bytes, enclosed_at: int = None) -> bytes:
    """
    Enclose a payload in an envelope.

    Args:
        payload (bytes): The payload to enclose.
        enclosed_at (int): The time at which the envelope was enclosed.
        source_timestamp (int): The source timestamp of the payload.

    Returns:
        envelope (bytes):
            The enclosed envelope.
    """
    env: Envelope = Envelope()
    env.enclosed_at.FromNanoseconds(enclosed_at or time.time_ns())
    env.payload = payload
    return env.SerializeToString()


def uncover(message) -> Tuple[int, int, bytes]:
    """
    Uncover Keelson message that is an envelope

    Args:
        message (bytes): The envelope to uncover.

    Returns:
        Object ( int, int, bytes):
            received_at, enclosed_at, payload

    Example:

    ```
    received_at, enclosed_at, payload = uncover(message)
    ```

    """
    env = Envelope.FromString(message)

    return time.time_ns(), env.enclosed_at.ToNanoseconds(), env.payload


###### Payload handling #####

_PROTO_TYPES = {}
_SUBJECTS = {}
_INTERFACES = {}


def add_well_known_interfaces(path_to_interfaces_yaml: Path):
    """Load a ``{interface}/{version} -> protobuf service full name`` registry
    (interfaces.yaml) into the well-known interface set."""
    with path_to_interfaces_yaml.open() as fh:
        _INTERFACES.update(yaml.safe_load(fh) or {})


def is_interface_well_known(interface_and_version: str) -> bool:
    """True if ``{interface}/{version}`` (e.g. ``"replay_control/v1"``) is
    registered in the bundled interfaces.yaml."""
    return interface_and_version in _INTERFACES


def get_interface_service(interface_and_version: str) -> str:
    """Return the protobuf service full name registered for
    ``{interface}/{version}``."""
    return _INTERFACES[interface_and_version]


def add_well_known_subjects_and_proto_definitions(
    path_to_subjects_yaml: Path, path_to_proto_file_descriptor_set: Path = None
):
    with path_to_subjects_yaml.open() as fh:
        _SUBJECTS.update(yaml.safe_load(fh))

    if path_to_proto_file_descriptor_set is not None:
        with path_to_proto_file_descriptor_set.open("rb") as fh:
            _PROTO_TYPES.update(
                GetMessages(FileDescriptorSet.FromString(fh.read()).file)
            )


# Add the bundled well-known subjects and types
add_well_known_subjects_and_proto_definitions(
    _PACKAGE_ROOT / "subjects.yaml",
    _PACKAGE_ROOT / "payloads" / "protobuf_file_descriptor_set.bin",
)

# Add the bundled well-known RPC interfaces (tolerate an SDK generated
# before interfaces.yaml existed).
if (_interfaces_yaml := _PACKAGE_ROOT / "interfaces.yaml").exists():
    add_well_known_interfaces(_interfaces_yaml)


def _assemble_file_descriptor_set(descriptor: Descriptor) -> FileDescriptorSet:
    file_descriptor_set = FileDescriptorSet()
    seen_deps = set()

    def _add_file_descriptor(file_descriptor: FileDescriptor):
        for dep in file_descriptor.dependencies:
            if dep.name not in seen_deps:
                seen_deps.add(dep.name)
                _add_file_descriptor(dep)
        file_descriptor.CopyToProto(file_descriptor_set.file.add())

    _add_file_descriptor(descriptor.file)
    return file_descriptor_set


def get_protobuf_message_class_from_type_name(type_name: str) -> Message:
    return _PROTO_TYPES[type_name]


def decode_protobuf_payload_from_type_name(payload: bytes, type_name: str):
    return get_protobuf_message_class_from_type_name(type_name).FromString(payload)


def get_protobuf_file_descriptor_set_from_type_name(type_name: str) -> Descriptor:
    return _assemble_file_descriptor_set(
        get_protobuf_message_class_from_type_name(type_name).DESCRIPTOR
    )


# SUBJECTS HELPER FUNCTIONS
def is_subject_well_known(subject: str) -> bool:
    return subject in _SUBJECTS


def get_subject_schema(subject: str) -> str:
    return _SUBJECTS[subject]
