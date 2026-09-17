# Navigation decisions **[proposed]**

A protocol in the sense of [protocol specification §8](../protocol-specification.md#8-protocol-specifications).
Payloads: `NavigationState`, `Encounter`, `NavigationAdvice`, `AdviceDisposition`
(`messages/payloads/`).

## 1. Purpose

Route planning (§6) says where a vessel means to go and `route_execution` says
how it is doing. Nothing on the bus says what the vessel's navigation function
**decided** along the way: which encounters it assessed, under which rule, what
it advised, and whether anyone acted on that advice. This protocol records
those decisions so they can be reviewed afterwards. It does not add a way to
command the vessel.

**Advice is a record, never a command.** Nothing acts on a `NavigationAdvice`
by receiving it. Acting on it is a separate, authorised call on the vehicle
interfaces (§5, step 5), and that call is where recording ends and acting
starts.

## 2. Roles

| Role | Does |
|---|---|
| **Navigation process** | States its own mode and the voyage, route edition and authority it works from (`navigation_state`). |
| **Advisor** | Assesses encounters (`encounter`) and advises action (`navigation_advice`). Retires its own advice (`advice_disposition` SUPERSEDED / EXPIRED). Holds nothing. |
| **Decision holder** | Whoever holds the decision for the navigation function: an operator at a station, or a conduct service under a grant. Accepts or rejects advice (`advice_disposition` ACCEPTED / REJECTED). |
| **Conn holder** | The holder of the live `command_authority` lease. The only role that calls the vehicle interfaces. |

One process may hold several roles. They stay separate on the wire, and the
invariants in §6 apply whichever process holds them.

## 3. Keys

Keys after `{base_path}/@v0/{entity_id}/pubsub/`. `{producer}` is §2.1.1's
producer chunk(s). `{voyage}` is the `voyage_id`, or the sentinel `novoyage`.

| Subject | Key template | Shape (§2.1.1) | Storage | Rate / cardinality |
|---|---|---|---|---|
| `navigation_state` | `navigation_state/{producer}` | producer only | none | 1 Hz, latest wins |
| `encounter` | `encounter/{producer}/{voyage}/{encounter_id}` | instance | latest | one key per encounter, restated in full until it closes |
| `navigation_advice` | `navigation_advice/{producer}/{voyage}/{advice_id}` | instance | latest | one key per advice, restated while the manoeuvre holds |
| `advice_disposition` | `advice_disposition/{voyage}/{advice_id}` | slot | latest | one key per advice, last disposition wins |

**`novoyage`.** The literal chunk `novoyage` fills the `{voyage}` position when
own ship has no voyage. The payload's `voyage_id` is then absent (not empty),
and a real `voyage_id` MUST NOT be `novoyage`. The chunk is always present,
so a key's depth never depends on whether a voyage exists and a selector such
as `encounter/**/novoyage/*` finds every voyage-less record. This protocol has
no other sentinel. In particular, `route_execution`'s key for a declared route
without a voyage is not defined here.

**Why `advice_disposition` is a slot.** Two roles write it: the decision holder
(ACCEPTED, REJECTED) and the advisor (SUPERSEDED, EXPIRED). The disposition of
one advice is one position. Putting the producer in the key would split it into
two keys, and a storage would then hold an ACCEPTED and an EXPIRED for the same
advice with no rule for which one stands. The writer is in the payload
(`decided_by`), as §2.1.1 requires of a slot. `advice_id` is a UUID, so the key
cannot collide across advisors.

**Storage** backing a subject selects
`{base_path}/@v0/*/pubsub/{subject}/**`. `encounter`, `navigation_advice` and
`advice_disposition` are meant to be backed by one. `navigation_state` is not
persisted.

All four subjects use the `elevated` QoS profile.

## 4. Lifecycle

The proto default (`*_UNSPECIFIED = 0`) is never a state.

**`NavigationState.mode`**: set only by the navigation process.
`IDLE → READY → UNDERWAY ⇄ FALLBACK`, then `UNDERWAY → ARRIVED → ENDED` or
any mode `→ ENDED`. `sub_mode` refines `UNDERWAY` only and is `UNSPECIFIED` in
every other mode.

**`Encounter`**: set only by the advisor.

| Field | States | Transition |
|---|---|---|
| `closed_at` | absent (open) → present (closed) | once; a closed encounter is not reopened, a new one gets a new `encounter_id` |
| `situation`, `own_role` | latched | set when the encounter opens, never changed |
| `risk` | `NONE`, `WATCH`, `RISK`, `DANGER` | any to any while open |
| `peak_risk` | same | only rises |

**`AdviceDisposition.state`**:

| From | To | Role |
|---|---|---|
| (none) | `ACCEPTED`, `REJECTED` | decision holder |
| (none), `ACCEPTED` | `SUPERSEDED` (with `superseded_by`) | advisor |
| (none), `ACCEPTED` | `EXPIRED` (after `valid_to`) | advisor |

`REJECTED`, `SUPERSEDED` and `EXPIRED` are terminal.

## 5. Sequence

```
navigation process ──navigation_state (1 Hz)──────────────────────────────▶
advisor           ──encounter (open, restated)──▶
advisor           ──navigation_advice──▶
decision holder                         ──advice_disposition ACCEPTED──▶
conn holder                                                ──RPC VehicleNavigation──▶ vessel
advisor           ──encounter (closed_at set)──▶ ──advice_disposition EXPIRED / SUPERSEDED──▶
```

1. **Advisor** publishes `encounter` when it opens one, then restates the whole record on each change. Guard: none.
2. **Advisor** publishes `navigation_advice` naming the `encounter_ids` it answers. Guard: `encounter_ids` lists every open encounter at `RISK_RISK` or above; for `KIND_RESUME_TRACK`, the encounters it avoided, now closed.
3. **Decision holder** publishes `advice_disposition` `ACCEPTED` or `REJECTED`. Guard: now is inside `[valid_from, valid_to]`.
4. **Advisor** publishes `SUPERSEDED` when it issues a different manoeuvre, or `EXPIRED` once `valid_to` has passed.
5. **Conn holder** calls **an RPC on a vehicle interface**: `VehicleNavigation.set_steering_order` for the steering proposal, `set_cruise_speed` for the propulsion proposal. Guard: it holds a live `command_authority` lease, the disposition is `ACCEPTED`, and now is inside the advice's validity window. **This step acts. Steps 1–4 only record.**

## 6. Invariants

1. Nothing acts on a `NavigationAdvice` or an `AdviceDisposition` by receiving it. Only step 5 acts.
2. Every `encounter` record restates the whole encounter, so the store's last value is complete.
3. `situation` and `own_role` do not change within an `encounter_id`.
4. `Encounter.situation` and a Route leg's situation use the same `ColregSituation` enum. A Route leg carries 1–4 only.
5. `NavigationState.authority_level_in_force` uses the same enum as `OperationalAuthority`, and `UNKNOWN` does not authorise anything.
6. `policy_config_digest` on advice identifies the policy that produced it. It is not an authorisation.
7. `NavigationState` overlaps `RouteExecution` (`voyage_id`, `route_ref`, `current_leg_from_waypoint_id`) on purpose: they come from different producers. `NavigationState` is not a lock or a grant. A consumer deciding whether to act reads `command_authority` and `operational_authority`, never `navigation_state`.

## 7. Not solved

- **Who may be a decision holder.** A disposition says who decided (`decided_by`, free text). It does not prove that person or service was entitled to decide. Authorising decision holders is a separate problem.
- **More than one advisor** advising on the same encounter. The keys keep their records apart, but nothing arbitrates between them.
- **Checking advice against the chart.** `rationale` should say when that was not done.
- **Presence of `RouteExecution.speed_over_ground_knots` / `course_over_ground_deg`.** A separate change.
- **`route_execution` for a declared route with no voyage.**
- **Migrating the as-built §6 instance keys** to carry a producer chunk.
