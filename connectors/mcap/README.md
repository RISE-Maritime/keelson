# mcap

Provides an interface to the [mcap] file format through two binaries:

* mcap-record

  Record envelopes to an mcap file injecting the appropriate message schemas for all well-known payloads.

* mcap-replay

  Replays all envelopes from a previously recorded mcap file.