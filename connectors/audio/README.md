# Audio connector

Audio capture, live listening and export for Keelson. The design and its
reasons are in [decision 0002](../../docs/decisions/0002-audio.md).

Audio travels on the bus as `keelson.AudioFrame` on the `audio_frame` subject.
A frame holds one 20 ms Opus packet by default (about 32 kbit/s per source), or
PCM for sources whose samples are themselves the measurement, such as
hydrophones. The subject uses the `media_live` QoS profile: best-effort and not
batched. A lost frame is played as silence. The same frames serve listening and
recording, so `keelson2mcap` records audio with no extra setup, and replay plays
it back.

| Binary | Role |
|---|---|
| `audio2keelson` | Capture from a sound device, or from any URL or file FFmpeg opens (RTSP, a MediaMTX path, `.wav`), and publish `audio_frame` |
| `keelson2audio` | Play one or more `audio_frame` streams, mixed, on a sound device or into a WAV file |
| `audio-export` | Turn the audio in MCAP recordings into `.ogg` files (Opus, not re-encoded) or `.wav` files |

## Examples

Put the bridge microphone on the bus:

```bash
audio2keelson -r rise -e vessel -s audio/bridge -f bridge --device "USB Audio"
```

Record the VHF receiver's audio from a MediaMTX path:

```bash
audio2keelson -r rise -e vessel -s audio/vhf -f vhf_ch16_rx --url rtsp://mediamtx:8554/vhf
```

Listen to every audio source on a vessel, from shore:

```bash
keelson2audio -k 'rise/@v0/vessel/pubsub/audio_frame/**' --connect tcp/router:7447
```

Export the audio of a recording, for an incident review:

```bash
audio-export recordings/2026-11-12_*.mcap -o incident/ --start 2026-11-12T14:05:00 --end 2026-11-12T14:20:00
```

Each source becomes one file named after its entity, source and start time.
Missing audio shorter than `--split-gap` is filled with silence so the file stays
aligned with the rest of the recording. A longer gap starts a new file.

## Notes

- **Devices in Docker.** Pass the sound devices to the container
  (`--device /dev/snd`, plus `--group-add audio`). Use `--list-devices` to see
  what PortAudio finds.
- **Latency.** The sink holds `--jitter-frames` frames (60 ms by default) before
  it starts playing. That is the trade between latency and the jitter it can
  absorb.
- **Replay faster than 1x.** Frames arrive faster than they play. The sink keeps
  at most `--max-frames` per source and drops the rest, so the audio is choppy.
  Mute it instead.
- **Timestamps.** A frame's `timestamp` is the wall-clock time of its first
  sample. For device capture it is estimated from when the block arrives. Opus
  adds 6.5 ms of codec delay at the start of a stream.

## `audio2keelson`

```
usage: audio2keelson [-h] [--log-level LOG_LEVEL] [--mode {peer,client}] [--connect CONNECT]
                     [--listen LISTEN] [--zenoh-config ZENOH_CONFIG] [-r REALM] [-e ENTITY_ID]
                     [-s SOURCE_ID] (--device DEVICE | --url URL | --list-devices)
                     [--format {opus,pcm_s16le}] [--sample-rate SAMPLE_RATE] [--channels CHANNELS]
                     [--frame-ms FRAME_MS] [--bitrate BITRATE] [-f FRAME_ID] [--pace | --no-pace]

Capture audio from a sound device or URL and publish it as keelson.AudioFrame on the audio_frame
subject

options:
  -h, --help            show this help message and exit
  --log-level LOG_LEVEL
                        Logging level (default: INFO) (default: 20)
  --mode, -m {peer,client}
                        The Zenoh session mode. (default: None)
  --connect CONNECT     Endpoints to connect to. Example: tcp/localhost:7447 (default: None)
  --listen LISTEN       Endpoints to listen on. Example: tcp/0.0.0.0:7447 (default: None)
  --zenoh-config ZENOH_CONFIG
                        Path to a Zenoh configuration file (JSON5). Everything the flags above
                        cannot express — access control, QoS defaults, transport tuning — lives
                        here. --mode/--connect/--listen still win where they overlap. Falls back
                        to the ZENOH_CONFIG environment variable. (default: None)
  -r, --realm REALM     Realm (e.g. rise). Required (default: None)
  -e, --entity-id ENTITY_ID
                        Entity id (e.g. landkrabba). Required (default: None)
  -s, --source-id SOURCE_ID
                        Source id (e.g. audio/bridge). Required (default: None)
  --device DEVICE       Input device: an index or (part of) a name from --list-devices, or
                        "default" (default: None)
  --url URL             Any URL or file PyAV/FFmpeg can open (rtsp://..., a .wav file, ...)
                        (default: None)
  --list-devices        Print the sound devices and exit (default: False)
  --format {opus,pcm_s16le}
                        Encoding on the bus. pcm_s16le is for sources whose samples are the
                        measurement (hydrophones), not for speech (default: opus)
  --sample-rate SAMPLE_RATE
                        Hz (default: 48000)
  --channels CHANNELS   Channel count (Opus: 1 or 2) (default: 1)
  --frame-ms FRAME_MS   Frame length (default: 20)
  --bitrate BITRATE     Opus bitrate in bit/s (default: 32000)
  -f, --frame-id FRAME_ID
                        What the source listens to, e.g. bridge or vhf_ch16_rx (default: None)
  --pace                Hold --url input to real time (the default for files)
  --no-pace             Publish --url input as fast as it decodes (the default for network
                        streams)
```

## `keelson2audio`

```
usage: keelson2audio [-h] [--log-level LOG_LEVEL] [--mode {peer,client}] [--connect CONNECT]
                     [--listen LISTEN] [--zenoh-config ZENOH_CONFIG] [-k KEY] [--device DEVICE |
                     --output-file OUTPUT_FILE | --list-devices] [--sample-rate SAMPLE_RATE]
                     [--channels CHANNELS] [--block-ms BLOCK_MS] [--jitter-frames JITTER_FRAMES]
                     [--max-frames MAX_FRAMES]

Play keelson.AudioFrame streams (subject audio_frame) on a sound device or into a WAV file

options:
  -h, --help            show this help message and exit
  --log-level LOG_LEVEL
                        Logging level (default: INFO) (default: 20)
  --mode, -m {peer,client}
                        The Zenoh session mode. (default: None)
  --connect CONNECT     Endpoints to connect to. Example: tcp/localhost:7447 (default: None)
  --listen LISTEN       Endpoints to listen on. Example: tcp/0.0.0.0:7447 (default: None)
  --zenoh-config ZENOH_CONFIG
                        Path to a Zenoh configuration file (JSON5). Everything the flags above
                        cannot express — access control, QoS defaults, transport tuning — lives
                        here. --mode/--connect/--listen still win where they overlap. Falls back
                        to the ZENOH_CONFIG environment variable. (default: None)
  -k, --key KEY         Key expression to play, e.g.
                        rise/@v0/vessel/pubsub/audio_frame/audio/bridge. Repeatable; every
                        matching source is mixed in. Required unless --list-devices (default:
                        None)
  --device DEVICE       Output device: an index or (part of) a name from --list-devices, or
                        "default" (default: None)
  --output-file OUTPUT_FILE
                        Write the mix to this WAV file in real time instead of playing it
                        (default: None)
  --list-devices        Print the sound devices and exit (default: False)
  --sample-rate SAMPLE_RATE
                        Output rate, Hz (default: 48000)
  --channels CHANNELS   Output channels (default: 1)
  --block-ms BLOCK_MS   Output block length; the playout tick (default: 10)
  --jitter-frames JITTER_FRAMES
                        Frames buffered before playout starts: absorbs network jitter, adds
                        latency (default: 3)
  --max-frames MAX_FRAMES
                        Frames buffered at most per source; beyond this the oldest are dropped
                        (default: 25)
```

## `audio-export`

```
usage: audio-export [-h] [-o OUTPUT_DIR] [--format {auto,ogg,wav}] [-t TOPIC] [--start START]
                    [--end END] [--split-gap SPLIT_GAP] [--list] [--log-level LOG_LEVEL]
                    mcap [mcap ...]

Export audio_frame sources in MCAP recordings to .ogg/.wav files

positional arguments:
  mcap                  MCAP file(s) of one recording, in time order (rotated files in sequence)

options:
  -h, --help            show this help message and exit
  -o, --output-dir OUTPUT_DIR
                        Where to write the files (default: .)
  --format {auto,ogg,wav}
                        auto: Ogg Opus for Opus sources, WAV for PCM sources (default: auto)
  -t, --topic TOPIC     Only export sources whose MCAP topic matches this regular expression
                        (default: None)
  --start START         Export from this time (epoch seconds or ISO 8601) (default: None)
  --end END             Export until this time (epoch seconds or ISO 8601) (default: None)
  --split-gap SPLIT_GAP
                        Seconds of missing audio after which a new file is started (default: 5.0)
  --list                List the audio sources in the files and exit (default: False)
  --log-level LOG_LEVEL
                        Log level 10=DEBUG, 20=INFO, 30=WARN, 40=ERROR, 50=CRITICAL (default: 20)
```
