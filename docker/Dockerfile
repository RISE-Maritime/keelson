# Dockerfile - Keelson all-in-one image
#
# Based on porla-maritime which provides Python 3.13, canboat tools,
# and common utilities (shuffle, zenoh, mqtt, etc.)
#
# Published as: ghcr.io/rise-maritime/keelson

FROM ghcr.io/rise-maritime/porla-maritime:v0.2.0

# Install keelson SDK
COPY sdks/python /build/sdks/python
RUN pip install --no-cache-dir /build/sdks/python

# Copy and install connector dependencies from requirements.txt files
COPY connectors/ais/requirements.txt /build/reqs/ais.txt
COPY connectors/camera/requirements.txt /build/reqs/camera.txt
COPY connectors/foxglove/requirements.txt /build/reqs/foxglove.txt
COPY connectors/klog/requirements.txt /build/reqs/klog.txt
COPY connectors/mcap/requirements.txt /build/reqs/mcap.txt
COPY connectors/mediamtx/requirements.txt /build/reqs/mediamtx.txt
COPY connectors/mockups/requirements.txt /build/reqs/mockups.txt
COPY connectors/nmea/requirements.txt /build/reqs/nmea.txt
COPY connectors/platform/requirements.txt /build/reqs/platform.txt

RUN pip install --no-cache-dir \
    -r /build/reqs/ais.txt \
    -r /build/reqs/camera.txt \
    -r /build/reqs/foxglove.txt \
    -r /build/reqs/klog.txt \
    -r /build/reqs/mcap.txt \
    -r /build/reqs/mediamtx.txt \
    -r /build/reqs/mockups.txt \
    -r /build/reqs/nmea.txt \
    -r /build/reqs/platform.txt \
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

# Rename scripts (remove .py extension) and ensure executable
RUN for f in /usr/local/bin/*.py; do \
        mv "$f" "${f%.py}"; \
    done && \
    chmod +x /usr/local/bin/*

# Entrypoint inherited from porla-maritime
