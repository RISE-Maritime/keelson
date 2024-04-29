# rtsp

Provides an interface to rtsp video streams. Outputs raw and compressed image frames to a keelson topic.

arg 
- send = raw or compressed
- save = jpeg , webp or png

```bash




```


```yml

# docker-compose.camera.yml

services:

  # Grabbing each frame of the rtsp stream and push to Keelson's 
  camera-1:
    image: ghcr.io/mo-rise/keelson:0.3.3-pre.1
    container_name: CAMERA-1
    restart: unless-stopped
    network_mode: host
    command:
      [
        "rtsp to_frames  -r rise -e boatswain -s purpose --compress jpeg -s axis-1 -u rtsp://root:prepare@10.10.20.2/axis-media/media.amp?camera=1"
      ]



# Grabbing each frame of the rtsp stream and or SAVING to file 
  camera-1-saving:
    image: ghcr.io/mo-rise/keelson:0.3.3-pre.1
    container_name: CAMERA-1-TO-FILE
    network_mode: host
    volume: <your_paht>:/rec
    command:
      [
        "rtsp to_frames --save jpeg --url rtsp://root:prepare@10.10.20.2/axis-media/media.amp?camera=1 --source-id axis-1"
      ]
 
```