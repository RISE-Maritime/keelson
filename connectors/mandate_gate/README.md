# mandate_gate

The vessel's gate on the ROC mandate: she is held short of waters the ROC site on her watch has no
mandate for.

A ROC site operates a vessel only where it holds a mandate: a licence, a contract, a sector, a
border. Where that mandate ends, the watch must have been handed to a site whose mandate covers
what lies ahead. If it has not, she must not sail on. This connector runs onboard and checks it.
It asks for a hold on `mandate_gate`, and leaves carrying it out to whoever steers her.

## What it reads, and what it decides

- **Her watch.** The newest ACCEPTED `checklist_handover` for her names the relief's `roc_site`.
  Before there is one, the watch is the config's `initial_watch`.
  - It is the **watch**, not the conn: a handover record does not move `command_authority`.
  - The records are read from the ROC's tree, as for `watch_handover`: from storage at start, then
    live.
  - Both wire forms are read: the protobuf, and the legacy JSON still in old stores.
- **Where she is going.** Her own `location_fix`, `course_over_ground_deg` and
  `speed_over_ground_knots`, under her entity, from any source. A reading older than
  `fix_max_age_s` does not count. The gate is onboard: it reads what she knows.
- **The areas.** Each area is a polygon with the ROC sites holding its mandate.
  - A position is forbidden to a watch when it lies in at least one area and in none that names
    the watch.
  - Water outside every area is open, and an overlap with an area the watch holds is a shared
    corridor.
- **The hold.** It is a loiter: make for the point, lie near it, and come back once she drifts
  past `loiter_radius_m`. She coasts past the point by her stopping distance (`stopping`, scaled
  with her speed), so the point is placed that far short of the line plus `margin_m`. It is moved
  further back until the whole circle keeps the margin.
  - **When it is asked for:** once she is within `stopping + margin + radius + speed × reaction_s`
    of the line, along her course over the ground.
  - **Further out:** the gate reads CLEAR and names the area ahead and how far away it is, for
    `warn_s` of sailing.
  - **Stopped:** at under `min_course_knots` her course is noise, so only "is she already in it" is
    judged.
- **What ends a hold:**
  - the watch reaching a site that holds the area: CLEAR. **CLEAR re-arms nothing.** She stays in
    her hold until the new watch orders her on;
  - her being taken out of the hold, away from forbidden water: CLEAR;
  - her being taken out of the hold toward forbidden water: a **new** hold, with a new `hold_id`.
    The gate stands.
- **No fix:** NO_FIX. No new hold is asked for, and any standing hold is kept.

`hold_id` rises once per hold. A consumer carries a hold out the first time it sees the id.

## `mandate-gate2keelson`

```
mandate-gate2keelson -r rise -e <vessel> --config gate.json \
    [--checklist-realm rise] [--checklist-entity roc1] [-s mandate-gate] [--publish-interval-s 1]
```

It publishes `{realm}/@v0/{vessel}/pubsub/mandate_gate/{source}` (`keelson.MandateGate`) every
interval, and at once on any change of state, hold or watch.

`gate.json`:

```json
{
  "initial_watch": "ROC-1",
  "areas": [
    {"id": "west-sector", "roc_sites": ["ROC-1"], "polygon": [[57.70, 11.60], [57.75, 11.60], [57.75, 11.80], [57.70, 11.80]]},
    {"id": "east-sector", "roc_sites": ["ROC-2"], "polygon": [[57.70, 11.80], [57.75, 11.80], [57.75, 12.00], [57.70, 12.00]]}
  ],
  "margin_m": 200,
  "loiter_radius_m": 400,
  "reaction_s": 10,
  "stopping": {"speed_knots": 11, "distance_m": 1088},
  "warn_s": 600,
  "fix_max_age_s": 10
}
```

`stopping` is her distance to stop with the engine stopped, at that speed, from her pilot card or
trials. Leave it out for a hull that stops in her own length.

## Limits

- **Her course is projected as a straight line.** A ship about to alter course away from the line,
  inside the trigger distance, is held all the same. The cost is a hold that was not needed, never
  a crossing.
- **She may still cross.** A ship already closer than her stopping distance when the area comes
  into view, for example from a turn, is held where she is, and the reason says so.
- **The watch is read, not checked.** The gate trusts the ROC's handover records as the stations
  do.
