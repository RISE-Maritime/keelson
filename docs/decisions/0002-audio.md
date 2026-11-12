# 0002 — Audio travels as frames on the bus; talk-back is a floor held at the vessel

**Status:** proposed · **Date:** 2026-11-12 ·
**Subjects:** `audio_frame`, `audio_output_state` · **Interface:** `audio_output/v1` ·
**Protocol:** `audio_floor`

## Context

A vessel's audio is part of its record and part of how it is operated:

- **Record.** The bridge, deck and engine-room microphones, and the audio received on VHF, are what a
  Voyage Data Recorder keeps. An incident review needs that audio aligned with the navigation data
  in the same recording.
- **Live listening.** An operator in a remote operations centre needs to hear the vessel: radio
  traffic, alarms sounding on the bridge, and the engine.
- **Talk-back.** The same operator needs to speak on board, through the PA, the intercom or VHF
  transmit, and only one person may speak at a time.

keelson has none of this today:

- `audio: keelson.Audio` is registered on `transient` QoS, but nothing publishes or consumes it.
- The type cannot carry a stream. It has MP3/WAV only, and no sample rate, channel count or sequence
  number. It also mixes a `text_to_voice` string into the same message.
- `keelson.Audio` is embedded in `Alarm` (field 9) as an alarm's sound cue.
- The live video path, MediaMTX with WHEP signalled over `whep_proxy/v1`, already negotiates an audio
  track. Audio from an RTSP source that has one plays in the browser today. That audio stays off the
  bus, though, so nothing records it, and the path only carries media from the vessel to the operator.

## Decision

### D1. A new payload, `keelson.AudioFrame`, on a new subject, `audio_frame`

| Field | Type | Meaning |
|---|---|---|
| `timestamp` | `google.protobuf.Timestamp` | Capture time of the frame's first sample |
| `frame_id` | `string` | Microphone or position label, e.g. `bridge`, `vhf_ch16_rx` |
| `format` | enum `FORMAT_UNSPECIFIED`, `OPUS`, `PCM_S16LE` | Encoding of `data` |
| `sample_rate` | `uint32` | Hz |
| `channels` | `uint32` | Interleaved for PCM |
| `sequence` | `uint64` | Increases by one per frame from a given source; a gap means a lost frame |
| `duration_us` | `uint32` | Length of the frame |
| `data` | `bytes` | One Opus packet, or the PCM samples |

- **A frame is the atom.** Under spec §2.2.1 rule 1b, one source's audio over a short interval is an
  indivisible frame and is published whole on one complex-payload subject.
- **The default is Opus, 48 kHz, mono, 20 ms.** That is about 32 kbit/s and 50 messages per second
  for each source. PCM is for sources whose samples are themselves the measurement, such as
  hydrophones and lab rigs.
- **The new type gets a new subject; `audio` is not repointed.** Spec §2.2.2 makes a complex type's
  subject the snake_case of its message name. Changing the type behind `audio` would also silently
  re-type every recording and consumer that knows the subject.
- **`audio` and `keelson.Audio` are kept as they are**, because `Alarm.audio` uses them for a one-shot
  sound cue. The docs mark them as legacy for streaming.

### D2. The bus carries live audio, not only recorded audio

Video is too heavy for the bus as a continuous stream. Audio is not: about 32 kbit/s per source is
less than a single compressed image per second. Putting live audio on the bus, in the same frames
that get recorded, means:

- **One capture.** The same frames are both recorded and listened to.
- **No special handling downstream.** MCAP recording, replay and the Foxglove bridge pick
  `audio_frame` up from the subject registry, and none of them changes.
- **One NAT-traversal story.** Audio uses the zenoh router path every other subject already uses.

To keep the latency low, a new QoS profile is added:

| Profile | Priority | Congestion | Reliability | Express |
|---|---|---|---|---|
| `media_live` | INTERACTIVE_HIGH | DROP | BEST_EFFORT | **true** |

`audio_frame` is assigned `media_live`. The existing `transient` profile would otherwise fit, but
it batches (`express: false`), and batching adds latency a listener can hear. A lost frame is
concealed by the Opus decoder, so best effort is the right reliability for this subject.

### D3. MediaMTX remains an optional path

- Browser viewers of an RTSP camera that has an audio track keep receiving that track over WHEP,
  with nothing changed.
- The capture connector can ingest an RTSP or MediaMTX URL as its input. Audio that already exists
  only in MediaMTX can therefore be put on the bus and recorded.
- Talk-back over WebRTC through MediaMTX (WHIP) is deferred; see *Not solved*.

### D4. Talk-back is a floor, arbitrated on the vessel by the speaker sink

Only one person may speak on a PA or a VHF channel at a time. The floor is a single-writer lease of
the same shape as the conn in decision 0001:

- **The arbiter is on board.** The vessel-side speaker sink decides who holds the floor, not any
  remote station.
- **The stream is the heartbeat.** The floor is held only while the holder's frames keep arriving.

| Element | Design |
|---|---|
| Interface | `audio_output/v1`, with `request_floor{claimant_id, source_key, priority}`, `release_floor` and `get_state` |
| What the sink plays | Only the holder's `source_key`, for as long as it holds the floor |
| Loss of the holder | The floor is released after `--floor-silence-timeout` without frames |
| Hard limit | The floor is released after `--max-transmit-seconds`, whatever is happening. This protects against a stuck transmitter. |
| Preemption | A strictly higher priority takes the floor |
| State for UIs | `audio_output_state` (`keelson.AudioOutputState`, `background` QoS) |
| PTT | Keyed by the sink, through serial RTS/DTR or a command hook. A remote station never keys it, and every exit path unkeys it. |
| Specification | `protocols/audio_floor.yaml` (spec §8) |

**The floor is separate from the conn.** Speaking on board is not actuation. A deployment might
later require a VHF floor holder to also hold the conn, so this is left as an optional flag for
when `conn/v1` is on `dev`.

### D5. Replay needs no change

- Recorded `audio_frame` is replayed with its recorded `enclosed_at`.
- At 1x it plays normally through the sink's jitter buffer.
- At any other speed, frames outrun the buffer and are dropped, so the audio comes out choppy. This
  is accepted. A UI that drives replay, such as crowsnest, should mute audio while
  `ReplayStatus.speed != 1`.

## Consequences

| Area | Change |
|---|---|
| `messages/` | `AudioFrame.proto`, `AudioOutputState.proto`, the subjects `audio_frame` and `audio_output_state`, and the QoS profile `media_live` |
| `interfaces/` | `AudioOutput.proto`, registered as `audio_output/v1` |
| `protocols/` | `audio_floor.yaml` |
| `connectors/audio/` (new) | `audio2keelson` (capture from a device or URL), `keelson2audio` (speaker sink, plus the floor server and PTT), and `audio-export` (MCAP to `.ogg` or `.wav`) |
| mcap, foxglove, replay | No change. They read `audio_frame` from the registry. |
| `Alarm.audio` | No change |

## Not solved

- **A browser player.** Crowsnest needs a JS SDK helper that decodes `audio_frame` with WebCodecs
  `AudioDecoder` behind an AudioWorklet jitter buffer. Until then, a browser listens only through
  MediaMTX.
- **WHIP talk-back.** This would let a browser speak through MediaMTX instead of publishing frames.
- **Gating on the conn.** Requiring a VHF floor holder to hold the conn waits for `conn/v1` on `dev`.
- **TTS and alarm cues on the sink.** This would play `Alarm.audio` and text-to-speech output.
- **Privacy.** Recording crew conversations has legal implications that vary by flag and by country.
  Which microphones are recorded, and for how long, is a deployment's decision, not keelson's.

## Open questions

1. Should the floor's priorities come from a signed per-vessel manifest, as the conn's do, or from
   the sink's command line until the conn lands?
2. Is 20 ms the right default frame length? 40 ms halves the message rate at the cost of 20 ms of
   added latency.
