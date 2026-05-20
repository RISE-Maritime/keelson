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
# python3 is first on PATH. In this repo's devcontainer that resolves to the
# uv-managed .venv interpreter, which has no `pip` module — so a literal
# `python3 -m pip install` fails. Prefer `uv pip install` (which installs into
# the active venv) and fall back to `python3 -m pip` for environments where
# uv isn't available.
echo "==> Installing Python build prerequisites for $(command -v python3)..."
PY_BUILD_DEPS=(setuptools 'empy==3.3.4' pexpect future lxml dronecan intelhex)
if command -v uv >/dev/null 2>&1; then
    uv pip install --upgrade "${PY_BUILD_DEPS[@]}"
else
    python3 -m pip install --upgrade "${PY_BUILD_DEPS[@]}"
fi

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
