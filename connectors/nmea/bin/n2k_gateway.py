#!/usr/bin/env python3

"""Shared CAN-gateway access for the NMEA2000 keelson connectors.

Wraps the asyncio gateway clients from the ``nmea2000`` library so the
synchronous keelson connectors (``n2k2keelson``, ``keelson2n2k``) can open a
CAN gateway directly and exchange :class:`NMEA2000Message` objects -- without
an intermediate JSON-pipe process.

This module is *not* an entry point; it is imported as a sibling by the
connector scripts in this directory. It provides:

- :data:`GATEWAY_PROFILES` -- named gateway profiles bundling wire protocol,
  transport and known on-bus quirks.
- :func:`create_gateway` -- build an ``nmea2000`` client for a profile.
- :class:`GatewayIdentity` -- what the connect-time probe learns about a
  gateway, and how it maps onto a ``source_id``.
- :func:`probe_identity` -- discover the gateway's claimed N2K address.
- :class:`GatewayRunner` -- run a gateway's asyncio loop on a background
  thread and hand decoded messages to a synchronous consumer via a queue.
"""

from __future__ import annotations

import asyncio
import logging
import queue
import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional

from nmea2000.ioclient import (
    AsyncIOClient,
    EByteNmea2000Gateway,
    TextNmea2000Gateway,
    WaveShareNmea2000Gateway,
)
from nmea2000.input_formats import N2KFormat
from nmea2000.message import NMEA2000Field, NMEA2000Message

logger = logging.getLogger("n2k_gateway")

# PGNs used by the connect-time identity probe.
PGN_ISO_REQUEST = 59904
PGN_ISO_ADDRESS_CLAIM = 60928

# Manufacturer codes (ISO Address Claim NAME field).
MANUFACTURER_YACHT_DEVICES = 717
MANUFACTURER_ACTISENSE = 273


# --------------------------------------------------------------------------
# Gateway profiles
# --------------------------------------------------------------------------

# A builder turns (host, port, device, include_pgns, exclude_pgns) into a client.
GatewayBuilder = Callable[
    [Optional[str], Optional[int], Optional[str], list, list], AsyncIOClient
]


@dataclass(frozen=True)
class GatewayProfile:
    """A named CAN-gateway profile.

    Bundles everything the connectors need to open a gateway and reason about
    its on-bus behaviour. The ``name`` doubles as the gateway-type segment of
    the published ``source_id``.
    """

    name: str
    transport: str  # "tcp" or "usb"
    manufacturer_code: Optional[int]
    # A "polite" gateway is itself an N2K node: it rewrites the source byte of
    # injected frames to its own claimed address before transmitting them.
    polite_node: bool
    builder: GatewayBuilder


def _build_text_tcp(fmt: Optional[N2KFormat]) -> GatewayBuilder:
    """Builder for a TCP text/line-based gateway with a fixed encode format."""

    def builder(host, port, device, include_pgns, exclude_pgns):
        return TextNmea2000Gateway(
            host,
            port,
            format=fmt,
            include_pgns=include_pgns,
            exclude_pgns=exclude_pgns,
        )

    return builder


def _build_ebyte(host, port, device, include_pgns, exclude_pgns):
    return EByteNmea2000Gateway(
        host, port, include_pgns=include_pgns, exclude_pgns=exclude_pgns
    )


def _build_waveshare(host, port, device, include_pgns, exclude_pgns):
    return WaveShareNmea2000Gateway(
        device, include_pgns=include_pgns, exclude_pgns=exclude_pgns
    )


GATEWAY_PROFILES: dict[str, GatewayProfile] = {
    # Yacht Devices YDEN-02 in RAW mode -- CAN-frame ASCII over TCP.
    "yden02": GatewayProfile(
        name="yden02",
        transport="tcp",
        manufacturer_code=MANUFACTURER_YACHT_DEVICES,
        polite_node=True,
        builder=_build_text_tcp(N2KFormat.CAN_FRAME_ASCII),
    ),
    # EByte ECAN -- a raw CAN-over-TCP bridge, not an N2K node.
    "ebyte": GatewayProfile(
        name="ebyte",
        transport="tcp",
        manufacturer_code=None,
        polite_node=False,
        builder=_build_ebyte,
    ),
    # Generic Actisense N2K-ASCII gateway over TCP, auto-detect (receive-only).
    # NOTE: NGT-1/NGX-1 USB devices speak a different (BST) protocol and are
    # handled by a dedicated profile added with the gateway-control work.
    "actisense": GatewayProfile(
        name="actisense",
        transport="tcp",
        manufacturer_code=MANUFACTURER_ACTISENSE,
        polite_node=True,
        builder=_build_text_tcp(None),
    ),
    # WaveShare USB-CAN-A serial gateway.
    "waveshare": GatewayProfile(
        name="waveshare",
        transport="usb",
        manufacturer_code=None,
        polite_node=False,
        builder=_build_waveshare,
    ),
}


def get_profile(name: str) -> GatewayProfile:
    """Return the named gateway profile, or raise ``ValueError``."""
    profile = GATEWAY_PROFILES.get(name)
    if profile is None:
        valid = ", ".join(sorted(GATEWAY_PROFILES))
        raise ValueError(f"Unknown gateway profile {name!r}. Valid profiles: {valid}.")
    return profile


def _validate_transport_args(
    profile: GatewayProfile,
    host: Optional[str],
    port: Optional[int],
    device: Optional[str],
) -> None:
    """Raise ``ValueError`` if the transport arguments are incomplete."""
    if profile.transport == "tcp" and (not host or not port):
        raise ValueError(
            f"Gateway profile {profile.name!r} (TCP) requires host and port."
        )
    if profile.transport == "usb" and not device:
        raise ValueError(
            f"Gateway profile {profile.name!r} (USB) requires a device path."
        )


def _endpoint_label(
    profile: GatewayProfile,
    host: Optional[str],
    port: Optional[int],
    device: Optional[str],
) -> str:
    """Human-readable endpoint string for logs and identity records."""
    if profile.transport == "tcp":
        return f"{host}:{port}"
    return device or "?"


def create_gateway(
    profile_name: str,
    *,
    host: Optional[str] = None,
    port: Optional[int] = None,
    device: Optional[str] = None,
    include_pgns: Optional[list[int]] = None,
    exclude_pgns: Optional[list[int]] = None,
) -> AsyncIOClient:
    """Build an ``nmea2000`` gateway client for the named profile.

    Raises:
        ValueError: unknown profile, or missing transport arguments.
    """
    profile = get_profile(profile_name)
    _validate_transport_args(profile, host, port, device)
    logger.info(
        "Creating %s gateway (%s)",
        profile_name,
        _endpoint_label(profile, host, port, device),
    )
    return profile.builder(host, port, device, include_pgns or [], exclude_pgns or [])


# --------------------------------------------------------------------------
# Identity probe
# --------------------------------------------------------------------------


@dataclass
class GatewayIdentity:
    """What the connect-time probe learned about a gateway."""

    gateway_type: str
    host: str
    polite_node: bool
    manufacturer_code: Optional[int] = None
    claimed_address: Optional[int] = None

    def source_id_suffix(self) -> str:
        """Path segment(s) appended to the connector's base ``source_id``.

        ``yden02/180`` once the claimed address is known, ``yden02`` while it
        is not -- so the gateway type is always present in the key.
        """
        if self.claimed_address is not None:
            return f"{self.gateway_type}/{self.claimed_address}"
        return self.gateway_type


def _build_iso_request(requested_pgn: int, source: int = 254) -> NMEA2000Message:
    """Build an ISO Request (PGN 59904) for ``requested_pgn``, broadcast.

    ``source`` 254 is the ISO null address -- appropriate for a node that has
    not claimed an address of its own.
    """
    return NMEA2000Message(
        PGN=PGN_ISO_REQUEST,
        id="isoRequest",
        description="ISO Request",
        source=source,
        destination=255,
        priority=6,
        fields=[NMEA2000Field(id="pgn", value=requested_pgn, raw_value=requested_pgn)],
    )


def _requested_pgn(message: NMEA2000Message) -> Optional[int]:
    """Extract the requested PGN from an ISO Request message, or ``None``."""
    for field in message.fields:
        if field.id == "pgn":
            try:
                return int(field.value)
            except (TypeError, ValueError):
                return None
    return None


async def probe_identity(
    client: AsyncIOClient,
    profile: GatewayProfile,
    host_label: str,
    *,
    timeout: float = 2.0,
    echo_window: float = 0.5,
) -> tuple[GatewayIdentity, list[NMEA2000Message]]:
    """Probe a freshly-connected gateway for its identity.

    Sends one ISO Request for the Address Claim PGN and listens for ``timeout``
    seconds:

    - **Method A** -- a polite gateway echoes the request back with its source
      byte rewritten to its own claimed address. The first such echo seen
      within ``echo_window`` seconds yields the claimed address.
    - **Method B** -- every node on the bus answers with its NAME; the set of
      responders is logged as a bus scan.

    Every message seen during the window is returned alongside the identity so
    the caller can forward data frames that arrived before normal streaming.
    """
    collected: list[tuple[float, NMEA2000Message]] = []

    async def _collect(message: NMEA2000Message) -> None:
        collected.append((time.monotonic(), message))

    client.set_receive_callback(_collect)

    sent_at = time.monotonic()
    # A read-only gateway (e.g. auto-detect format) cannot encode; AsyncIOClient
    # logs a warning and drops the frame rather than raising, so Method A simply
    # finds no echo and the probe degrades to a type-only identity.
    await client.send(_build_iso_request(PGN_ISO_ADDRESS_CLAIM))
    await asyncio.sleep(timeout)

    messages = [message for _, message in collected]

    # Method A: the gateway's own echo, recognised as an ISO Request for the
    # Address Claim PGN arriving within the echo window. Only meaningful for a
    # polite gateway -- a raw bridge does not rewrite the source.
    claimed_address: Optional[int] = None
    if profile.polite_node:
        for seen_at, message in collected:
            if (
                message.PGN == PGN_ISO_REQUEST
                and seen_at - sent_at <= echo_window
                and _requested_pgn(message) == PGN_ISO_ADDRESS_CLAIM
            ):
                claimed_address = message.source
                break

    # Method B: nodes answering with an Address Claim -- logged for diagnostics.
    discovered = sorted(
        {message.source for message in messages if message.PGN == PGN_ISO_ADDRESS_CLAIM}
    )
    if discovered:
        logger.info(
            "Bus scan: %d node(s) claimed an address: %s", len(discovered), discovered
        )

    identity = GatewayIdentity(
        gateway_type=profile.name,
        host=host_label,
        polite_node=profile.polite_node,
        manufacturer_code=profile.manufacturer_code,
        claimed_address=claimed_address,
    )
    return identity, messages


# --------------------------------------------------------------------------
# GatewayRunner -- async gateway loop on a background thread
# --------------------------------------------------------------------------


class GatewayRunner:
    """Run a CAN gateway's asyncio event loop on a background thread.

    Connects the gateway, probes its identity once, then (for a read consumer)
    streams decoded :class:`NMEA2000Message` objects into a thread-safe queue.
    Designed to serve both connector directions: :meth:`send` schedules an
    outbound message onto the gateway loop.

    A write-only consumer should pass ``stream_received=False`` so received
    frames are dropped rather than accumulating in an undrained queue.
    """

    def __init__(
        self,
        profile_name: str,
        *,
        host: Optional[str] = None,
        port: Optional[int] = None,
        device: Optional[str] = None,
        include_pgns: Optional[list[int]] = None,
        exclude_pgns: Optional[list[int]] = None,
        probe_timeout: float = 2.0,
        stream_received: bool = True,
    ):
        self._profile_name = profile_name
        self._profile = get_profile(profile_name)
        _validate_transport_args(self._profile, host, port, device)

        self._host = host
        self._port = port
        self._device = device
        self._include_pgns = include_pgns or []
        self._exclude_pgns = exclude_pgns or []
        self._probe_timeout = probe_timeout
        self._stream_received = stream_received
        self._host_label = _endpoint_label(self._profile, host, port, device)

        self.messages: "queue.Queue[NMEA2000Message]" = queue.Queue()
        self.identity: Optional[GatewayIdentity] = None

        self._identity_ready = threading.Event()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._client: Optional[AsyncIOClient] = None

    def start(self) -> None:
        """Start the background gateway thread."""
        self._thread = threading.Thread(
            target=self._run,
            name=f"n2k-gateway-{self._profile_name}",
            daemon=True,
        )
        self._thread.start()

    def is_running(self) -> bool:
        """Return ``True`` while the background gateway thread is alive."""
        return self._thread is not None and self._thread.is_alive()

    def wait_identity(
        self, timeout: Optional[float] = None
    ) -> Optional[GatewayIdentity]:
        """Block until the identity probe completes, or until ``timeout``.

        Returns the identity, or ``None`` if the timeout elapsed first. Note
        the identity itself is ``None`` only if the gateway thread failed
        before probing.
        """
        if self._identity_ready.wait(timeout):
            return self.identity
        return None

    def send(self, message: NMEA2000Message) -> None:
        """Thread-safe: schedule an outbound message onto the gateway loop."""
        if self._loop is None or self._client is None:
            raise RuntimeError("Gateway is not running")
        asyncio.run_coroutine_threadsafe(self._client.send(message), self._loop)

    def stop(self) -> None:
        """Signal the gateway thread to shut down and wait for it to finish."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=10.0)

    def _run(self) -> None:
        try:
            asyncio.run(self._async_main())
        except Exception:
            logger.exception("Gateway thread crashed")
        finally:
            # Never leave a consumer blocked on the identity probe.
            self._identity_ready.set()

    async def _async_main(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._client = create_gateway(
            self._profile_name,
            host=self._host,
            port=self._port,
            device=self._device,
            include_pgns=self._include_pgns,
            exclude_pgns=self._exclude_pgns,
        )

        logger.info("Connecting to %s gateway...", self._profile_name)
        await self._client.connect()
        logger.info(
            "Connected; probing gateway identity (~%.0fs)...", self._probe_timeout
        )

        identity, collected = await probe_identity(
            self._client,
            self._profile,
            self._host_label,
            timeout=self._probe_timeout,
        )
        self.identity = identity
        self._log_identity(identity)
        self._identity_ready.set()

        if self._stream_received:
            # Forward data frames seen during the probe window, then stream live.
            for message in collected:
                self.messages.put(message)
            self._client.set_receive_callback(self._on_message)
        else:
            # Write-only consumer: drop received frames instead of queueing them.
            self._client.set_receive_callback(None)

        while not self._stop.is_set():
            await asyncio.sleep(0.2)

        logger.info("Closing %s gateway connection", self._profile_name)
        await self._client.close()

    async def _on_message(self, message: NMEA2000Message) -> None:
        self.messages.put(message)

    def _log_identity(self, identity: GatewayIdentity) -> None:
        address = (
            identity.claimed_address
            if identity.claimed_address is not None
            else "unknown"
        )
        logger.info(
            "Gateway identity: type=%s claimed_address=%s manufacturer_code=%s "
            "host=%s polite_node=%s",
            identity.gateway_type,
            address,
            identity.manufacturer_code,
            identity.host,
            identity.polite_node,
        )
        if identity.polite_node and identity.claimed_address is not None:
            logger.info(
                "Polite gateway: injected frames will appear on the bus with "
                "src=%s (%s claim) -- verify injection on payload-internal "
                "markers, not on the source address.",
                identity.claimed_address,
                identity.gateway_type,
            )
