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

# Install the LabJack LJM library -- the native dependency the labjack connector
# loads at runtime (the `labjack-ljm` pip wrapper alone is not enough). Headless
# flags follow LabJack's Docker guidance: skip the Kipling GUI and don't touch
# udev device rules (those belong to the host). x86_64 / linux/amd64 only.
RUN apt-get update \
    && apt-get install -y --no-install-recommends wget unzip libusb-1.0-0-dev udev \
    && wget -q https://files.labjack.com/installers/LJM/Linux/x64/release/LabJack-LJM_2024-06-10.zip -O /tmp/ljm.zip \
    && unzip -q /tmp/ljm.zip -d /tmp/ljm \
    && INSTALLER="$(find /tmp/ljm -name labjack_ljm_installer.run | head -n1)" \
    && chmod +x "$INSTALLER" \
    # Blank BASH_ENV: the base image's /bash-init.sh hook runs `set -u` and the
    # installer spawns a non-interactive bash where BASH_EXECUTION_STRING is unset.
    && BASH_ENV= "$INSTALLER" -- --without-kipling --no-restart-device-rules \
    && rm -rf /tmp/ljm /tmp/ljm.zip \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

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
COPY connectors/rtcm/requirements.txt /build/reqs/rtcm.txt
COPY connectors/tak/requirements.txt /build/reqs/tak.txt
COPY connectors/entity_health/requirements.txt /build/reqs/entity_health.txt
COPY connectors/mavlink/requirements.txt /build/reqs/mavlink.txt
COPY connectors/labjack/requirements.txt /build/reqs/labjack.txt

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
    -r /build/reqs/rtcm.txt \
    -r /build/reqs/tak.txt \
    -r /build/reqs/entity_health.txt \
    -r /build/reqs/mavlink.txt \
    -r /build/reqs/labjack.txt

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
COPY connectors/labjack/bin/*.py /usr/local/bin/
COPY connectors/labjack/config-schema.json /usr/local/bin/

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
