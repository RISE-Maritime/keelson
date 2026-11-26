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

# ArduPilot builds and runs in its OWN venv, on Python 3.13, not the workspace venv.
# waf is invoked via `#!/usr/bin/env python3`, so it builds with whichever `python3` is first on
# PATH. The workspace venv follows .python-version (3.14 since #357), and Rover-4.5's build does
# not survive 3.14: dronecangen uses multiprocessing, 3.14 made forkserver the default start
# method on Linux, and its workers die in the handshake ("ConnectionResetError ... dronecangen
# returned 1 error code"). Pinning the build to its own interpreter keeps ArduPilot's toolchain
# independent of whatever the workspace moves to next.
ARDUPILOT_PYTHON="${ARDUPILOT_PYTHON:-3.13}"
ARDUPILOT_VENV="${ARDUPILOT_VENV:-$HOME/.ardupilot-venv}"
if [ ! -x "$ARDUPILOT_VENV/bin/python3" ]; then
    echo "==> Creating ArduPilot build venv ($ARDUPILOT_PYTHON) at $ARDUPILOT_VENV ..."
    uv venv --python "$ARDUPILOT_PYTHON" "$ARDUPILOT_VENV"
fi
# A workspace venv activated by the caller would otherwise shadow this one on PATH.
if [ -n "${VIRTUAL_ENV:-}" ] && [ "$VIRTUAL_ENV" != "$ARDUPILOT_VENV" ]; then
    deactivate 2>/dev/null || PATH="${PATH//$VIRTUAL_ENV\/bin:/}"
fi
# shellcheck disable=SC1091
source "$ARDUPILOT_VENV/bin/activate"

echo "==> Installing apt prerequisites for ArduPilot SITL build..."
sudo apt-get update
sudo apt-get install -y \
    build-essential ccache g++ gawk git make pkg-config

# Install the build prerequisites into the build venv (uv-managed venvs ship without pip).
echo "==> Installing Python build prerequisites for $(command -v python3)..."
PY_BUILD_DEPS=(setuptools 'empy==3.3.4' pexpect future lxml dronecan intelhex pymavlink)
uv pip install --python "$ARDUPILOT_VENV/bin/python3" --upgrade "${PY_BUILD_DEPS[@]}"

# Fail fast with a clear message if waf's interpreter still can't see the deps,
# rather than letting the build error out deep inside code generation.
if ! python3 -c 'import em' >/dev/null 2>&1; then
    echo "ERROR: 'empy' is not importable by $(command -v python3) —" >&2
    echo "       ArduPilot's waf build would fail during code generation." >&2
    exit 1
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
# sim_vehicle.py is `#!/usr/bin/env python3` too, so called from a shell (or a test) with the
# workspace venv active it would run on 3.14 without pexpect & co. A wrapper pins it to the
# build venv instead of a symlink.
sudo tee /usr/local/bin/sim_vehicle.py >/dev/null <<WRAPPER
#!/bin/sh
exec "$ARDUPILOT_VENV/bin/python3" "$ARDUPILOT_DIR/Tools/autotest/sim_vehicle.py" "\$@"
WRAPPER
sudo chmod +x /usr/local/bin/sim_vehicle.py

echo "==> ArduPilot SITL install complete. Verify with:"
echo "    ardurover --help"
echo "    sim_vehicle.py -v Rover -f boat --no-mavproxy -L SE"
