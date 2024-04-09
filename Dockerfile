FROM python:3.11-bullseye as wheelhouse

RUN mkdir wheelhouse

# Make sure relevant parts of the repo is available
COPY ./messages /messages
COPY ./sdks/python /sdks/python
COPY ./connectors /connectors
COPY requirements.txt requirements.txt

# And build all wheels in one go to ensure proper dependency resolution
RUN pip3 wheel\
    /sdks/python\
    -r requirements.txt\
    --wheel-dir /wheelhouse


FROM python:3.11-slim-bullseye

# Install all dependecies
COPY --from=wheelhouse /wheelhouse /wheelhouse
RUN pip3 install /wheelhouse/*

# Copy "binaries" to image
COPY --chmod=555 ./connectors/*/bin/* /usr/local/bin

ENTRYPOINT ["/bin/bash", "-l", "-c"]

