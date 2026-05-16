#!/bin/bash
# Install ArduPilot SITL (Rover, with boat frame support) for use by the
# keelson-connector-mavlink tests and ad-hoc development.
#
# Idempotent: re-running skips the build if ardurover is already present.
# Override location/branch via env vars:
#   ARDUPILOT_DIR     (default: $HOME/ardupilot)
#   ARDUPILOT_BRANCH  (default: Rover-stable)

set -euo pipefail

ARDUPILOT_DIR="${ARDUPILOT_DIR:-$HOME/ardupilot}"
ARDUPILOT_BRANCH="${ARDUPILOT_BRANCH:-Rover-4.5}"
ARDUROVER_BIN="$ARDUPILOT_DIR/build/sitl/bin/ardurover"

if [ -x "$ARDUROVER_BIN" ] && [ -L /usr/local/bin/ardurover ]; then
    echo "ArduRover SITL already built at $ARDUROVER_BIN — skipping."
    exit 0
fi

echo "==> Installing apt prerequisites for ArduPilot SITL build..."
sudo apt-get update
sudo apt-get install -y \
    build-essential ccache g++ gawk git make pkg-config

# ArduPilot's waf is invoked via `#!/usr/bin/env python3`, so it uses whichever
# python3 is first on PATH (in this devcontainer that's the 3.13 from the
# devcontainers/python feature, NOT system /usr/bin/python3.11). Install the
# Python build deps into THAT interpreter so waf's `import em` succeeds.
echo "==> Installing Python build prerequisites for $(command -v python3)..."
python3 -m pip install --upgrade setuptools 'empy==3.3.4' pexpect future lxml dronecan intelhex

if [ ! -d "$ARDUPILOT_DIR/.git" ]; then
    echo "==> Cloning ArduPilot ($ARDUPILOT_BRANCH) into $ARDUPILOT_DIR..."
    git clone --branch "$ARDUPILOT_BRANCH" --recurse-submodules --shallow-submodules --depth 1 \
        https://github.com/ArduPilot/ardupilot.git "$ARDUPILOT_DIR"
else
    echo "==> ArduPilot already cloned at $ARDUPILOT_DIR — using existing checkout."
fi

echo "==> Configuring waf for SITL board..."
cd "$ARDUPILOT_DIR"
./waf configure --board sitl

echo "==> Building ArduRover (slow — typically 10-30 min on first build)..."
./waf rover

echo "==> Symlinking ardurover and sim_vehicle.py into /usr/local/bin/..."
sudo ln -sf "$ARDUROVER_BIN" /usr/local/bin/ardurover
sudo ln -sf "$ARDUPILOT_DIR/Tools/autotest/sim_vehicle.py" /usr/local/bin/sim_vehicle.py

echo "==> ArduPilot SITL install complete. Verify with:"
echo "    ardurover --help"
echo "    sim_vehicle.py -v Rover -f boat --no-mavproxy -L SE"
