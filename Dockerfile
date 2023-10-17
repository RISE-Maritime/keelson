FROM python:3.11-bullseye as wheelhouse

RUN mkdir wheelhouse

# Make sure brefv is available
COPY ./brefv /brefv

# And the requirements for keelson-record
COPY keelson-record/requirements.txt keelson-record-requirements.txt

# And build all wheels in one go to ensure proper dependency resolution
RUN pip3 wheel /brefv/python -r keelson-record-requirements.txt --wheel-dir /wheelhouse


FROM python:3.11-slim-bullseye

# Install all dependecies
COPY --from=wheelhouse /wheelhouse /wheelhouse
RUN pip3 install /wheelhouse/*

# Copy "binaries" to image
COPY --chmod=555 ./keelson-record/record /usr/local/bin

ENTRYPOINT ["/bin/bash", "-l", "-c"]

