FROM python:3.11-bullseye as wheelhouse

RUN mkdir wheelhouse

# Make sure brefv is available
COPY ./brefv /brefv

# And all requirements
COPY requirements.txt requirements.txt
COPY keelson-interface-mcap/requirements.txt keelson-mcap-requirements.txt
COPY keelson-interface-http/requirements.txt keelson-http-requirements.txt
COPY keelson-interface-video/requirements.txt keelson-video-requirements.txt
COPY keelson-interface-lidar/requirements.txt keelson-lidar-requirements.txt

# And build all wheels in one go to ensure proper dependency resolution
RUN pip3 wheel\
    /brefv/python\
    -r requirements.txt\
    -r keelson-mcap-requirements.txt\
    -r keelson-http-requirements.txt\
    -r keelson-video-requirements.txt\
    -r keelson-lidar-requirements.txt\
    --wheel-dir /wheelhouse


FROM python:3.11-slim-bullseye

# Install all dependecies
COPY --from=wheelhouse /wheelhouse /wheelhouse
RUN pip3 install /wheelhouse/*

# Copy "binaries" to image
COPY --chmod=555 ./keelson-interface-mcap/bin/* /usr/local/bin
COPY --chmod=555 ./keelson-interface-http/bin/* /usr/local/bin
COPY --chmod=555 ./keelson-interface-video/bin/* /usr/local/bin
COPY --chmod=555 ./keelson-interface-lidar/bin/* /usr/local/bin

ENTRYPOINT ["/bin/bash", "-l", "-c"]

