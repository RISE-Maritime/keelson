#!/bin/bash
# Install ArduPilot SITL (Rover, with boat frame support) for use by the
# keelson-connector-mavlink tests and ad-hoc development.
#
# Idempotent: re-running skips the build if ardurover is already present.
# Override location/branch via env vars:
#   ARDUPILOT_DIR     (default: $HOME/ardupilot)
#   ARDUPILOT_BRANCH  (default: Rover-stable)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

ARDUPILOT_DIR="${ARDUPILOT_DIR:-$HOME/ardupilot}"
ARDUPILOT_BRANCH="${ARDUPILOT_BRANCH:-Rover-4.5}"
ARDUROVER_BIN="$ARDUPILOT_DIR/build/sitl/bin/ardurover"

if [ -x "$ARDUROVER_BIN" ] && [ -L /usr/local/bin/ardurover ]; then
    echo "ArduRover SITL already built at $ARDUROVER_BIN — skipping."
    exit 0
fi

# ArduPilot's waf is invoked via `#!/usr/bin/env python3`, so it builds with
# whichever `python3` is first on PATH. During the devcontainer postCreate run
# no venv is activated, so that is the *system* interpreter — which does not
# have ArduPilot's Python build prerequisites (empy, etc.). Activate the
# uv-managed workspace venv up front so that `python3`, and therefore waf,
# resolves to the same interpreter `uv pip install` installs those deps into.
if [ -z "${VIRTUAL_ENV:-}" ] && [ -f "$REPO_ROOT/.venv/bin/activate" ]; then
    echo "==> Activating workspace venv at $REPO_ROOT/.venv ..."
    # shellcheck disable=SC1091
    source "$REPO_ROOT/.venv/bin/activate"
fi

echo "==> Installing apt prerequisites for ArduPilot SITL build..."
sudo apt-get update
sudo apt-get install -y \
    build-essential ccache g++ gawk git make pkg-config

# Install the build prerequisites into the interpreter waf will use (the venv
# activated above). uv-managed venvs ship without a `pip` module, so a literal
# `python3 -m pip install` fails inside one — prefer `uv pip install`, which
# targets the active venv, and fall back to `python3 -m pip` only when uv is
# unavailable (and thus no venv was activated either).
echo "==> Installing Python build prerequisites for $(command -v python3)..."
PY_BUILD_DEPS=(setuptools 'empy==3.3.4' pexpect future lxml dronecan intelhex)
if command -v uv >/dev/null 2>&1; then
    uv pip install --upgrade "${PY_BUILD_DEPS[@]}"
else
    python3 -m pip install --upgrade "${PY_BUILD_DEPS[@]}"
fi

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
sudo ln -sf "$ARDUPILOT_DIR/Tools/autotest/sim_vehicle.py" /usr/local/bin/sim_vehicle.py

echo "==> ArduPilot SITL install complete. Verify with:"
echo "    ardurover --help"
echo "    sim_vehicle.py -v Rover -f boat --no-mavproxy -L SE"
