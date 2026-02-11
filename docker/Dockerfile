# Dockerfile - Keelson all-in-one image
#
# Builds an image containing the SDK and all available connectors.
# This is the primary distribution image for Keelson.
#
# Usage:
#   docker build -f docker/Dockerfile -t keelson .
#
# Published as: ghcr.io/mo-rise/keelson

FROM debian:bookworm-slim

# Install tini for proper signal handling
ADD https://github.com/krallin/tini/releases/download/v0.19.0/tini /tini
RUN chmod +x /tini

# Install system tools
RUN apt-get update && apt-get install -y --no-install-recommends \
    socat \
    jq \
    && rm -rf /var/lib/apt/lists/*

# Install startup script for logging
COPY docker/bash-init.sh /etc/bash-init.sh
RUN chmod +x /etc/bash-init.sh
ENV BASH_ENV=/etc/bash-init.sh

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

ENV UV_CACHE_DIR=/var/cache/uv \
    UV_PYTHON_INSTALL_DIR=/opt/python \
    UV_COMPILE_BYTECODE=1

# Install Python versions
RUN uv python install 3.13

# Copy source
COPY sdks/python /build/sdks/python
COPY connectors/foxglove /build/connectors/foxglove
COPY connectors/klog /build/connectors/klog
COPY connectors/mcap /build/connectors/mcap
COPY connectors/mediamtx /build/connectors/mediamtx
COPY connectors/mockups /build/connectors/mockups
COPY connectors/nmea /build/connectors/nmea
COPY connectors/platform /build/connectors/platform
COPY connectors/ais /build/connectors/ais
COPY connectors/camera /build/connectors/camera

# Build SDK wheel
RUN cd /build/sdks/python && uv build --wheel --out-dir /wheels

# Inject metadata and install scripts
RUN UV_FIND_LINKS=/wheels && \
    for dir in /build/connectors/*/; do \
        name=$(basename "$dir"); \
        for script in "$dir"/bin/*.py; do \
            [ -f "$script" ] || continue; \
            UV_FIND_LINKS=/wheels uv add --script "$script" -r "$dir/requirements.txt" --prerelease allow; \
            sed -i '1s|.*|#!/usr/bin/env -S uv run --script|' "$script"; \
            basename=$(basename "$script" .py); \
            cp "$script" "/usr/local/bin/$basename"; \
            chmod +x "/usr/local/bin/$basename"; \
        done; \
    done

# Install shuffle tool for stream transformation
COPY docker/bin/shuffle /usr/local/bin/shuffle
RUN chmod +x /usr/local/bin/shuffle && \
    uv add --script /usr/local/bin/shuffle parse

# Pre-warm uv environments for all scripts (fail build if any script is broken)
RUN for script in /usr/local/bin/*; do \
        [ -x "$script" ] || continue; \
        head -c2 "$script" | grep -q '#!' || continue; \
        echo "Pre-warming: $script"; \
        UV_FIND_LINKS=/wheels uv run --prerelease allow --script "$script" --help > /dev/null; \
    done && \
    rm -rf /var/cache/uv/wheels-v1 /var/cache/uv/archive-v0 /wheels /build

ENTRYPOINT ["/tini", "-g", "--", "/bin/bash", "-c"]
