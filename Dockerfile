FROM python:3.11-bullseye as wheelhouse

RUN mkdir wheelhouse

# Make sure brefv is available
COPY ./brefv /brefv

# And all requirements
COPY requirements.txt requirements.txt
COPY keelson-interface-mockups/requirements.txt keelson-mockups-requirements.txt
COPY keelson-interface-mediamtx/requirements.txt keelson-mediamtx-requirements.txt
COPY keelson-interface-ouster/requirements.txt keelson-ouster-requirements.txt
COPY keelson-interface-opendlv/requirements.txt keelson-opendlv-requirements.txt

# And build all wheels in one go to ensure proper dependency resolution
RUN pip3 wheel\
    /brefv/python\
    -r requirements.txt\
    -r keelson-mockups-requirements.txt\
    -r keelson-mediamtx-requirements.txt\
    -r keelson-ouster-requirements.txt\
    -r keelson-opendlv-requirements.txt\
    --wheel-dir /wheelhouse


FROM python:3.11-slim-bullseye

# Install all dependecies
COPY --from=wheelhouse /wheelhouse /wheelhouse
RUN pip3 install /wheelhouse/*

# Copy "binaries" to image
COPY --chmod=555 ./core/bin/* /usr/local/bin
COPY --chmod=555 ./keelson-interface-mockups/bin/* /usr/local/bin
COPY --chmod=555 ./keelson-interface-mediamtx/bin/* /usr/local/bin
COPY --chmod=555 ./keelson-interface-ouster/bin/* /usr/local/bin
COPY --chmod=555 ./keelson-interface-opendlv/bin/* /usr/local/bin

ENTRYPOINT ["/bin/bash", "-l", "-c"]

