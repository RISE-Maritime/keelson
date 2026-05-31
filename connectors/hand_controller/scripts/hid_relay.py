#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["pygame-ce>=2.5.0"]
# ///

"""
Cross-platform joystick-to-TCP relay for keelson-connector-hand-controller.

Reads joystick/gamepad events using pygame (works on macOS, Windows, Linux)
and forwards them as 8-byte binary events over TCP to the containerized connector.

Wire format (same as Linux joystick API):
    - timestamp (4 bytes, uint32, milliseconds)
    - value (2 bytes, int16)
    - type (1 byte, uint8: 0x01=button, 0x02=axis)
    - number (1 byte, uint8: button/axis index)

Usage:
    python hid_relay.py                    # Start relay on port 9090
    python hid_relay.py --port 5000        # Custom port
    python hid_relay.py --list             # List available joysticks
    python hid_relay.py --joystick-index 1 # Use second joystick
"""

import argparse
import json
import logging
import signal
import socket
import struct
import sys
import time

try:
    import pygame
except ImportError:
    print("ERROR: pygame is required for the relay. Install with:")
    print("  pip install pygame")
    sys.exit(1)


# Joystick event types (matching Linux joystick API)
JS_EVENT_BUTTON = 0x01
JS_EVENT_AXIS = 0x02

logger = logging.getLogger("hid_relay")
shutdown_requested = False


def signal_handler(signum, frame):
    global shutdown_requested
    logger.info("Shutdown signal received")
    shutdown_requested = True


def list_joysticks():
    """List all detected joysticks and their capabilities."""
    pygame.joystick.init()
    count = pygame.joystick.get_count()
    if count == 0:
        print("No joysticks detected.")
        print("Make sure your controller is connected via USB.")
        return

    print(f"Found {count} joystick(s):\n")
    for i in range(count):
        js = pygame.joystick.Joystick(i)
        js.init()
        print(f"  [{i}] {js.get_name()}")
        print(f"      Axes: {js.get_numaxes()}")
        print(f"      Buttons: {js.get_numbuttons()}")
        print(f"      Hats: {js.get_numhats()}")
        print()

    pygame.joystick.quit()


def pack_event(event_type, number, value):
    """Pack a joystick event into the 8-byte wire format."""
    timestamp_ms = pygame.time.get_ticks() & 0xFFFFFFFF
    return struct.pack("IhBB", timestamp_ms, value, event_type, number)


def run_relay(
    port, joystick_index, axis_map, button_map, no_mfi=False, dpad_axis_base=2
):
    """Run the TCP relay server."""
    # Full init needed on macOS for axis events (Cocoa event loop)
    import os

    os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
    if no_mfi:
        # Disable Apple GCController/MFI backend. On macOS 13+, GCController
        # exclusively claims known gamepads (e.g. Logitech F310), hiding them
        # from SDL's joystick API. Disabling MFI forces SDL to use IOKit instead.
        os.environ["SDL_JOYSTICK_MFI"] = "0"
    pygame.init()

    count = pygame.joystick.get_count()
    if count == 0:
        logger.error("No joysticks detected. Connect a controller and try again.")
        sys.exit(1)

    if joystick_index >= count:
        logger.error(
            f"Joystick index {joystick_index} out of range (found {count} joystick(s))"
        )
        sys.exit(1)

    js = pygame.joystick.Joystick(joystick_index)
    js.init()
    logger.info(f"Using joystick [{joystick_index}]: {js.get_name()}")
    logger.info(f"  Axes: {js.get_numaxes()}, Buttons: {js.get_numbuttons()}")

    # Setup TCP server
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.settimeout(1.0)
    server.bind(("0.0.0.0", port))
    server.listen(1)
    logger.info(f"Relay listening on 0.0.0.0:{port}")
    logger.info("Waiting for connector to connect...")

    client = None

    try:
        while not shutdown_requested:
            # Accept new client if none connected
            if client is None:
                try:
                    client, addr = server.accept()
                    client.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                    logger.info(f"Client connected from {addr}")
                except socket.timeout:
                    # Pump events even without a client to keep pygame responsive
                    pygame.event.pump()
                    continue

            # Process pygame events
            events_to_send = []
            for event in pygame.event.get():
                if event.type == pygame.JOYAXISMOTION:
                    # Skip unmapped axes when remapping is active
                    if axis_map and event.axis not in axis_map:
                        continue
                    axis_num = axis_map.get(event.axis, event.axis)
                    # Scale float (-1.0..1.0) to int16 (-32768..32767)
                    value = max(-32768, min(32767, int(event.value * 32767)))
                    logger.debug(
                        f"Axis {event.axis}->{axis_num}: {event.value:.4f} -> {value}"
                    )
                    events_to_send.append(pack_event(JS_EVENT_AXIS, axis_num, value))

                elif event.type == pygame.JOYBUTTONDOWN:
                    btn_num = button_map.get(event.button, event.button)
                    logger.debug(f"Button {event.button}->{btn_num} pressed")
                    events_to_send.append(pack_event(JS_EVENT_BUTTON, btn_num, 1))

                elif event.type == pygame.JOYBUTTONUP:
                    btn_num = button_map.get(event.button, event.button)
                    logger.debug(f"Button {event.button}->{btn_num} released")
                    events_to_send.append(pack_event(JS_EVENT_BUTTON, btn_num, 0))

                elif event.type == pygame.JOYDEVICEREMOVED:
                    logger.error(
                        f"Joystick removed (instance_id={event.instance_id}); "
                        "exiting non-zero so a supervisor can restart the relay."
                    )
                    sys.exit(2)

                elif event.type == pygame.JOYHATMOTION:
                    # Convert hat (x, y) to two axis events
                    # dpad_axis_base = dpad_x, dpad_axis_base+1 = dpad_y
                    hx, hy = event.value
                    # Scale -1/0/1 to int16 range
                    dpad_x = max(-32768, min(32767, hx * 32767))
                    dpad_y = max(-32768, min(32767, hy * 32767))
                    logger.debug(f"Hat ({hx},{hy}) -> dpad_x={dpad_x}, dpad_y={dpad_y}")
                    events_to_send.append(
                        pack_event(JS_EVENT_AXIS, dpad_axis_base, dpad_x)
                    )
                    events_to_send.append(
                        pack_event(JS_EVENT_AXIS, dpad_axis_base + 1, dpad_y)
                    )

            # Send buffered events
            if events_to_send and client is not None:
                try:
                    client.sendall(b"".join(events_to_send))
                except (BrokenPipeError, ConnectionResetError, OSError):
                    logger.warning("Client disconnected")
                    try:
                        client.close()
                    except Exception:
                        pass
                    client = None
                    logger.info("Waiting for connector to reconnect...")

            # Small sleep to avoid busy-waiting (~200Hz poll rate)
            # Gamepads update at 60-125Hz, so 5ms is sufficient
            time.sleep(0.005)

    finally:
        if client:
            try:
                client.close()
            except Exception:
                pass
        server.close()
        pygame.quit()
        logger.info("Relay shut down")


def parse_index_map(map_str):
    """Parse a JSON index mapping string like '{"0":1, "2":3}' into {int: int}."""
    if not map_str:
        return {}
    raw = json.loads(map_str)
    return {int(k): int(v) for k, v in raw.items()}


def main():
    parser = argparse.ArgumentParser(
        description="Cross-platform joystick-to-TCP relay for keelson-connector-hand-controller",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "-l",
        "--list",
        action="store_true",
        help="List available joysticks and exit",
    )
    parser.add_argument(
        "-p",
        "--port",
        type=int,
        default=9090,
        help="TCP port to listen on",
    )
    parser.add_argument(
        "-j",
        "--joystick-index",
        type=int,
        default=0,
        help="Index of the joystick to use (see --list)",
    )
    parser.add_argument(
        "--axis-map",
        type=str,
        default=None,
        help="JSON mapping of pygame axis index to wire axis index, e.g. '{\"3\":5}'",
    )
    parser.add_argument(
        "--button-map",
        type=str,
        default=None,
        help="JSON mapping of pygame button index to wire button index, e.g. '{\"0\":2}'",
    )
    parser.add_argument(
        "--no-mfi",
        action="store_true",
        help="Disable Apple GCController/MFI backend (macOS). Required for controllers "
        "like Logitech F310 that are exclusively claimed by GCController.",
    )
    parser.add_argument(
        "--log-level",
        type=int,
        default=20,
        help="Log level 10=DEBUG, 20=INFO, 30=WARN, 40=ERROR",
    )

    args = parser.parse_args()

    logging.basicConfig(
        format="%(asctime)s %(levelname)s %(name)s %(message)s", level=args.log_level
    )

    if args.no_mfi:
        import os

        os.environ["SDL_JOYSTICK_MFI"] = "0"

    if args.list:
        list_joysticks()
        return

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    axis_map = parse_index_map(args.axis_map)
    button_map = parse_index_map(args.button_map)

    dpad_axis_base = 2  # default: hat events sent as wire axes 2,3
    if args.no_mfi and not args.axis_map:
        # macOS IOKit reports axes in same order as Linux (0,1=left, 2,3=right).
        # No axis remap needed. Hat (dpad) maps to wire axes 4,5.
        dpad_axis_base = 4
        logger.info("Auto-applying --no-mfi settings: dpad_axis_base=4, no axis remap")

    if axis_map:
        logger.info(f"Axis remapping: {axis_map}")
    if button_map:
        logger.info(f"Button remapping: {button_map}")

    run_relay(
        args.port,
        args.joystick_index,
        axis_map,
        button_map,
        args.no_mfi,
        dpad_axis_base,
    )


if __name__ == "__main__":
    main()
