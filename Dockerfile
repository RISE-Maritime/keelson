FROM python:3.11-bullseye as wheelhouse

RUN mkdir wheelhouse

# Make sure relevant parts of the repo is available
COPY ./messages /messages
COPY ./sdks/python /sdks/python
COPY ./connectors /connectors
COPY requirements_connectors.txt requirements_connectors.txt

# And build all wheels in one go to ensure proper dependency resolution
RUN pip3 wheel\
    /sdks/python\
    -r requirements_connectors.txt\
    --wheel-dir /wheelhouse


FROM python:3.11-slim-bullseye

# Using tini to be PID 1 and handle signals
ADD https://github.com/krallin/tini/releases/download/v0.19.0/tini /tini
RUN chmod +x /tini

# Install all dependecies
COPY --from=wheelhouse /wheelhouse /wheelhouse
RUN pip3 install /wheelhouse/*

# Copy "binaries" to image
COPY --chmod=555 ./connectors/*/bin/* /usr/local/bin

ENTRYPOINT ["/tini", "-g", "--", "/bin/bash", "-c"]

