# 0001 — Command authority follows coxswain's conn design

**Status:** proposed · **Date:** 2026-11-06 ·
**Protocol:** [`conn`](../protocols/conn.md) · **Interface:** `conn/v1`

## Context

Two designs answer the same question — *who may command the vessel, and what
happens when they stop* — and nothing connected them:

- **coxswain** ([freol35241/coxswain](https://github.com/freol35241/coxswain))
  is a control stack for small vessels that talks to keelson. One vessel-wide
  *conn*; claimants with integer priorities declared in a signed per-vessel
  manifest; a synchronous grant, with a strictly higher priority preempting;
  the setpoint stream doubling as the holder's heartbeat; and an onboard
  supervisor as the only arbiter, which holds station on its own authority when
  the holder falls silent. It is built, simulated end to end and locked in CI,
  including a test that kills the zenoh router mid-run and asserts the vessel
  keeps station.
- **keelson's command-authority stack** (open PRs #331 and the thirteen stacked
  on it) designs roles, per-domain fences, a two-phase transfer between an
  authority manager and a motion gate, and per-DOF control candidates. It is
  unreviewed, and its services run only in a hand-run loopback suite.

## Decision

**coxswain's conn design is keelson's command-authority model.** Where the two
overlap, coxswain's design is adopted as it is. What keelson's stack built that
coxswain does not have is carried over *on top of* the conn, never inside its
arbitration. Improvements the adoption needs on the coxswain side are listed
below for the coxswain maintainer to take as they see fit.

keelson takes coxswain's protocol upstream as it stands: the `conn/v1` interface
(`interfaces/Conn.proto`), the `setpoint`, `conn_state` and `manifest` subjects
(`messages/payloads/ConnState.proto`), and the protocol specification
`protocols/conn.yaml`. Field numbers, result codes and semantics are coxswain's.
The adaptations are keelson conventions only:

| coxswain | keelson | Why |
|---|---|---|
| RPC `conn_register`, `conn_request`, `conn_release`, `vehicle_arm`, `vehicle_disarm` | `conn/v1` `register`, `request`, `release`, `arm`, `disarm` | The interface chunk now carries the prefix (§3) |
| `.../@rpc/{procedure}/{responder}` | `.../@rpc/conn/v1/{procedure}/{responder}` | keelson's RPC key shape since 0.6.0-pre.1 |
| `SetpointMsg`, `ConnStateMsg`, `ManifestInfo` | `Setpoint`, `ConnState`, `Manifest` | A subject is the snake_case of its message (§2.2.2); the subject names do not change |
| `lat_deg`/`lon_deg` pairs | `keelson.Coordinate` | keelson's shared type for a referent position |
| `ConnRequest{claimant_id}` | the same, with field 2 reserved | For the signed attestation of carry-over C2 |

## What happens to each part of keelson's stack

**Adopted from coxswain — the overlapping part of keelson's stack is superseded.**
The PRs stay open for reference, labelled `superseded: coxswain`.

| Concern | coxswain (adopted) | Superseded in keelson |
|---|---|---|
| Who holds authority | One vessel-wide conn; strictly-greater priority preempts; synchronous grant | #331 roles-as-leases for the conn, per-domain fences, the two-phase transfer |
| Who arbitrates | The supervisor, onboard, in the control loop | #359 `authority_manager/v1` + `control_gate/v1`; the authority-manager and control-gate services |
| How control reaches the vessel | The holder's `setpoint` stream (idle, heading/speed, station-keep, path, direct effort) | #361 `ControlCandidate`/`GuidanceCandidate`/`ResolvedControl`, #382 hand-controller candidates, #386 latency provenance |
| Loss of the holder | Silence revokes; station-keep latched; a resumed stream does not restore | The lease lapse into per-mode transition states |
| Failsafes | The supervisor's matrix: critical voltage, position degraded, geofence, claimant lost | #363 `safe_state_policy` fault and timeout tables |
| Arming | `conn/v1 arm`/`disarm`, holder only, refused under a failsafe condition | #333 `Caller` and the motion fence on the vehicle interfaces |
| Per-vessel declaration | The signed manifest, its hash published on `manifest` | #363 the platform command policy and control matrix |
| Changing safety configuration | A new manifest is a re-commissioning | #377 `maintenance_state` |

**Carried over — keelson's work on top of the conn.** Labelled
`carry-over: coxswain`; each will be reworked onto `conn/v1`.

| | What | From | How it sits on the conn |
|---|---|---|---|
| C1 | Shore STOP and local lockout: `emergency_request` → `safety_state` | #332 | A new top row of the supervisor's failsafe matrix: a non-CLEAR `safety_state` stops the vessel whoever holds the conn. Already consumed by Slipway and crowsnest. |
| C2 | Claim attestation: a signed claim bound to the exact call | #383 | **Specified:** `ConnRequest.attestation` (field 2), the challenge on `ConnState.attestation_challenge`, `REFUSED_ATTESTATION`. The manager's role in #383 is the supervisor's; the trusted keys, and which claimants must attest, are in the manifest |
| C3 | Human command roles: overall and navigational command, navigation-function authority | #331 (role layer), #376, #381 | A policy *above* the conn: which person may operate which claimant. The supervisor's arbitration does not change |
| C4 | The engineer's propulsion veto, payload motion limits, payload control | #358, #360 | Limits applied in coxswain's guidance and allocation; payload control as its own interface |

**Not affected.** #282 `navigation_control/v1` is the navigation process's
front door and stays; under the conn the navigator is a claimant that streams
`setpoint`. The as-built `command_authority` lease protocol stays until crowsnest
and the navigator move to `conn/v1`.

## Refinements, 2026-11-12

After the adoption was reviewed against keelson's whole fleet, five
refinements were set (Ted Sjöblom). They narrow how the conn is used. They do
not change its arbitration.

**R1. coxswain is the primary vehicle stack; ArduPilot is a backend.** On a
coxswain hull the supervisor arbitrates in the control loop. An ArduPilot
vessel is reached through `mavlink2keelson`, which maps the same decisions onto
the autopilot. Its arbitration sits on the bus, not in the loop, so it does not
carry D-008's guarantee, and anything that shows the conn must not suggest it
does.

**R2. The autopilot holds heading and speed, and turns. Nothing more.** Route,
track, `go_to` and return-to-home belong to the onboard navigator
(`navigation_control/v1`, #282). The navigator is a claimant that streams
`HeadingSpeed`, and the navigator is vehicle-agnostic, so the route layer is
written once for the whole fleet. coxswain is asked for a turn order beside
`HeadingSpeed` (a rate of turn, deg/s; see coxswain item 8 below).
`FollowPath` stays on the wire for coxswain's own use, but keelson's navigator
does not depend on it.

**R3. The RC receiver is an independent emergency stop, not a claimant.** This
departs from coxswain D-025, whose sequencing had a kill channel first anyway.
A kill must stop the vessel whoever holds the conn and whether or not the
conn process is alive. So it lands in `safety_state` (C1), which acts at the
actuator nodes, and never takes the conn. A person aboard who must steer uses
the onboard physical override (R4). There is no remote takeover by RC.

**R4. No remote direct-actuator path.** Rudder angle, engine RPM and thrust
percentage are not set from shore. Direct steering is the onboard physical
override's job (a steering mode switch at the steering unit). The setpoint
vocabulary stays at guidance level, so `DirectEffort` is the lowest a claimant
goes. The per-device actuator axes of #327, #328 and #329 are not carried over
onto the conn.

**R5. Fallback is decided by the safety supervisor, never by a station.** All
safety monitoring that triggers a recovery lives in one onboard service, the
safety supervisor of C1. It extends C1 from STOP to the whole ladder `CONTINUE
→ HOLD → RETURN_HOME → STANDBY → STOP`. Its rules are condition, threshold,
hold time and action. It **decides** a recovery and **commands** an existing
recovery function. It never plans a path itself. This is run-time assurance
in ASTM F3269's sense (a simple monitor that switches to a verified recovery
function), and its shape follows PX4's link-loss failsafe: hold first, then
act. On a coxswain hull it commands through a manifest-declared **recovery
claimant**. That claimant ranks above every station and below nothing but the
kill, and streams `StationKeep` (HOLD) or `Idle` (STANDBY). For RETURN_HOME it
orders the navigator to `go_to` the mission's home. Return-home is armed only
when the mission defines a home (`SetHome`); otherwise the rule holds. The
policy is safety-relevant configuration and changes only under maintenance.
The protocol is `protocols/fallback.yaml`, on its own pull request stacked on
this one.

coxswain keeps its own in-loop reflexes, critical voltage and claimant lost,
because they must work with the bus gone. The safety supervisor's rules are
the slower, mission-level ones, and they act through the conn like any other
claimant.

## Improvements the adoption needs in coxswain

For the coxswain maintainer; numbered so they can be referred to.

1. **Speak `conn/v1`**: the interface/version RPC key shape, the procedure names
   above, the payload names, `keelson.Coordinate`, and the interface liveliness
   token keelson's RPC servers declare (§3.5).
2. **Configurable base path.** The keelson base path is hard-coded `"keelson"`.
3. **The RC kill switch only disarms while RC holds the conn**, because
   `disarm` is holder-only. A kill switch should disarm whoever holds the conn.
4. **Registry slots are never freed.** There is no deregistration, and the
   registry holds eight claimants.
5. **Nothing records why the conn changed.** The failsafe cause is not
   published, and `conn_state` is a 1 Hz snapshot, so a preemption or revoke
   between two samples is invisible. A change event would close both gaps.
6. **source_ids without a producer.** The fused and raw streams publish as
   `estimator` and `raw/{id}`; keelson's §2.1.1 puts the producer first
   (`coxswain/estimator`, as coxswain's own design notes planned).
7. **Carry-overs C1–C4** above, each as coxswain chooses to take it.
8. **A turn order** beside `HeadingSpeed`: a rate of turn in deg/s, keelson's
   unit for rate of turn (R2).
9. **A recovery claimant** in the manifest. It is the safety supervisor, it
   ranks above every station and below nothing but the kill, and it may
   preempt any holder (R5).
10. **The RC channel as a stop, not a claimant** (R3). A kill acts at the
    actuator nodes whoever holds the conn, which also closes item 3.

## Open questions

- **Key custody.** Who holds the manifest signing key, and the keys behind
  attestation (C2), how they rotate, and how many may sign. Open on both sides.
- **`vehicle_lifecycle/v1` arm/disarm.** keelson's vehicle interfaces also arm
  and disarm. On a vessel running coxswain, `conn/v1` is the authority and the
  vehicle interfaces are not served for actuation; whether they are deprecated
  more widely is undecided.
- **Downstream rework order.** crowsnest, the navigator and the safety
  supervisor move to `conn/v1` in later rounds; the authority-manager and
  control-gate services are not developed further.
