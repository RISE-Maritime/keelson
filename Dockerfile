FROM python:3.11-bullseye as wheelhouse

RUN mkdir wheelhouse

# Make sure brefv is available
COPY ./brefv /brefv

# And all requirements
COPY keelson-record/requirements.txt keelson-record-requirements.txt
COPY keelson-rest-api/requirements.txt keelson-rest-api-requirements.txt

# And build all wheels in one go to ensure proper dependency resolution
RUN pip3 wheel\
    /brefv/python\
    -r keelson-record-requirements.txt\
    -r keelson-rest-api-requirements.txt\
    --wheel-dir /wheelhouse


FROM python:3.11-slim-bullseye

# Install all dependecies
COPY --from=wheelhouse /wheelhouse /wheelhouse
RUN pip3 install /wheelhouse/*

# Copy "binaries" to image
COPY --chmod=555 ./keelson-record/record /usr/local/bin
COPY --chmod=555 ./keelson-rest-api/rest-api /usr/local/bin

ENTRYPOINT ["/bin/bash", "-l", "-c"]

