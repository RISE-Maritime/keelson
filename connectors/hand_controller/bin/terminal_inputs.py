import argparse


def terminal_inputs():
    """Parse the terminal inputs and return the arguments"""

    parser = argparse.ArgumentParser(
        prog="keelson_connector_hc",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
        description="Read joystick/gamepad controller and publish to Keelson/Zenoh",
    )

    parser.add_argument(
        "-l",
        "--log-level",
        type=int,
        default=20,
        help="Log level 10=DEBUG, 20=INFO, 30=WARN, 40=ERROR, 50=CRITICAL 0=NOTSET",
    )

    parser.add_argument(
        "--mode",
        "-m",
        dest="mode",
        choices=["peer", "client"],
        type=str,
        help="The zenoh session mode.",
    )

    parser.add_argument(
        "--connect",
        action="append",
        type=str,
        help="Endpoints to connect to, in case multicast is not working. ex. tcp/localhost:7447",
    )

    parser.add_argument(
        "-r",
        "--realm",
        default="rise",
        type=str,
        help="Unique id for a domain/realm to connect ex. rise",
    )

    parser.add_argument(
        "-e",
        "--entity-id",
        default="rov",
        type=str,
        help="Entity being a unique id representing an entity within the realm",
    )

    parser.add_argument(
        "--device",
        "-d",
        type=str,
        default="/dev/input/js0",
        help="Joystick device path (default: /dev/input/js0)",
    )

    parser.add_argument(
        "--relay",
        type=str,
        default=None,
        help="TCP relay address (host:port) for cross-platform mode. "
        "Reads joystick events from a TCP relay instead of a device file. "
        "Example: --relay host.docker.internal:9090",
    )

    parser.add_argument(
        "-c",
        "--controller",
        type=str,
        default="ssrov",
        help="Controller profile name (resolves to profiles/<name>.yaml).",
    )

    parser.add_argument(
        "--controller-config",
        type=str,
        default=None,
        help="Path to a custom controller-profile YAML file. Overrides --controller.",
    )

    parser.add_argument(
        "--relay-max-retries",
        type=int,
        default=0,
        help="Max relay connection attempts before exit (0 = unlimited).",
    )

    parser.add_argument(
        "--axis-min-interval-ms",
        type=int,
        default=30,
        help="Per-axis rate limit: minimum ms between publishes when value barely changed.",
    )

    parser.add_argument(
        "--axis-min-change",
        type=float,
        default=1.0,
        help="Per-axis rate limit: percentage-point change that forces immediate publish.",
    )

    parser.add_argument(
        "--axis-center-snap-pct",
        type=float,
        default=0.0,
        help=(
            "Snap |value| < this to 0.0 before the rate-limit check. "
            "Cleans up joystick ADC rest offset so a released stick publishes exactly 0.0. "
            "0 disables; recommended 2.0. Loses sub-snap precision near rest."
        ),
    )

    parser.add_argument(
        "--log-json",
        action="store_true",
        help="Emit logs as one JSON object per line (for container log pipelines).",
    )

    parser.add_argument(
        "--source-id",
        type=str,
        default=None,
        help="Override the source-id base. Defaults to the --controller value. "
        "Use this to run two of the same controller side-by-side with distinct "
        "source-id prefixes (e.g. --controller ssrov --source-id ssrov-port).",
    )

    parser.add_argument(
        "--health-interval-s",
        type=float,
        default=1.0,
        help="Period (seconds) between controller_health publishes.",
    )

    parser.add_argument(
        "--health-stale-s",
        type=float,
        default=2.0,
        help="If no hardware event has arrived within this many seconds, "
        "controller_health is published as 0 (stale); otherwise 1 (alive).",
    )

    # Parse arguments and start doing our thing
    args = parser.parse_args()

    return args
