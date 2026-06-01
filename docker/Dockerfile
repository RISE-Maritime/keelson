# Dockerfile - Keelson all-in-one image
#
# Based on porla-maritime which provides Python 3.13, canboat tools,
# and common utilities (shuffle, zenoh, mqtt, etc.)
#
# Published as: ghcr.io/rise-maritime/keelson

FROM ghcr.io/rise-maritime/porla-maritime:v0.2.0

# Install all third-party Python dependencies from the frozen lockfile
# export. requirements-prod.txt is generated from uv.lock by
#   uv export --frozen --format requirements-txt --no-emit-workspace \
#             --no-hashes --no-dev -o requirements-prod.txt
# and is verified in CI (`lint` job) to be in sync with uv.lock so docker
# builds are reproducible — no live PyPI resolution, no surprise upstream
# regressions. --no-deps below: every transitive is already pinned in the
# file, pip must not re-resolve.
COPY requirements-prod.txt /build/requirements-prod.txt
RUN pip install --no-cache-dir --no-deps -r /build/requirements-prod.txt

# Install the keelson SDK and connectors from source (workspace members
# are intentionally excluded from requirements-prod.txt by
# --no-emit-workspace; they're installed here instead).
COPY sdks/python /build/sdks/python
RUN pip install --no-cache-dir --no-deps /build/sdks/python

# entity_health ships an importable Python sub-package alongside its bin
# script, so install the connector itself (not just its requirements) so
# `import entity_health.evaluator` works from /usr/local/bin/.
COPY connectors/entity_health /build/entity_health
RUN pip install --no-cache-dir --no-deps /build/entity_health \
    && rm -rf /build

# Copy all connector scripts
COPY connectors/ais/bin/*.py /usr/local/bin/
COPY connectors/camera/bin/*.py /usr/local/bin/
COPY connectors/foxglove/bin/*.py /usr/local/bin/
COPY connectors/klog/bin/*.py /usr/local/bin/
COPY connectors/mcap/bin/*.py /usr/local/bin/
COPY connectors/mediamtx/bin/*.py /usr/local/bin/
COPY connectors/mockups/bin/*.py /usr/local/bin/
COPY connectors/nmea/bin/*.py /usr/local/bin/
COPY connectors/platform/bin/*.py /usr/local/bin/
COPY connectors/platform/config-schema.json /usr/local/bin/
COPY connectors/rtcm/bin/*.py /usr/local/bin/
COPY connectors/tak/bin/*.py /usr/local/bin/
COPY connectors/entity_health/bin/*.py /usr/local/bin/
COPY connectors/mavlink/bin/*.py /usr/local/bin/

# Strip the .py extension from entry-point scripts so they run as commands.
# Library modules imported by a sibling (e.g. nmea's n2k_gateway.py, ngx1.py)
# keep their .py extension -- without it `import n2k_gateway` would fail. An
# entry point is identified by its `if __name__ == "__main__"` guard.
RUN for f in /usr/local/bin/*.py; do \
        if grep -q "__main__" "$f"; then \
            mv "$f" "${f%.py}"; \
        fi; \
    done && \
    chmod +x /usr/local/bin/*

# Entrypoint inherited from porla-maritime
