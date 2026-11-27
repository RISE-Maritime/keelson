# 0003 — The watch is the lease: the OOW holds it, the master is always defined, and Nav conn is a task

**Status:** proposed · **Date:** 2026-11-27 ·
**Protocol:** [`command_authority`](../../protocols/command_authority.yaml) ·
**Supersedes (if merged first):** 0001 — command authority follows coxswain's conn design (#399)

## Context

Two designs answer *who may command a vessel, and what happens when they stop*:

- **The `command_authority` lease.**
  - On `dev`, plus three PRs: #331 (roles, assignments, fences), #466 (`conn_event`, decline on
    the wire, and a ranked forced takeover that alarms the displaced holder), and #457
    (`conn_offer`).
  - Every consumer already speaks it: Crowsnest, the navigation process
    (keelson-processor-navigation) and the Slipway simulator.
- **conn/v1** (#399, with #400–#403).
  - coxswain's onboard supervisor as the only arbiter, with integer priorities in a signed
    manifest, and the setpoint stream as the heartbeat.
  - coxswain's author has said, on #399, that coxswain "keeps its conn semantics internal… will
    not own a competing wire protocol", and that its adapter "translates representation, never
    authority".

#282 (`navigation_control/v1`) was deferred until this question is settled. With it, the lease
chain stacked on it waits too: #331, #332 (`emergency_request`), #358, #376 and #381.

Measured against the operating rules of the ROC that uses keelson (Slipway and Crowsnest):

| Requirement | Lease | conn/v1 |
| --- | --- | --- |
| An emergency stop is anyone's, owned by the onboard safety service, and cuts power to everything that moves the vessel, steering gear included | Ownership fits (#332). The steering gear has to be named | A shore stop is "a higher-priority claimant streaming `Idle`" (`conn.yaml` at #399): the power stays on |
| The OOW and the master may order a crash stop, standby, or a pause or stop of the mission without holding the conn | Needs a policy change. The lease is advisory | No path: only the holder's setpoints drive |
| A watch handover moves the people's roles; who drives is a separate task | Fits: assignments are separate from the lease (#331) | Roles live only inside signed claims (#402) |
| Every holder shows its kind (human or automation), who it is, and what it acts through | A small change: `controller_id`, site and role exist | The holder is a bare `uint32` |
| A live holder is displaced only by an explicit forced takeover, which alarms them | Fits (#466) | Pre-emption is silent and immediate |

## Decision

1. **keelson standardises the `command_authority` lease,** extended by #331, #466 and #457. conn/v1
   (#399–#403) is withdrawn as keelson's wire protocol. coxswain keeps its onboard arbitration, and
   maps onto the lease through its adapter.
2. **The lease is the watch.**
   - It is renamed from "the conn" to the **OOW's watch**: the officer of the watch holds it.
   - It is held by one person at a station.
   - It moves by take, request and grant, release, lapse, or a watch handover.
3. **The master is defined for every vessel at all times,** as the legal definition of who is
   responsible for her.
   - The master may override the OOW at any time, by a forced takeover of the watch.
   - The master's takeover does not wait the 30 s that a ranked takeover waits.
   - The displaced OOW is alarmed, and the takeover is recorded as a `conn_event`.
4. **Who drives the vessel ("Nav conn") is a task, not the lease.**
   - It is allocated to a human, through a hand controller or orders, or to automation (the
     navigation process), through its interface.
   - The OOW decides the allocation. The navigation process holds Nav conn whenever no human does.
   - The record says the holder's kind (human or automation), the person or the microservice, and
     the interface or controller it acts through.
5. **The emergency stop stays with the onboard safety supervisor** (`emergency_request`, #332).
   - Anyone may trigger it.
   - It removes power from everything that moves the vessel, steering gear included; the rudder
     holds its angle.
   - The holder of Nav conn, the OOW and the master may also order a crash stop, standby, or a
     pause or stop of the mission.

## Follow-ups

Each of these is a change in its own PR, not in this record.

- **Rename conn to watch / OOW:**
  - #331 `ROLE_CONN`;
  - #466 `conn_event`;
  - #457 `conn_offer`;
  - #463 `ROLE_NAVIGATIONAL_COMMAND_AND_CONN`, which becomes a handover of the master or the OOW.
- **Add a holder kind and interface to the lease record** (#466).
- **The OOW's and the master's standing over stops:** #282's order guard, #321 (`crash_stop`), and
  #331 / #466's role record.
- **The steering gear in the stop's required outcome:** #332, `VehicleLifecycle`.
- **Rebase the fallback ladder onto the lease:** #430, #436.
- **Lift #282's deferral,** and merge the lease chain bottom-up.
- **Close #399–#403 with thanks.** Their attestation (#400) can return as a lease-level proposal
  (#383).
