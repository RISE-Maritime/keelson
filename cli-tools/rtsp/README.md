# rtsp

Provides an interface to rtsp video streams, it uses OpenCV so other sources compatible with `cv2.VideoCapture(args.url)` are possible but might not be fully compatible. Outputs raw or compressed image frames to a keelson topic.

## Help description

```bash
usage: rtsp to_frames [-h] -u CAM_URL [-r REALM] [-e ENTITY_ID] [-s SOURCE_ID]
                      [-f FRAME_ID] [--save {raw,webp,jpeg,png}]
                      [--send {raw,webp,jpeg,png}]

options:
  -h, --help            show this help message and exit
  -u CAM_URL, --cam-url CAM_URL
                        RTSP URL or any other video source that OpenCV can
                        handle
  -r REALM, --realm REALM
  -e ENTITY_ID, --entity-id ENTITY_ID
  -s SOURCE_ID, --source-id SOURCE_ID
  -f FRAME_ID, --frame-id FRAME_ID
                        Frame ID for Foxglove
  --save {raw,webp,jpeg,png}
  --send {raw,webp,jpeg,png}
```

## Run with Docker compose file

```yml

# docker-compose.camera.yml

services:

  # Grabbing each frame of the rtsp stream and push to Keelson's as a jpeg frame
  camera-1:
    image: ghcr.io/mo-rise/keelson:0.3.5
    container_name: CAMERA-1
    restart: unless-stopped
    network_mode: host
    command:
      [
         "rtsp --log-level 10 to_frames --cam-url rtsp://root:prepare@10.10.20.2/axis-media/media.amp?camera=1 -r rise -e boatswain -s purpose --send jpeg"
      ]



# Grabbing each frame of the rtsp stream and or SAVING to file 
  camera-1-saving:
    image: ghcr.io/mo-rise/keelson:0.3.3-pre.1
    container_name: CAMERA-1-TO-FILE
    network_mode: host
    volume: <your_paht>:/rec
    command:
      [
        "rtsp to_frames --save jpeg --cam-url rtsp://root:prepare@10.10.20.2/axis-media/media.amp?camera=1 --source-id axis-1"
      ]
 
```

## Run direct from docker image 

```bash

# Send frames 
docker run --network host ghcr.io/mo-rise/keelson:0.3.5 "rtsp --log-level 10 to_frames --cam-url rtsp://root:prepare@10.10.20.2/axis-media/media.amp?camera=1 -r rise -e boatswain -s purpose --send jpeg"

# Save frames 
docker run --network host --volume /home/user/rec_frames:/rec ghcr.io/mo-rise/keelson:0.3.4 "rtsp to_frames --cam-url rtsp://localhost:8554/cam-axis-1 --save jpeg"

```