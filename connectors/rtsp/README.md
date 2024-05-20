# rtsp

Provides an interface to rtsp video streams, it uses OpenCV so other sources compatible with `cv2.VideoCapture(args.url)`. Outputs raw or compressed image frames to a keelson topic. 

## Run with Docker Compose file

```yml

# docker-compose.camera.yml

services:

  # Grabbing each frame of the rtsp stream and push to Keelson's 
  camera-1:
    image: ghcr.io/mo-rise/keelson:0.3.5-pre.1
    container_name: CAMERA-1
    restart: unless-stopped
    network_mode: host
    command:
      [
        "rtsp to_frames --cam-url rtsp://localhost:8554/cam-axis-1 -r rise -e boatswain -s purpose --send jpeg"
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