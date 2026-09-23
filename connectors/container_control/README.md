# container_control

Host container inspection and control over the Docker Engine API.
`docker2keelson` exposes one host's containers on the bus: list them, tail their
logs, start / stop / restart them, and remove them — and publishes their state,
their resource use and their logs for consoles and the MCAP recorder.

It serves the **`container_control/v1`** interface, so its keys are:

```
{realm}/@v0/{entity_id}/@rpc/container_control/v1/{procedure}/{source_id}
```

for example `rise/@v0/masslab/@rpc/container_control/v1/list/masslab-4`.

> **Read-only by default.** Mounting the Docker socket makes this process
> root-equivalent on its host. `start`, `stop` and `restart` refuse with
> `PERMISSION_DENIED` until it is started with `--allow-control` *and* an
> explicit `--allow` allow-list. See [Safety model](#safety-model).
>
> **`remove` is off even then.** It is the one irreversible verb here, so it has
> its own switch and its own allow-list: `--allow-remove GLOB`. Turning control
> on does not turn removal on, and upgrading a responder that has had control
> enabled for months does not either.

## Procedures

| Procedure | Request | Reply | Notes |
|---|---|---|---|
| `list` | `ListContainersRequest{running_only, name_glob}` | `ListContainersResponse{containers, observed_at, control_enabled}` | An empty payload is a valid "list everything". |
| `logs` | `GetLogsRequest{name, tail_lines, since, stream}` | `GetLogsResponse{name, id, lines, truncated, tail_lines}` | Lines are tagged stdout/stderr and timestamped. |
| `start` | `StartContainerRequest{name}` | `ContainerActionResponse{container}` | Idempotent. |
| `stop` | `StopContainerRequest{name, timeout_s}` | `ContainerActionResponse{container}` | Idempotent. |
| `restart` | `RestartContainerRequest{name, timeout_s}` | `ContainerActionResponse{container}` | |
| `remove` | `RemoveContainerRequest{name, force, remove_volumes}` | `RemoveContainerResponse{name, id, force_applied}` | Refuses a running container with `INVALID_STATE` unless `force`. |

The contract is [`interfaces/ContainerControl.proto`](../../interfaces/ContainerControl.proto), with the
domain types — `ContainerInfo` and the published messages — in
[`messages/payloads/ContainerHost.proto`](../../messages/payloads/ContainerHost.proto).
Two things about it are worth knowing before you write a client:

- **Containers are addressed by name, never by id.** The id changes on every
  `docker compose up --force-recreate`, so a client that round-trips an id from
  a `list` will get `NOT_FOUND` for a container plainly on its screen the moment
  the operator redeploys. `ContainerInfo` still returns the id, for display and
  for correlating with `docker ps`.
- **A refusal is a normal reply, not a fault.** Read
  `ListContainersResponse.control_enabled` and `ContainerInfo.controllable` and
  grey the button out, rather than discovering the refusal on the operator's
  click.

Every action reply carries the container's state *after* the operation, so a
client never has to follow an action with a `list` and race the runtime for the
answer.

## Safety model

The `/var/run/docker.sock` bind mount is the whole security story: anything that
can reach the Engine API through it can start a privileged container with `/`
bind-mounted. Treat this responder as a root shell on its host that happens to
speak protobuf.

The previous version of this interface exposed exactly that to any peer that
could reach one zenoh key, with no check of any kind. What replaces it:

1. **Read-only by default.** Without `--allow-control`, the mutating
   procedures refuse *before touching the socket* — so a refused call cannot
   even be used to probe which containers exist on the host.
2. **An explicit allow-list.** `--allow-control` requires at least one
   `--allow GLOB`, matched with `fnmatch` against the container **name**.
   Starting with control enabled and nothing allowed is rejected at startup
   rather than producing a responder that looks enabled and refuses everything.
   `--allow '*'` is how you say "everything", deliberately.
3. **Self-protection.** The responder refuses to stop, restart or remove its own
   container even when a glob matches it — that call would kill the responder
   mid-flight, and the caller would see a transport timeout rather than an
   answer.
4. **A second gate for `remove`.** `--allow-remove GLOB` is matched
   independently of `--allow`, and having none is what keeps the procedure off.
   There is no `--allow-remove` boolean to fall out of step with the list, which
   is the failure mode `--allow-control` needs its own startup check for.

### Why removal is not just another controlled verb

`start`, `stop` and `restart` are recoverable: the wrong click is undone by the
opposite click, and the container is still there while you work out which. A
removal is not, and nothing in this interface recreates a container — only the
deployment that defined it does.

That asymmetry rules out the smaller design, where `--allow-control` covers all
four. Two things break under it. **Upgrades:** every deployment already running
`--allow-control --allow '*'` — a posture real deployments run — would
acquire the power to delete anything on its host by pulling an image, with no
change to its configuration and nothing in its logs to say so. And **blast
radius:** the useful posture is control over everything and removal over almost
nothing, and a single flag cannot say it.

So the separation runs the whole way down rather than stopping at the flag:
`remove_enabled` beside `control_enabled` on the listing, `removable` beside
`controllable` per container. A client that greys its Remove button on
`controllable` is reading the wrong field and will offer an action that every
call refuses.

### What `remove` does and does not touch

- **A running container is refused**, with `INVALID_STATE` and a description
  naming the stop it needs. `force: true` kills it first, and is a separate
  decision in a separate call — the confirmation lives in the protocol rather
  than only in whichever UI is in front of the operator. `force_applied` on the
  reply says whether it was actually used, so "I tidied up an exited container"
  and "I took a running one down" are distinguishable after the fact.
- **Named volumes are never touched.** They outlive the container by definition
  and are shared with whatever else mounts them. `remove_volumes: true` reaches
  the *anonymous* ones only, and is off by default: reclaiming disk is a
  deliberate act, not a side effect of tidying a container list.
- **The reply is `RemoveContainerResponse`, not `ContainerActionResponse`.**
  Every other action returns the container's state *after* the operation; after
  a removal there is none to read, and a `ContainerInfo` describing a container
  that no longer exists is a falsehood a client would render as an ordinary row.

Refusals come back as `ErrorResponse.PERMISSION_DENIED` with a description
naming *which* rule fired, because the operator's next move differs in each case.

### Why the allow-list matches names, not labels

Docker labels are authoritative and rename-immune, and they were considered. But
they require editing the compose file of every container you want to manage —
and the point of this interface is managing containers you did not author. The
name is the handle `docker ps`, compose's `container_name:` and the operator all
already use. If name churn becomes a problem (compose's generated
`project-service-1` names), a label predicate is the v2 answer; do not smuggle
one in as a `label:key=value` string inside `--allow`.

### Do not put the zenoh router in the allow-list

You would be able to stop it exactly once. The bus you would need in order to
start it again goes down with it.

### Self-identification

Three strategies, tried in order; the one that won is logged at startup.

1. `--self-container-name` — set it to the same literal as your compose file's
   `container_name:`. This is the documented path.
2. `/proc/self/mountinfo` (then `/proc/self/cgroup`) — Docker bind-mounts
   `/etc/hostname` out of `/var/lib/docker/containers/<id>/`, so the full id is
   readable there.
3. The `keelson.container_control.self=1` label, accepted only if **exactly
   one** container carries it.

`$HOSTNAME` is deliberately *not* used: it is the short container id only when
Docker sets the container's hostname, and this interface runs with
`network_mode: host`, where the container inherits the **host's** hostname.

If `--allow-control` is set and none of the three resolves, the process exits
rather than run without self-protection.

### Hardening further

For a genuinely least-privilege deployment, put a socket proxy
(`tecnativa/docker-socket-proxy`) in front, configured to allow `GET
/containers/*` and deny `POST`. That is a second container on every platform, so
it is not the default here, but it is the right answer where the host matters.

## Running it

```yaml
services:
  container-control:
    image: ghcr.io/rise-maritime/keelson:<tag>
    container_name: container-control
    restart: unless-stopped
    init: true                      # forwards SIGTERM to PID 1 — see below
    network_mode: host
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock
    labels:
      keelson.container_control.self: "1"
    healthcheck:
      test: ["CMD", "container-control-healthcheck"]
      interval: 30s
      timeout: 10s
      start_period: 15s
      retries: 3
    command: >
      docker2keelson -r rise -e masslab -s masslab-4
      --mode peer --connect tcp/127.0.0.1:7447
      --self-container-name container-control
```

**The socket must be readable by the process.** Mount `/var/run/docker.sock`.
If you run the container as a non-root `user:`, that user is not in the host's
`docker` group and cannot open a `root:docker 0660` socket — add the group with
`group_add: ["<gid>"]`, where the gid is host-specific
(`getent group docker | cut -d: -f3`). If the socket cannot be opened the process
exits at startup and says so, rather than serving and answering `UNAVAILABLE` to
every call.

**On Docker Desktop, host networking joins the *Linux VM's* namespace, not the
Mac's**, so a router published on the Mac is not at `127.0.0.1` from inside the
container — it starts, serves nothing reachable, and looks healthy in its own
logs. Use bridge networking with `extra_hosts: ["host.docker.internal:host-gateway"]`
and `--mode client --connect tcp/host.docker.internal:7447` instead.

### "The console does not list this host"

Almost always the host is fine and the console did not *find* it. A console has
three ways to discover a `container_control` responder, and **two of them fail
silently**:

| Path | How it fails |
| --- | --- |
| An entry in the console's platform registry | Only knows what somebody typed. On Crowsnest's shipped registry exactly one platform declares `container_control` queryables — every other host depends on the paths below. |
| The RPC-interface liveliness token | **Invisible when it fails.** Nothing renders a token that did not arrive: no row, no error, no explanation. |
| The `container_status` subject | Visible: either snapshots arrive or they do not, and the same key carries the container list. |

The third is why `--publish-status` is **on by default** while the two
privileged flags are not — it is the only path whose failure an operator can
see. Turning it off leaves a host discoverable only by the two paths above, and
the process now logs a warning saying so.

One deployment sat invisible in Crowsnest's container panel for weeks while
publishing `container_status` every five seconds — running, reachable and
answering, with nothing on any screen to say why it was missing. The console
did not subscribe to the subject, and its registry named the entity
`landkrabban` while the responder published as `landkrabb`.

**Start with the log.** Since that episode the process prints every key it
answers or publishes on, at startup:

```
Serving container_control/v1 at rise/@v0/landkrabb/@rpc/container_control/v1/*/docker
Publishing container_status at rise/@v0/landkrabb/pubsub/container_status/docker
Publishing log_message at rise/@v0/landkrabb/pubsub/log_message/docker
```

so `docker logs <container>` answers "which entity and source is this actually
on?" without touching the bus. Compare those against what the console is looking
for; a mismatched `-e`/`-s` is the usual answer.

If they agree and the host still does not appear, check that the console
subscribes to `container_status` — a REST `GET` on the status key proves the
data is reaching the router, and a *stored* sample only proves it was published
once, so compare its timestamp against now.

### Healthcheck

`container-control-healthcheck` runs the same `ping()` the process runs at
startup, in an entry point that never imports zenoh. It exists for one failure
the startup gate cannot catch: a socket that goes away *after* boot (a daemon
restart, a changed docker gid, a revoked mount). In that state the process keeps
serving, every RPC returns `UNAVAILABLE`, and without the probe `docker ps` still
says the container is healthy. The shared keelson image carries no
`HEALTHCHECK` of its own, so declare it in compose as above.

```bash
docker inspect --format '{{json .State.Health}}' container-control
```

**This is visibility, not recovery.** `restart: unless-stopped` reacts to a
container *exiting*, not to it going unhealthy — nothing here auto-heals. That
is deliberate: restarting a responder does not fix a socket that is not there,
and keelson's protocol specification names container health checks as the
out-of-band visibility channel, not a control loop.

**`init: true` is not optional.** A process running as PID 1 with no signal
handler installed does not get the default action, so without it `docker stop`
waits out the full grace period and then SIGKILLs — leaving this responder's
liveliness tokens standing until their lease expires.

**Do not mount the socket `:ro` expecting safety.** The Engine API is
POST-over-HTTP on that socket, and a read-only inode does not make
`POST /containers/{id}/stop` fail. The real control is the process's own guard.

## Calling it

`container-control-cli` ships in the same image:

```bash
container-control-cli -r rise -e masslab -s masslab-4 \
  --mode client --connect tcp/127.0.0.1:7447 list

container-control-cli ... list --running-only --name-glob 'keelson-*'
container-control-cli ... logs --name keelson-router --tail 50 --stream stderr
container-control-cli ... restart --name mcap-recorder
container-control-cli ... stop --name mcap-recorder --timeout-s 5
```

From Python, through the SDK's interface registry:

```python
import keelson, zenoh
from keelson.interfaces import invoke_procedure
from keelson.interfaces.ContainerControl_pb2 import ListContainersRequest

with zenoh.open(zenoh.Config()) as session:
    response = invoke_procedure(
        session, "rise", "masslab", "container_control", "v1", "list", "masslab-4",
        request=ListContainersRequest(),
    )
    for container in response.containers:
        print(container.name, container.state)
```

## Capturing logs to MCAP

The `logs` procedure answers a question asked now. `--follow-logs` answers the
one asked later — it follows matching containers continuously and publishes each
line on keelson's well-known `log_message` subject as a `foxglove.Log`, so the
fleet's MCAP recorder captures it alongside everything else on the bus. That is
the only way anyone reads the logs of a container that has already died on a
machine they cannot reach.

```
--follow-logs 'keelson-*' --follow-logs 'mcap-*'
```

**Independent of `--allow-control` by design.** Recording a container's output
and being allowed to restart it are different decisions, and this responder is
read-only in every deployment — gating capture on the control allow-list would
have meant capturing nothing, everywhere.

**One channel per container.** The key carries the container name:

```
{realm}/@v0/{entity}/pubsub/log_message/{source_id}/{container}
```

The recorder makes one MCAP channel per full key, so each container is a
separate togglable channel in Foxglove rather than one merged firehose. Because
`log_message` is a well-known subject the recorder writes a real protobuf
schema (`foxglove.Log`), which is what Foxglove Studio's Log panel binds to — an
ad-hoc subject name would record as an undecodable blob instead.

| Flag | Default | Meaning |
|---|---|---|
| `--follow-logs GLOB` | — | Container name glob to follow; repeatable. Off entirely when unset. |
| `--follow-rescan-s` | 10 | How often to look for containers that have appeared or gone. |
| `--follow-max-lines-per-s` | 200 | Per-container ceiling; see below. |
| `--follow-queue-size` | 10000 | Lines buffered before the oldest are dropped. |
| `--follow-tail` | 0 | History replayed when a follow starts. 0 starts at the end. |

**Overload degrades; it does not take the responder down.** A crash-looping
container can emit megabytes a second. Above the rate cap lines are dropped and
a single `[keelson] dropped N lines` entry is published in their place, so the
gap is visible in the recording rather than silent. If the publish queue backs
up, the oldest entries go. Both are deliberate: this process's primary job is
answering `list` / `start` / `stop`, and losing that because one container is
noisy would be a self-inflicted outage. (The MCAP recorder makes the opposite
choice and exits when it cannot keep up — right for a recorder, wrong here.)

**Levels are sniffed, and it is a heuristic.** `foxglove.Log.level` drives the
Log panel's colouring, so lines are scanned for a standalone severity token near
the start (`ERROR`, `WARN`, `panic:`, …), defaulting to INFO. Deliberately *not*
stderr→ERROR: plenty of well-behaved programs write everything to stderr because
it is unbuffered, and a solid-red panel teaches the operator to ignore the
colour. The match is anchored on word boundaries so "no errors found" stays INFO.

**Volume is real.** Logs ride the `background` QoS profile (DATA_LOW, DROP), so
locally they yield to live data — but nothing downsamples text upstream the way
the router downsamples images. On a constrained link, narrow the glob.

## Deliberately not done

Three things a reviewer reasonably asks for, and why this responder does not do
them. Each is a decision, not an omission.

### It does not serve `configurable/v1`

Tempting — the allow-list looks like configuration. It would be a hole straight
through the guard.

`keelson.scaffolding.configurable`'s `set_config` handler is
`set_config_cb(json.loads(op.request_bytes))` and nothing else: no caller
identity, no authorisation, no validation beyond whatever the callback does.
Exposing the allow-list through it would let **any peer that can reach one zenoh
key** POST `{"control_enabled": true, "allow_globs": ["*"]}` and disable every
check in [Safety model](#safety-model) — on a process that is root-equivalent on
its host. The guard's whole value is that it is set at deploy time by whoever
wrote the compose file.

Three lesser reasons, any of which would still count:

- `make_configurable` declares a `configuration_json` publisher unconditionally,
  and does not declare the subject-level liveliness token that publishing a
  subject owes. (This responder does publish — `log_message`, when
  `--follow-logs` is set — and declares that token itself; the objection is
  that `make_configurable` would add a second published subject without one.)
- It republishes the whole config on every set, from a `finally:` — so a
  *rejected* attempt broadcasts the allow-list too.
- keelson's own guidance puts deployment-static mappings in version control and
  reserves RPC configuration for live-editable ones. An allow-list is the
  definition of deployment-static.

Adoption upstream is thin and consistent with that: four entry points across
three of ~21 connectors, every one a live-editable *watch set*, none a security
boundary.

### It does not publish `entity_health`

A connector publishing its own health bakes health *policy* into the connector,
and two emitters writing the same key race and flip-flop. keelson's dedicated
`entity_health` processor is the sanctioned aggregator.

The part that surprises people: **that aggregator cannot see this responder
either.** It subscribes to exact `(source, subject)` pub/sub pairs, and its
liveliness subscription deliberately cannot reach `@rpc` tokens — zenoh treats
`@`-prefixed chunks as verbatim, so no wildcard matches them. Its own source
comment says as much, and calls it intended.

**The health signal is the RPC interface liveliness token** that `serve_rpc`
declares: `{realm}/@v0/{entity}/@rpc/container_control/v1/*/{source}`. Present
means serving. Crowsnest already derives its "is this responder alive?" dot from
exactly that.

### It publishes container state

`container_status` → `keelson.ContainerHostStatus`,
on `{realm}/@v0/{entity}/pubsub/container_status/{source_id}` — **one key per
host**, carrying the complete container set, `observed_at` and
`control_enabled`. Exactly what `list` answers, published when it changes so a
console does not have to ask N hosts every fifteen seconds.

Controlled by `--publish-status` (**on by default**), `--status-interval-s`
(5.0) and `--status-heartbeat-s` (30.0). On by default unlike `--allow-control`
and `--follow-logs`, and that is consistent rather than a departure: those two
are off because they are *privileged* — one mutates the host, the other
republishes container stdout. This publishes precisely the bytes `list` already
hands to any bus participant who asks.

**Change plus heartbeat, not every tick.** An unchanged tick stays silent, which
is the entire saving. But zenoh pub/sub does not backfill, so a subscriber that
joins during a quiet period would otherwise wait indefinitely for its first
value; the heartbeat bounds that wait rather than removing it, which is why a
consumer should still prime itself with one `list` call on arrival.

**One key per host, never per container** — unlike logs, which go the other way
so Foxglove gets one channel per container to toggle. A per-container state key
cannot express *removal*: a deleted container simply stops publishing, which is
indistinguishable from a quiet one, a wedged publisher and a dead host. A
whole-host snapshot expresses removal by omission, and "that container is gone"
is exactly what an operator needs to see.

**The recorder needs nothing extra.** Both subjects are registered in keelson's
`messages/subjects.yaml`, so `keelson2mcap` writes a real protobuf schema for
them like any other well-known subject.

It is *not* a sixth procedure — see below.

### Deployment detail — on request, RPC only, no environment

A `list` request with `include_deployment_detail: true` fills three
`ContainerInfo` fields describing how each container is *wired*: `ports`,
`networks` and `mounts` (fields 18–20). That covers the usual "why can't
container A reach container B" question without an SSH session and
`docker inspect`.

- **RPC only.** `container_status` never carries these fields. Pubsub is
  recorded by `keelson2mcap` and fanned out to every subscriber; an RPC reply
  goes only to the caller. Static config has no place on a status heartbeat anyway.
- **Opt-in per request.** A routine `list` poll stays lean.
- **No environment, command or entrypoint — not even variable names.** That is
  where tokens, passwords and connection strings live, and the bus has no
  per-key authorization. Even names show which credentials exist. Use
  authenticated host access (`docker inspect` over SSH) for those.
- **Empty lists are answers.** No published ports, host networking, no mounts —
  all legitimate. They are also what a responder predating these fields
  sends; the responder version settles which.

### It publishes container **resource** stats, opt-in

`container_stats` → `keelson.ContainerHostStats`,
on `{realm}/@v0/{entity}/pubsub/container_stats/{source_id}` — **one key per
host**, carrying one `ContainerResourceUsage` per sampled container: CPU
percentage and core count, memory working set and percentage, network and block
I/O totals with their per-second rates, PID count, the configured allocation
(`--cpus`, `--cpu-shares`, `--cpuset-cpus`, `--memory`), and CFS throttling.

Controlled by `--publish-stats GLOB` (**off entirely when unset**, repeatable)
and `--stats-interval-s` (10.0).

**Off by default, unlike `--publish-status`, and the difference is the point.**
That one republishes precisely the bytes `list` already hands to any bus
participant who asks, only on time. This is *new* continuous telemetry: every
tick is a sample by definition, so it is a fixed-rate load on a link that also
carries navigation data. An operator who can reach netdata or portainer on the
host should leave it off.

**What it buys where those cannot reach.** netdata and portainer are still
better at this, on a machine you can open a browser to. What they cannot do is
put utilisation *in the MCAP recording*, on the same clock as the vessel data —
so "the connector dropped out at 14:03" can be laid next to "that container was
CFS-throttled at 14:03", after the fact, on a host nobody can reach any more.
That is the case this is for, and the reason it is opt-in rather than absent.

**Every tick publishes; there is no change detection.** The one place this
departs from `container_status`, and not an oversight. Container state is a step
function, so suppressing repeats there is what lets a subscriber trust that a
message means something moved. Utilisation changes on every sample by
definition: a series with its repeats suppressed is a series with holes in it,
and a consumer could not tell a quiet container from a stalled publisher.

**Unset is not zero.** Everything derived from a *pair* of readings — the CPU
percentage and all four byte rates — is absent, not zero, when there is no
usable predecessor. Three ordinary cases produce that:

- the **first sample** for a container, which has nothing to difference against;
- a **restart**, which zeroes the cgroup counters; one tick unset, then it
  resumes. (Keyed by container id, so a `--force-recreate` — same name, fresh
  counters — is the same story rather than a spike that never happened.)
- a **host-networked container**, whose four network fields are absent
  permanently: it has no interface of its own, and the Engine omits the
  counters rather than zeroing them. Reporting 0 would claim it moves no
  traffic, which is the opposite of true.

A dashboard cannot tell 0.0 from "no reading" — it draws a flat line through the
gap and an alert rule reads it as healthy — which is why every such field is
`optional` in the proto and why `tests/test_proto_contract.py` pins that
presence field by field.

**The rule survives rendering and then dies in arithmetic.** Rendering absence
as a dash is the easy half; the half that gets missed is that an absent reading
must never reach a comparator, an average, a threshold or a sum. In a language
where the absent value coerces — JavaScript's `null - 5` is `-5`, `null - null`
is `0` — a naive sort promotes "never measured" to the top of an ascending CPU
column, where it reads as *lowest*. No decoder test catches that: the decode was
correct and the presence was intact right up until something did sums with it.
The crowsnest implementation ranks absent rows out before comparison so they
sort last in **both** directions, while a *measured* zero sorts as the number it
is — and pins that in its own checks. If you consume this subject, the question
to ask of every numeric path is not "do I handle null?" but "does null get to
pretend it is a number here?".

**Running containers only.** A stopped container's stats call still answers
`200`, with an all-empty body; published naively that is a row reading "0% CPU,
0 bytes" for something that is not running at all. It is omitted instead, and
`container_status` — which carries the complete set including stopped ones — is
where absence means *gone*.

**Configured limits come from the container's configuration, not the counters.**
The Engine reports the host's total memory as the `limit` of an unconstrained
container, so `memory_limit_bytes` is set only when a limit was actually
configured. `memory_used_pct` still divides by the reported limit either way,
which is the number `docker stats` prints in its MEM% column.

That last part has a consequence a consumer must handle: **`memory_used_pct` is
of the limit when there is one and of host total when there is not**, so what it
is a percentage *of* depends on whether `memory_limit_bytes` is present. Label
the two cases differently ("of limit" / "of host") or a fleet table shows two
incomparable percentages in one column. It is kept because dropping it would
lose the docker-comparable figure for the unconstrained containers that are the
overwhelming majority — but it is the one place here where one field's presence
changes another's meaning, which the throttling counters are `optional`
specifically to avoid.

**One sweep, one thread, one-shot samples.** `stats(stream=False,
one_shot=True)`: without `one_shot` the daemon sleeps a full sampling cycle
before answering — about a second *per container* — which is why `docker stats
--no-stream` over eight containers takes two seconds. One-shot reads the cgroup
files and returns; eight containers sweep in about a tenth of a second. The
price is that `precpu_stats` comes back zeroed, which is why the differencing is
done here. The publisher warns if a sweep exceeds half the interval.

**It is not a sixth procedure**, for the reason `container_status` is not:
continuous streams are pub/sub, not RPC, and adding a procedure to a published
interface version is a breaking change requiring `v2` — a consumer cannot tell
"this implementor predates the method" from "this implementor is unreachable",
because zenoh returns no reply for both. Adding a message changes no existing
message's wire format, and no `rpc` could express a continuous stream anyway.

Procedures are not free: a consumer cannot tell "this implementor predates the
method" from "this implementor is unreachable", because zenoh returns no reply
for either. `container_control/v1` is registered in keelson's
`messages/interfaces.yaml`, so its procedure list is fixed and the next
procedure costs a `v2`.

**Decoding it in JavaScript: do not "fix" the defaults option.** protobufjs
compiles a proto3 `optional` field to a synthetic oneof, and `toObject`'s
`defaults: true` does not populate oneof members — so presence survives
`defaults: true` intact, and the defensive-looking move of setting
`defaults: false` because "this message is mostly optional" is actively wrong.
It changes nothing for the optional fields and turns the five non-optional ones
into `undefined` whenever they hold their proto3 default, where `0` resident
bytes and an empty `cpuset_cpus` are both real readings. Verified against live
payloads by the crowsnest side. (`ts-proto` differs from protobufjs on
timestamps — a `Date` rather than `{seconds, nanos}` — but that is a rendering
detail; this one silently loses data.)

**Host-level totals are not here, deliberately.** Summing the containers does
not give you the host: non-containerised processes are outside the sum, so "is
this host oversubscribed?" is not answerable from this subject and is not meant
to be. Those figures come from a host telemetry agent — the [`pc`](../pc/README.md)
connector publishes `cpu_load_pct`, `memory_used_pct` and the rest of keelson's
compute host telemetry block. Duplicating them into a container message is how two
sources of truth start.

## `docker2keelson`

```
usage: docker2keelson [-h] [--log-level LOG_LEVEL] [--mode {peer,client}] [--connect CONNECT]
                      [--listen LISTEN] [--zenoh-config ZENOH_CONFIG] -r REALM -e ENTITY_ID
                      -s SOURCE_ID [--allow-control] [--allow GLOB] [--allow-remove GLOB]
                      [--self-container-name SELF_CONTAINER_NAME]
                      [--publish-status | --no-publish-status]
                      [--status-interval-s STATUS_INTERVAL_S]
                      [--status-heartbeat-s STATUS_HEARTBEAT_S] [--publish-stats GLOB]
                      [--stats-interval-s STATS_INTERVAL_S] [--follow-logs GLOB]
                      [--follow-rescan-s FOLLOW_RESCAN_S]
                      [--follow-max-lines-per-s FOLLOW_MAX_LINES_PER_S]
                      [--follow-queue-size FOLLOW_QUEUE_SIZE] [--follow-tail FOLLOW_TAIL]
                      [--stop-timeout-s STOP_TIMEOUT_S] [--default-tail-lines DEFAULT_TAIL_LINES]
                      [--max-tail-lines MAX_TAIL_LINES] [--max-log-bytes MAX_LOG_BYTES]

Keelson RPC responder exposing this host's containers as container_control/v1. Serves six
procedures -- list, logs, start, stop, restart, remove -- on the keelson RPC key space:
{realm}/@v0/{entity_id}/@rpc/container_control/v1/{procedure}/{source_id} READ-ONLY BY DEFAULT.
Mounting the Docker socket makes this process root-equivalent on its host, so the mutating
procedures refuse with PERMISSION_DENIED until it is started with --allow-control and an explicit
--allow allow-list. REMOVE IS OFF EVEN THEN. --allow-control covers the reversible verbs only;
`remove` needs --allow-remove GLOB on top of it. Upgrading a responder that has had control
enabled for months must not hand it the power to delete containers because a new image happened to
grow the procedure.

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
  -r, --realm REALM     Keelson base path. (default: None)
  -e, --entity-id ENTITY_ID
  -s, --source-id SOURCE_ID
                        Responder id. (default: None)

container control (off by default):
  --allow-control       Permit start/stop/restart. Requires at least one --allow. Without it this
                        responder answers list and logs only. Does NOT enable remove -- see
                        --allow-remove. (default: False)
  --allow GLOB          Container NAME glob that may be controlled; repeatable. Use '*' to mean
                        every container, deliberately. (default: [])
  --allow-remove GLOB   Container NAME glob that may be REMOVED; repeatable. Enabling this at all
                        is what enables the remove procedure -- there is no separate boolean to
                        get out of step with it. Requires --allow-control, and is matched
                        independently of --allow, so 'restart anything, delete only the scratch
                        containers' is sayable. Removal is the one action here no other call can
                        undo. (default: [])
  --self-container-name SELF_CONTAINER_NAME
                        This responder's own container_name. Set it to the same literal as your
                        compose file's container_name: so it can refuse to stop itself. (default:
                        None)

container status (on by default):
  --publish-status, --no-publish-status
                        Publish the container set as the 'container_status' subject whenever it
                        changes, so consoles subscribe instead of polling list() per host. ON BY
                        DEFAULT, unlike --allow-control and --follow-logs: those are off because
                        they are privileged (one mutates the host, the other republishes container
                        stdout). This publishes precisely the bytes list() already hands to any
                        bus participant who asks -- the same answer, on time, not a wider one.
                        (default: True)
  --status-interval-s STATUS_INTERVAL_S
                        How often the container set is snapshotted and compared. This is the
                        worst-case latency of a state change reaching a console, and it must beat
                        the poll it replaces to be an improvement. (default: 5.0)
  --status-heartbeat-s STATUS_HEARTBEAT_S
                        Republish unchanged state at most this often. Zenoh pub/sub does not
                        backfill, so this bounds how stale a late subscriber's first value is.
                        (default: 30.0)

resource stats (off by default):
  --publish-stats GLOB  Publish per-container CPU, memory, network, block I/O and CFS throttling
                        as the 'container_stats' subject, for container names matching this glob;
                        repeatable. Off entirely when unset. OFF BY DEFAULT, unlike --publish-
                        status: that one republishes bytes list() already hands to any bus
                        participant who asks, while this is new continuous telemetry -- every tick
                        is a sample, by definition -- on a link that also carries navigation data.
                        (default: [])
  --stats-interval-s STATS_INTERVAL_S
                        How often every matching running container is sampled, and the window
                        every rate on the wire is averaged over. Longer than --status-interval-s
                        on purpose: a state change is news, a utilisation sample is a series.
                        (default: 10.0)

log capture (off by default):
  --follow-logs GLOB    Follow the logs of containers whose NAME matches, publishing them as the
                        'log_message' subject so the fleet's MCAP recorder captures them.
                        Repeatable. Deliberately independent of --allow-control: recording a
                        container's output is a different decision from being allowed to restart
                        it, and a read-only responder must still be able to capture. (default: [])
  --follow-rescan-s FOLLOW_RESCAN_S
  --follow-max-lines-per-s FOLLOW_MAX_LINES_PER_S
                        Per-container ceiling. Excess is dropped and reported in-band. (default:
                        200)
  --follow-queue-size FOLLOW_QUEUE_SIZE
  --follow-tail FOLLOW_TAIL
                        Lines of history to replay when a follow starts. 0 starts at the end.
                        (default: 0)

limits:
  --stop-timeout-s STOP_TIMEOUT_S
  --default-tail-lines DEFAULT_TAIL_LINES
  --max-tail-lines MAX_TAIL_LINES
  --max-log-bytes MAX_LOG_BYTES
```

## `container-control-cli`

```
usage: container-control-cli [-h] [--log-level LOG_LEVEL] [--mode {peer,client}]
                             [--connect CONNECT] [--listen LISTEN] [--zenoh-config ZENOH_CONFIG]
                             -r REALM -e ENTITY_ID -s SOURCE_ID [--timeout TIMEOUT]
                             {list,logs,start,stop,restart,remove} ...

Command-line client for container_control/v1 -- verification and ops. Lists containers, tails logs
and starts, stops, restarts or removes one, against a docker2keelson responder.

positional arguments:
  {list,logs,start,stop,restart,remove}
    list                List containers on the responder's host.
    logs                Tail one container's logs.
    start               Start one container.
    stop                Stop one container.
    restart             Restart one container.
    remove              Delete one container. Not undoable by any other procedure here.

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
  -r, --realm REALM
  -e, --entity-id ENTITY_ID
  -s, --source-id SOURCE_ID
  --timeout TIMEOUT
```

## `container-control-healthcheck`

```
usage: container-control-healthcheck [-h] [--timeout-s TIMEOUT_S]

Exit 0 if the Docker socket answers, 1 with the reason on stderr if not. For a compose healthcheck
on a docker2keelson container.

options:
  -h, --help            show this help message and exit
  --timeout-s TIMEOUT_S
                        Client-side budget for the ping (default: 5).
```
