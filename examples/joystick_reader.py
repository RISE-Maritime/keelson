#!/usr/bin/env python3
"""
Read Seascape ROV Hand Controller via Linux Joystick Interface (/dev/input/js0).

This is the RECOMMENDED method for reading the controller - much faster and more
reliable than the serial interface. This is how QGroundControl reads the controller.

The controller exposes both a serial interface (/dev/ttyACM1) and a joystick
interface (/dev/input/js0). The joystick interface provides real-time access
to all axes and buttons.

Requirements:
    - No external libraries needed! Uses Python's built-in struct module
    - Linux kernel joystick driver

Usage:
    # Basic usage
    python3 joystick_reader.py

    # Specify custom device
    python3 joystick_reader.py --device /dev/input/js1

    # List available joystick devices
    python3 joystick_reader.py --list
"""

import argparse
import sys
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "bin"))

from joystick_proto import (  # noqa: E402  (sys.path shim must come first)
    JS_EVENT_AXIS,
    JS_EVENT_BUTTON,
    JS_EVENT_INIT,
    normalize_axis,
    read_event,
)

# Default device
DEFAULT_DEVICE = "/dev/input/js0"


def list_joystick_devices():
    """List all available joystick devices."""
    input_dir = Path("/dev/input")
    
    if not input_dir.exists():
        print("ERROR: /dev/input directory not found!")
        return
    
    joysticks = sorted(input_dir.glob("js*"))
    
    if not joysticks:
        print("No joystick devices found!")
        print("\nMake sure:")
        print("  1. The controller is connected via USB")
        print("  2. The kernel joystick driver is loaded")
        return
    
    print("\nAvailable joystick devices:")
    print("=" * 60)
    for js in joysticks:
        print(f"  {js}")
        
        # Try to read device name
        try:
            name_file = Path(f"/sys/class/input/{js.name}/device/name")
            if name_file.exists():
                name = name_file.read_text().strip()
                print(f"    Name: {name}")
        except:
            pass
        
        print()


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Read Seascape ROV Hand Controller via Joystick Interface",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )
    
    parser.add_argument(
        "--device",
        "-d",
        default=DEFAULT_DEVICE,
        help=f"Joystick device path (default: {DEFAULT_DEVICE})",
    )
    
    parser.add_argument(
        "--list",
        "-l",
        action="store_true",
        help="List available joystick devices and exit",
    )
    
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Show all events including INIT flags",
    )
    
    args = parser.parse_args()
    
    # List devices and exit
    if args.list:
        list_joystick_devices()
        return
    
    # Check if device exists
    device_path = Path(args.device)
    if not device_path.exists():
        print(f"ERROR: Device {args.device} not found!")
        print("\nAvailable devices:")
        list_joystick_devices()
        sys.exit(1)
    
    print("=" * 60)
    print("Seascape ROV Hand Controller - Joystick Reader")
    print("=" * 60)
    print(f"Device: {args.device}")
    print()
    
    try:
        # Try to read device name
        name_file = Path(f"/sys/class/input/{device_path.name}/device/name")
        if name_file.exists():
            name = name_file.read_text().strip()
            print(f"Controller: {name}")
    except:
        pass
    
    print("=" * 60)
    print("\nPress Ctrl+C to stop\n")
    
    try:
        # Open joystick device in binary mode, unbuffered
        js_device = open(args.device, 'rb', buffering=0)
        
        print(f"Joystick device opened successfully!")
        print("\nWaiting for events...")
        print("- Move joysticks to see axis events")
        print("- Press buttons to see button events")
        print()
        
        event_count = 0
        button_states = {}  # Track button states
        axis_values = {}    # Track axis values
        
        # Read events loop
        while True:
            event = read_event(js_device)
            
            if event:
                timestamp, value, event_type, number = event
                event_count += 1
                
                # Remove INIT flag for display
                is_init = (event_type & JS_EVENT_INIT) != 0
                event_type = event_type & ~JS_EVENT_INIT
                
                # Skip INIT events unless debug mode
                if is_init and not args.debug:
                    continue
                
                timestamp_str = datetime.now().strftime("%H:%M:%S.%f")[:-3]
                
                if event_type == JS_EVENT_BUTTON:
                    button_states[number] = value
                    state = "PRESSED" if value == 1 else "RELEASED"
                    init_flag = " [INIT]" if is_init else ""
                    print(f"[{timestamp_str}] BUTTON {number:2d} {state:8s} {init_flag}")
                    
                elif event_type == JS_EVENT_AXIS:
                    axis_values[number] = value
                    init_flag = " [INIT]" if is_init else ""
                    
                    # Show percentage for better readability
                    percentage = normalize_axis(value)
                    bar_length = 40
                    bar_fill = int((value + 32768) / 65535 * bar_length)
                    bar = "=" * bar_fill + " " * (bar_length - bar_fill)
                    
                    print(f"[{timestamp_str}] AXIS {number:2d} {value:6d} [{bar}] {percentage:6.1f}%{init_flag}")
                
                else:
                    print(f"[{timestamp_str}] UNKNOWN EVENT: type={event_type}, number={number}, value={value}")
            
    except IOError as e:
        print(f"\nDevice error: {e}")
        print("\nTroubleshooting:")
        print("  1. Check if the device exists")
        print("  2. Verify permissions: ls -l /dev/input/js*")
        print("  3. Grant read access: sudo chmod a+r /dev/input/js*")
        print("  4. Add user to input group: sudo usermod -a -G input $USER")
        sys.exit(1)
        
    except KeyboardInterrupt:
        print("\n\n" + "=" * 60)
        print(f"Stopped by user - processed {event_count} events")
        print("=" * 60)
        
        if button_states:
            print("\nFinal button states:")
            for btn in sorted(button_states.keys()):
                state = "PRESSED" if button_states[btn] else "released"
                print(f"  Button {btn:2d}: {state}")
        
        if axis_values:
            print("\nFinal axis values:")
            for axis in sorted(axis_values.keys()):
                value = axis_values[axis]
                print(f"  Axis {axis:2d}: {value:6d} ({normalize_axis(value):6.1f}%)")
        
    except Exception as e:
        print(f"\nError: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
        
    finally:
        if 'js_device' in locals():
            js_device.close()
            print("\nJoystick device closed")


if __name__ == "__main__":
    main()
