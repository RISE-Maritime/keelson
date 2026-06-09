# Interfaces

**Work In Progress!!**
**Use with caution!!**

**Interfaces** in keelson are specifications of collections of rpc endpoints, i.e. remote function signatures. A single interface may contain any number of rpc endpoints. The interfaces are described using the protobuf service syntax.

## Design principles

These shape what belongs in an interface and how it should be modelled. The
full, worked-out list (with rationale and examples) lives in
[`connectors/CLAUDE.md` → "Interface design principles"](../connectors/CLAUDE.md);
the headlines:

- **Keep external protocol shapes out of interfaces.** No degE7 / fixed-point
  scaling, magic command numbers, or opaque `(param1..param4)` blobs. Interfaces
  are vehicle-agnostic — any connector implementing the same protocol should be
  able to expose the same contract.
- **Closed typed sets over opaque escape hatches.** Use `oneof` over a typed
  set for variant data rather than a discriminator + opaque payload. Where a
  raw pass-through is genuinely needed, expose it as a separate, intentionally
  protocol-shaped RPC and document the leak (e.g. `MavlinkCommand`).
- **Pick RPC vs pub/sub by data shape.** One-shot commands / queries with a
  typed success/failure are RPCs; continuous streams are pub/sub. A `cmd_*`
  subject almost always wants to be an RPC.
- **Reuse existing subjects for the data plane.** A connector's interface is
  the *configuration of which keys to use*, not a new payload type for data
  that an existing subject already carries.
- **Typed interfaces over generic catalogs; keep vendor reference data off the
  bus.** Model a specific operator action as a typed RPC named for the domain
  concept — not a generic key-value / "parameter" bag. Don't carry an external
  system's reference catalog (autopilot parameter tables, command numbering,
  mode lists) on the bus or bundle it into the core SDK: it's firmware-owned,
  versioned per release, and drifts. If such metadata is genuinely needed, pull
  it from its authoritative source. Values that need live control should each
  become a typed RPC (e.g. `set_cruise_speed`); comprehensive parameter tuning
  is a ground-control-station job.
