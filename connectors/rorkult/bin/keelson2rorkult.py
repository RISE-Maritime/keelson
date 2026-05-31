#!/usr/bin/env python3

"""Bidirectional Keelson <-> rorkult-MCU connector (skeleton).

Bridges the Keelson bus to an external actuation MCU over TCP:
commanded setpoints (steering, throttle, ...) flow MCU-bound; the
MCU's measured actuator state and heartbeat flow back. Single process
per MCU connection — run two processes for two MCUs.

Status: skeleton.

- Zenoh session, liveliness token, and graceful shutdown are wired up.
- TCP transport with bounded exponential reconnect backoff runs on a
  dedicated asyncio thread.
- ``entity_health`` is published periodically and on every MCU-link
  state transition (HEALTH_NOMINAL when connected, HEALTH_CRITICAL
  otherwise).
- RPC handlers for ``VehicleControl`` (set/get_control_mapping) and
  ``VehicleLifecycle`` (arm / set_mode / emergency_stop) are declared
  as queryables but respond with ``COMMAND_RESULT_UNSUPPORTED`` (or
  ``reply_err`` where the response message has no field for it) until
  the MCU wire format (framing + protocol) is decided.
"""

# pylint: disable=invalid-name

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import threading
import traceback
from pathlib import Path
from typing import Any, Callable, NamedTuple

import zenoh

import keelson
from keelson import construct_pubsub_key, enclose
from keelson.interfaces.ErrorResponse_pb2 import ErrorResponse
from keelson.interfaces.VehicleCommon_pb2 import CommandResult
from keelson.interfaces.VehicleControl_pb2 import (
    ControlAxisMapping,
)
from keelson.interfaces.VehicleLifecycle_pb2 import (
    ArmResponse,
    EmergencyStopResponse,
    SetModeResponse,
)
from keelson.scaffolding import (
    GracefulShutdown,
    add_common_arguments,
    create_zenoh_config,
    declare_liveliness_token,
    setup_logging,
)

# Add the connector's package dir to sys.path so the sibling ``rorkult``
# package is importable when running from source. The Docker image
# installs the package proper, so this prepend is a dev-mode convenience
# (matches the pattern used by entity_health).
_PKG_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PKG_ROOT))
from rorkult.framing import Framing, PassthroughFraming  # noqa: E402
from rorkult.health import HealthState, build_entity_health  # noqa: E402
from rorkult.transport import ReconnectBackoff, TcpTransport  # noqa: E402

logger = logging.getLogger("keelson2rorkult")

# Detail string used by every stubbed RPC handler. Centralised so the
# message moves in one place once framing lands.
_STUB_DETAIL = "rorkult skeleton: MCU framing not yet implemented"


# ---------------------------------------------------------------------------
# RPC dispatch -- request/response procedures backed by Zenoh queryables.
#
# Same architectural shape as mavlink2keelson: one queryable per
# procedure; each callback runs synchronously on its dedicated callback
# thread (zenoh-python spawns one thread per queryable). Different
# procedures therefore run concurrently; same-procedure calls
# serialise. v1 handlers do not touch the MCU at all -- they just
# return UNSUPPORTED -- so no asyncio bridging is needed yet. That
# will become important when framing lands.
# ---------------------------------------------------------------------------


class RpcOp(NamedTuple):
    query: Any  # zenoh.Query
    procedure: str
    reply_key: str
    request_bytes: bytes


def _reply_err(query, msg: str) -> None:
    try:
        query.reply_err(ErrorResponse(error_description=msg).SerializeToString())
    except Exception:  # noqa: BLE001
        logger.exception("Failed to reply_err on RPC")


def _reply_unsupported(op: RpcOp, response_cls) -> None:
    """Build a UNSUPPORTED CommandResult response and reply with it.

    The response class must have ``result`` (CommandResult) and
    ``detail`` (string) fields -- every ``Vehicle*Response`` does.
    """
    resp = response_cls(
        result=CommandResult.COMMAND_RESULT_UNSUPPORTED,
        detail=_STUB_DETAIL,
    )
    op.query.reply(op.reply_key, resp.SerializeToString())


# ---- RPC handlers (all stubs in v1) --------------------------------------


def _handle_set_control_mapping(op: RpcOp) -> None:
    # Validate the request decodes -- catches malformed callers even at
    # the stub stage -- then surface the unimplemented framing as an err
    # (the response type ``ControlAxisMappingAck`` has no result/detail
    # field, so reply_err is the honest answer).
    try:
        ControlAxisMapping().ParseFromString(op.request_bytes)
    except Exception as exc:  # noqa: BLE001
        _reply_err(op.query, f"set_control_mapping: malformed request: {exc}")
        return
    _reply_err(op.query, f"set_control_mapping: {_STUB_DETAIL}")


def _handle_get_control_mapping(op: RpcOp) -> None:
    _reply_err(op.query, f"get_control_mapping: {_STUB_DETAIL}")


def _handle_arm(op: RpcOp) -> None:
    _reply_unsupported(op, ArmResponse)


def _handle_set_mode(op: RpcOp) -> None:
    _reply_unsupported(op, SetModeResponse)


def _handle_emergency_stop(op: RpcOp) -> None:
    _reply_unsupported(op, EmergencyStopResponse)


# Procedure name -> handler. Order also drives the "Declared RPC
# queryable" log lines.
_RPC_HANDLERS: dict[str, Callable[[RpcOp], None]] = {
    "set_control_mapping": _handle_set_control_mapping,
    "get_control_mapping": _handle_get_control_mapping,
    "arm": _handle_arm,
    "set_mode": _handle_set_mode,
    "emergency_stop": _handle_emergency_stop,
}


def _make_rpc_handler(procedure: str, reply_key: str):
    """Build the Zenoh queryable callback for ``procedure``."""
    handler = _RPC_HANDLERS[procedure]

    def _callback(query) -> None:
        try:
            payload = query.payload
            request_bytes = bytes(payload.to_bytes()) if payload is not None else b""
        except Exception:  # noqa: BLE001
            request_bytes = b""
        op = RpcOp(
            query=query,
            procedure=procedure,
            reply_key=reply_key,
            request_bytes=request_bytes,
        )
        try:
            handler(op)
        except Exception:  # noqa: BLE001
            logger.exception("RPC %s handler failed", procedure)
            _reply_err(query, traceback.format_exc())

    return _callback


def _setup_rpc_queryables(
    session: "zenoh.Session", args: argparse.Namespace
) -> list:
    queryables = []
    for proc in _RPC_HANDLERS:
        key = keelson.construct_rpc_key(
            args.realm, args.entity_id, proc, args.source_id
        )
        q = session.declare_queryable(key, _make_rpc_handler(proc, key), complete=True)
        logger.info("Declared RPC queryable: %s", key)
        queryables.append(q)
    return queryables


# ---------------------------------------------------------------------------
# MCU connection supervisor: connect -> read -> disconnect, with
# auto-reconnect via bounded exponential backoff. Owns the asyncio loop
# on a dedicated thread.
# ---------------------------------------------------------------------------


async def _sleep_or_shutdown(seconds: float, is_shutdown: Callable[[], bool]) -> None:
    """Sleep up to ``seconds``, returning early as soon as shutdown is set."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + seconds
    while not is_shutdown():
        remaining = deadline - loop.time()
        if remaining <= 0:
            return
        await asyncio.sleep(min(remaining, 0.1))


async def _mcu_supervisor(
    transport: TcpTransport,
    backoff: ReconnectBackoff,
    framing: Framing,
    health: HealthState,
    is_shutdown: Callable[[], bool],
) -> None:
    """Connect-read-disconnect loop with auto-reconnect.

    The read step is bounded with ``asyncio.wait_for`` so the loop
    checks ``is_shutdown`` at a few-hundred-ms cadence even when the
    MCU is silent. ``Framing.decode`` mutates the buffer in place.
    State transitions are mirrored into ``health`` so the periodic
    entity_health publisher reflects link state.
    """
    while not is_shutdown():
        try:
            logger.info("Connecting to MCU at %s", transport.endpoint)
            await transport.connect()
        except (ConnectionError, OSError, asyncio.TimeoutError) as exc:
            reason = f"connect to {transport.endpoint} failed: {type(exc).__name__}: {exc}"
            logger.warning("MCU %s", reason)
            health.mark_disconnected(reason)
            await _sleep_or_shutdown(backoff.next_delay(), is_shutdown)
            continue

        backoff.reset()
        logger.info("MCU connected at %s", transport.endpoint)
        health.mark_connected(transport.endpoint)
        buffer = bytearray()

        try:
            while not is_shutdown():
                try:
                    chunk = await asyncio.wait_for(transport.read(4096), timeout=0.5)
                except asyncio.TimeoutError:
                    continue
                buffer.extend(chunk)
                for msg in framing.decode(buffer):
                    # v1: framing is passthrough so ``msg`` is raw bytes
                    # and there's no protocol to interpret it. Logged
                    # at debug so the e2e test can verify reads occur
                    # without spamming production logs.
                    logger.debug(
                        "MCU -> connector: %d bytes (framing stubbed)", len(msg)
                    )
        except (ConnectionError, OSError) as exc:
            reason = f"read from {transport.endpoint} failed: {exc}"
            logger.warning("MCU %s", reason)
            health.mark_disconnected(reason)
        finally:
            await transport.close()
            logger.info("MCU disconnected from %s", transport.endpoint)
            # Cover the clean-shutdown case (no exception) too — the
            # supervisor exiting because ``is_shutdown`` flipped should
            # also surface as a non-connected health state for any
            # consumer still polling.
            if not is_shutdown():
                health.mark_disconnected(f"link to {transport.endpoint} dropped")

        if is_shutdown():
            return
        await _sleep_or_shutdown(backoff.next_delay(), is_shutdown)


async def _health_publisher(
    publisher,
    health: HealthState,
    publish_rate_hz: float,
    is_shutdown: Callable[[], bool],
) -> None:
    """Periodic EntityHealth publisher; sleeps in small slices so
    shutdown is detected promptly."""
    period_s = 1.0 / publish_rate_hz
    while not is_shutdown():
        payload = build_entity_health(health, publish_rate_hz=publish_rate_hz)
        publisher.put(enclose(payload.SerializeToString()))
        await _sleep_or_shutdown(period_s, is_shutdown)


def _run_mcu_thread(
    transport: TcpTransport,
    backoff: ReconnectBackoff,
    framing: Framing,
    health: HealthState,
    health_publisher,
    health_publish_rate_hz: float,
    is_shutdown: Callable[[], bool],
) -> None:
    """Entry point for the MCU thread: own an asyncio loop, run the
    supervisor and the entity_health publisher on it concurrently."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(
            asyncio.gather(
                _mcu_supervisor(transport, backoff, framing, health, is_shutdown),
                _health_publisher(
                    health_publisher, health, health_publish_rate_hz, is_shutdown
                ),
            )
        )
    finally:
        loop.close()


# ---------------------------------------------------------------------------
# CLI + main
# ---------------------------------------------------------------------------


def _parse_endpoint(endpoint: str) -> tuple[str, int]:
    """Parse ``host:port`` into ``(host, int(port))``."""
    if ":" not in endpoint:
        raise argparse.ArgumentTypeError(
            f"--mcu-endpoint must be host:port, got: {endpoint!r}"
        )
    host, _, port = endpoint.rpartition(":")
    if not host or not port:
        raise argparse.ArgumentTypeError(
            f"--mcu-endpoint must be host:port, got: {endpoint!r}"
        )
    try:
        return host, int(port)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"--mcu-endpoint port must be an integer, got: {port!r}"
        ) from exc


def _parse_backoff(spec: str) -> tuple[float, float]:
    """Parse ``min,max`` (seconds) into a (float, float) tuple."""
    parts = spec.split(",")
    if len(parts) != 2:
        raise argparse.ArgumentTypeError(
            f"--mcu-reconnect-backoff-s expected MIN,MAX, got: {spec!r}"
        )
    try:
        lo, hi = float(parts[0]), float(parts[1])
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"--mcu-reconnect-backoff-s values must be numbers, got: {spec!r}"
        ) from exc
    if lo <= 0 or hi < lo:
        raise argparse.ArgumentTypeError(
            f"--mcu-reconnect-backoff-s requires 0 < MIN <= MAX, got: {spec!r}"
        )
    return lo, hi


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="keelson2rorkult",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
        description=(
            "Bidirectional Keelson <-> rorkult-MCU actuation connector "
            "(skeleton; MCU framing deferred)."
        ),
    )
    add_common_arguments(parser)
    parser.add_argument("-r", "--realm", type=str, required=True)
    parser.add_argument("-e", "--entity-id", type=str, required=True)
    parser.add_argument("-s", "--source-id", type=str, required=True)

    parser.add_argument(
        "--mcu-endpoint",
        type=str,
        required=True,
        help="MCU TCP endpoint as host:port (e.g. 192.0.2.50:9000)",
    )
    parser.add_argument(
        "--mcu-connect-timeout-s",
        type=float,
        default=5.0,
        help="Per-attempt connect timeout to the MCU in seconds",
    )
    parser.add_argument(
        "--mcu-reconnect-backoff-s",
        type=str,
        default="1.0,30.0",
        help=(
            "Bounded exponential reconnect backoff as MIN,MAX seconds. "
            "Resets to MIN after every successful connect."
        ),
    )
    parser.add_argument(
        "--health-publish-rate-hz",
        type=float,
        default=1.0,
        help=(
            "Rate at which entity_health is published. Independent of "
            "MCU link state -- the publisher always emits at this rate, "
            "the level field reflects current connectivity."
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    setup_logging(level=args.log_level)

    mcu_host, mcu_port = _parse_endpoint(args.mcu_endpoint)
    backoff_min, backoff_max = _parse_backoff(args.mcu_reconnect_backoff_s)
    if args.health_publish_rate_hz <= 0:
        parser.error("--health-publish-rate-hz must be positive")
    transport = TcpTransport(
        mcu_host, mcu_port, connect_timeout_s=args.mcu_connect_timeout_s
    )
    backoff = ReconnectBackoff(backoff_min, backoff_max)
    framing: Framing = PassthroughFraming()
    health = HealthState()

    conf = create_zenoh_config(
        mode=args.mode, connect=args.connect, listen=args.listen
    )

    logger.info("Opening Zenoh session...")
    with (
        GracefulShutdown() as shutdown,
        zenoh.open(conf) as session,
        declare_liveliness_token(session, args.realm, args.entity_id, args.source_id),
    ):
        logger.info("Declared liveliness token (connector alive)")

        # entity_health publisher uses the connector's plain --source-id;
        # the {source_id}/setpoint and {source_id}/measured sub-namespace
        # is for actuator values once framing lands (see ZENOH_API.md).
        health_key = construct_pubsub_key(
            args.realm, args.entity_id, "entity_health", args.source_id
        )
        health_publisher = session.declare_publisher(health_key)
        logger.info("Publishing entity_health on %s", health_key)

        # Start the MCU thread *before* RPC queryables so an RPC arriving
        # immediately can't observe a missing-supervisor state. (Today's
        # stubbed handlers don't touch the supervisor, but the ordering
        # matters for when they do.)
        mcu_thread = threading.Thread(
            target=_run_mcu_thread,
            args=(
                transport,
                backoff,
                framing,
                health,
                health_publisher,
                args.health_publish_rate_hz,
                shutdown.is_requested,
            ),
            name="rorkult-mcu",
            daemon=True,
        )
        mcu_thread.start()

        rpc_queryables = _setup_rpc_queryables(session, args)

        try:
            while not shutdown.is_requested():
                shutdown.wait(timeout=1.0)
        finally:
            for q in rpc_queryables:
                try:
                    q.undeclare()
                except Exception:  # noqa: BLE001
                    pass
            shutdown.request()
            mcu_thread.join(timeout=2.0)
            if mcu_thread.is_alive():
                logger.warning("MCU thread did not exit within 2s of shutdown")
            try:
                health_publisher.undeclare()
            except Exception:  # noqa: BLE001
                pass

    logger.info("Shutdown complete")
    return 0


if __name__ == "__main__":
    sys.exit(main())
