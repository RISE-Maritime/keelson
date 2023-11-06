# Example: Proxying WHEP requests over a zenoh network

Making a remote WHEP (WebRTC HTTP Egress Protocol) across a zenoh network.

This example makes use of the following parts:
* A html/javascript example page making a WHEP-like request to a local POST endpoint (this example)
* A local rest-to-zenoh bridge (`rest-api` in this repo)
* A remote zenoh whep client (see https://github.com/MO-RISE/keelson-interface-mediamtx/blob/main/bin/whep-proxy)
* A remote [MediaMTX](https://github.com/bluenviron/mediamtx) instance with a feed on path `example`