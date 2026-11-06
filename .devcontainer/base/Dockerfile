# syntax=docker/dockerfile:1
#
# The keelson devcontainer base image: everything that is the same for every
# workspace and slow to rebuild, baked in once. Published multi-arch (amd64,
# arm64) by .github/workflows/devcontainer.yml as
# ghcr.io/rise-maritime/keelson-devcontainer:<ardupilot tag>; see
# .devcontainer/README.md for building it locally instead.
#
# Build context: .devcontainer/

# protodot publishes a linux binary for x86-64 only. Built from source here so
# the docs' proto diagrams work natively on arm64 (Apple Silicon) as well.
FROM golang:1.23-bookworm AS protodot
ARG PROTODOT_COMMIT=34af90078d6734231f744e1c7d3e5773e0d39805
RUN go install "github.com/seamia/protodot@${PROTODOT_COMMIT}"

# Major 3 is the maintained image line (major 2 stopped at 2.1.2 and ships Python 3.14.0).
# Bookworm, not trixie: the docker-in-docker feature's install.sh refuses trixie with its default
# moby=true ("The 'moby' option is not supported"), so a trixie base fails every rebuild.
FROM mcr.microsoft.com/devcontainers/python:3-3.14-bookworm

# Yarn rotated its Debian apt signing key, which breaks `apt-get update` and any
# feature install that runs it (e.g. docker-in-docker). We use npm, not the yarn
# apt repo, so drop the repo definition before features are layered on top.
RUN rm -f /etc/apt/sources.list.d/yarn.list

# graphviz renders protodot's output into the docs' SVGs.
RUN apt-get update \
    && apt-get install -y --no-install-recommends graphviz \
    && rm -rf /var/lib/apt/lists/*

COPY --from=protodot /go/bin/protodot /usr/local/bin/protodot

# uv on the system PATH, for every user.
RUN curl -LsSf https://astral.sh/uv/install.sh \
    | env UV_INSTALL_DIR=/usr/local/bin INSTALLER_NO_MODIFY_PATH=1 sh

# ArduPilot SITL, built by the same script a developer would run by hand, so
# the two cannot drift. It builds in its own Python 3.13 venv (see the script).
# UV_PYTHON_INSTALL_DIR is set for this step only: the 3.13 interpreter must
# live outside root's home to be usable by the vscode user, and a global ENV
# would also send the workspace's own `uv sync` into a root-owned directory.
ARG ARDUPILOT_BRANCH=Rover-4.5
ENV ARDUPILOT_DIR=/opt/ardupilot \
    ARDUPILOT_VENV=/opt/ardupilot-venv
COPY install-ardupilot-sitl.sh /tmp/install-ardupilot-sitl.sh
RUN UV_PYTHON_INSTALL_DIR=/opt/uv-python \
    ARDUPILOT_BRANCH="${ARDUPILOT_BRANCH}" \
    ARDUPILOT_PRUNE=1 \
    bash /tmp/install-ardupilot-sitl.sh \
    && rm /tmp/install-ardupilot-sitl.sh \
    && rm -rf /var/lib/apt/lists/*

LABEL org.opencontainers.image.source="https://github.com/RISE-Maritime/keelson" \
      org.opencontainers.image.description="keelson devcontainer base: Python, uv, protodot, graphviz and ArduPilot SITL (ardurover, sim_vehicle.py)"
