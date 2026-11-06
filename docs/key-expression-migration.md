# Key-expression migration: `{realm}/v0` → `{base_path}/@v0`

Keelson 0.4.0 changed the version chunk of every key from `v0` to the
verbatim chunk `@v0`. The legacy form has been **unsupported since 0.4.0**.
This page covers both forms, why the change was made, what happens when the two
meet on one bus, how to migrate a repository, and which RISE-Maritime
repositories still need to.

## The two forms

| | Legacy (≤ 0.3.x) | Current (≥ 0.4.0) |
|---|---|---|
| Pub/sub | `{realm}/v0/{entity_id}/pubsub/{subject}/{source_id}` | `{base_path}/@v0/{entity_id}/pubsub/{subject}/{source_id}` |
| RPC | `{realm}/v0/{entity_id}/rpc/{procedure}/{subject_in}/{subject_out}/{source_id}` | `{base_path}/@v0/{entity_id}/@rpc/{interface}/{version}/{procedure}/{source_id}` |
| First SDK argument | `realm` | `base_path` |

Release boundary:

| | Release | Date |
|---|---|---|
| Change merged | commit `3c3df09` | 2025-04-16 |
| Last release with the legacy form | `0.3.9-pre.22` (PyPI `0.3.9rc22`) | |
| First release with the current form | `0.4.0-pre.0` (PyPI `0.4.0rc0`) | 2025-04-30 |
| First stable release with the current form | `0.4.0` | 2025-05-02 |

The RPC key changed twice. 0.4.0 introduced `@rpc/{procedure}/{responder_id}`,
and `0.6.0-pre.1` (#128) added the `{interface}/{version}` chunks (see
[Protocol specification §3](protocol-specification.md#3-query-request-reply-messaging-remote-procedure-calls)). An SDK from 0.4.x or
0.5.x therefore speaks current pub/sub but not current RPC.

## Why

A chunk that starts with `@` is *verbatim*: it is matched only by an identical
chunk, never by `*` or `**`. Putting the major version in a verbatim chunk
seals each major version's key space, so a `v1` bus can later run next to `v0`
and no wildcard subscription will mix them by accident. See
[Protocol specification §1](protocol-specification.md#1-common-key-space-design).

## What happens when the two forms meet

They do not see each other. The table below was checked against a live zenoh
1.10 session by putting one sample on each form and subscribing with each
expression:

| Subscriber key expression | Receives `rise/v0/boat/pubsub/x/src` | Receives `rise/@v0/boat/pubsub/x/src` |
|---|---|---|
| `rise/v0/**` | yes | no |
| `rise/@v0/**` | no | yes |
| `rise/**` | yes | **no** |
| `rise/*/boat/**` | yes | **no** |
| `**` | yes | **no** |

What this means in practice:

- **A legacy publisher is invisible to every current consumer, and the reverse
  holds too.** Nothing raises an error and nothing arrives.
- **Catch-all subscribers miss all current traffic.** That covers recorders run
  with `-k rise/**` and router storages with `key_expr: rise/**`. The failure
  looks like an idle bus.
- **The SDK parsers reject legacy keys.** From 0.4.0 on, `parse_pubsub_key` and
  `parse_rpc_key` raise `ValueError` on a legacy key.
- **Recordings keep the keys they were made with.** An MCAP file recorded
  before 0.4.0 contains legacy keys only.

## Position

**The legacy form is unsupported since 0.4.0.** No SDK release from 0.4.0 on
constructs or parses it, and no bridge, dual-publishing mode or compatibility
shim will be added. A deployment that mixes the two forms is fixed by upgrading
its legacy side.

## Migrating a repository

1. **Pin a current SDK exactly.** That is `keelson` on PyPI or
   `@rise-maritime/keelson-js` on npm (the npm package was named `keelson-js`
   before 0.4.6). Pin the release the rest of the deployment runs. For RPC
   against this repository's connectors, that is `0.6.0-pre.1` or newer.
2. **Rename `realm` to `base_path`.** This applies to calls to
   `construct_pubsub_key` and `construct_rpc_key`. In Python, `realm=` as a
   keyword raises `TypeError` against a current SDK. Positional calls keep
   working.
3. **Replace hard-coded keys.** Look beyond source code: compose files, router
   storage `key_expr`, recorder `-k` arguments, Node-RED flows and environment
   variable defaults. Build keys with the SDK's `construct_*` functions rather
   than string literals.
4. **Port RPC endpoints.** The legacy
   `rpc/{procedure}/{subject_in}/{subject_out}` shape has no mechanical mapping.
   Register an interface in `messages/interfaces.yaml` and serve it with
   `keelson.scaffolding.serve_rpc`.
5. **Replace image tags.** Swap `ghcr.io/rise-maritime/keelson:0.3.x`, and the
   older `ghcr.io/mo-rise/keelson:*`, for a current tag.
6. **Verify on a live bus.** Subscribe to `{base_path}/@v0/**` and check that
   your keys arrive, then subscribe to `{base_path}/v0/**` and check that
   nothing does.

To find what is left, run from the repository root:

```bash
grep -rnE '/v0(/|\*|"|$)' --exclude-dir=.git --exclude-dir=node_modules .
grep -rnE 'keelson(-js)?"?\s*[=:<>~^]+\s*"?\^?0\.[0-3]\.|keelson:0\.[0-3]\.' \
    --exclude-dir=.git --exclude-dir=node_modules .
```

## Audit, 2026-11-06

**Scope.** Every non-archived repository in the RISE-Maritime organisation
other than keelson itself: 102 repositories, 3 of them empty. Each was
shallow-cloned at the head of its default branch and searched for:

- keelson SDK pins;
- `ghcr.io/rise-maritime/keelson` image tags;
- `/v0/` and `/@v0/` literals.

61 repositories mention keelson. Four of them neither pin it nor build keys:
`DRYDOCK`, `Keelson-processor-PointTracking`, `blueos-gateway` and
`keelson-connector-ardusimple`. The remaining 57 are classified below.

**Not checked:** running deployments, branches other than the default, and
repositories outside the organisation.

### Legacy: pinned to a pre-0.4 SDK (13)

Every repository here builds its keys through the SDK, so the pin decides the
form. Each needs the same work: pin a current SDK, rename `realm=` to
`base_path=`, and port any RPC (migration steps 1, 2 and 4). A repository
marked *superseded* has an in-tree successor and may be archived instead.

| Repository | Pin | Notes |
|---|---|---|
| `keelson-connector-camera-tapo` | `keelson==0.3.8-pre.3` | Serves RPC. |
| `keelson-connector-dlv-radar` | `keelson==0.3.9rc15` | The Dockerfile runs an unpinned `pip install keelson`, so an image built today gets a current SDK and `construct_pubsub_key(realm=…)` raises `TypeError` at startup. |
| `keelson-connector-gnss` | `keelson==0.3.8rc10` | Serves RPC. |
| `keelson-connector-lidar-sick` | `keelson==0.3.6` | Serves RPC. The README examples use legacy keys. |
| `keelson-connector-lidar-yd` | `keelson==0.3.1-pre.11` | |
| `keelson-connector-mavlink` | `keelson==0.3.1-pre.9` | Serves RPC. *Superseded* by `connectors/mavlink`. |
| `keelson-connector-pontus` | `keelson==0.3.8rc25` | |
| `keelson-processor-ais` | `keelson==0.3.9rc15` | |
| `keelson-processor-network-manager` | `keelson==0.3.7rc45` | Serves RPC. *Superseded* by `connectors/network_manager`. The legacy key is also hard-coded in `docker-compose.network-manager.yml`. |
| `keelson-processor-panorama` | `keelson==0.3.1-pre.20` | |
| `keelson-processor-pointcloud-tracking` | `keelson==0.3.1-pre.20` | Serves RPC. |
| `keelson-processor-radar-aptiv` | `keelson==0.3.7-pre.55` | |
| `crowsnest-frontend` | `keelson-js ^0.3.6-pre.20` | Also hard-codes `rise/v0/…` keys and `/rpc/mediamtx/whep`. *Superseded* by `crowsnest-dev`. |

### Legacy: hand-written keys in deployment configuration (4)

These do not go through an SDK at all. The legacy key is a literal in
configuration.

| Repository | Where | Needed change |
|---|---|---|
| `keelson-platforms` | `platforms/seahorse/sh-1` and `sh-2`: router storages, recorders, pre-0.4 images.<br>`platforms/landkrabb` and `platforms/usv`, `docker-compose.ais.yml`: porla pipelines put `rise/v0/…/raw/ais/…`.<br>`platforms/masslab/seastation/docker-compose.ais-digitrafic.yml`: puts `rise/v0/digitraffic/…`.<br>`platforms/manatee/docker-compose.zenoh-router.yml`: storage `rise/v0/boatswain/pubsub/**`.<br>`platforms/gota` and `platforms/purpose`: `0.1.0`/`0.3.x` images.<br>`platforms/case` and `platforms/landkrabb-small`: legacy keys in commented-out lines only.<br>`archive/` is left as is. | Rewrite the keys to `@v0` and bump the images, or retire the platform. |
| `platform-landkrabba` | `LANDKRABBA-ONE/docker-compose.{zenoh-router,nmea,nmea-to-keelson}.yml` | Rewrite the keys, or archive the repository. |
| `plattform-rc-model` | `purpose/docker-compose.{zenoh-router,rec-mcap}.yml`, with image `0.3.5-pre.4` | Rewrite the keys and bump the image, or archive the repository. |
| `keelson-connector-nodered` | `flows/example_keelson_flow.json:31` and `README.md:87` subscribe to `keelson/v0/**` | The example matches nothing a current SDK publishes. Change it to `keelson/@v0/**`. |

### Current, with stale legacy strings (9)

These publish and subscribe on `@v0`. The legacy strings are dead code,
defaults, documentation or recorded data.

| Repository | Where | Needed change |
|---|---|---|
| `crowsnest-dev` | `src/apps/wind-power/index.jsx:19` compares `msg.key` with `rise/v0/seaman/…`, which is never true.<br>`vite.config.ts:115` defaults `VITE_ZENOH_BASE_KEYEXP` to `rise/v0/masslab/**`.<br>The other hits are commented-out code or tests asserting that legacy keys are rejected. | Fix the wind-power key and the Vite default. |
| `chart-server` | `docs/dev-environment.md:181` documents the same `rise/v0/masslab/**` default | Update the doc. |
| `FASTAPI-GNSS` | `experimental/pub.py:54` defaults to `rise/v0/test/…` | Update the default. |
| `keelson-connector-anello`, `keelson-connector-kvh`, `keelson-connector-sbg` | The README's recording example runs `keelson:0.3.7-pre.51` with `-k rise/v0/ted/**` | Update the example. |
| `keelson-connector-anavs`, `keelson-connector-rutx` | `experimental/nmea.json` and the notebooks hold data recorded under legacy keys | None, since this is historical data. |
| `wiki` | `content/repositories.md:54` notes that older repositories use the legacy form | Link to this page. |

### Current (31)

These have a pin of 0.4.0 or newer, or only `@v0` keys in source:

- `bino`
- `foxglove-custom-panels`
- `ghost-drone`
- `keelson-connector-ais`
- `keelson-connector-blueos`
- `keelson-connector-camera`
- `keelson-connector-camera-luxonis`
- `keelson-connector-camera-realsense`
- `keelson-connector-gamepad`
- `keelson-connector-haddock`
- `keelson-connector-lidar-os`
- `keelson-connector-nmea`
- `keelson-connector-pc`
- `keelson-connector-radar-aptiv`
- `keelson-connector-safety-supervisor`
- `keelson-connector-tdk-imu`
- `keelson-processor-autopilot`
- `keelson-processor-control-authority`
- `keelson-processor-control-gate`
- `keelson-processor-eventmonitor`
- `keelson-processor-logic-pointcloud-vessel-extraction`
- `keelson-processor-navigation`
- `keelson-processor-navrisk`
- `keelson-sil-echosounder`
- `keelson-sil-gnss`
- `keelson-storage-reaper`
- `Logline`
- `porla-maritime`
- `porla-zenoh`
- `SLIPWAY`
- `the-warrant-paper`

Fourteen of them pin a 0.4.x or 0.5.x SDK, so their pub/sub is current but
their RPC key shape predates `0.6.0-pre.1`:

- `keelson-connector-ais`
- `keelson-connector-blueos`
- `keelson-connector-camera`
- `keelson-connector-camera-luxonis`
- `keelson-connector-camera-realsense`
- `keelson-connector-gamepad`
- `keelson-connector-haddock`
- `keelson-connector-lidar-os`
- `keelson-connector-nmea`
- `keelson-connector-pc`
- `keelson-connector-radar-aptiv`
- `keelson-processor-autopilot`
- `keelson-processor-eventmonitor`
- `porla-zenoh`

That only matters for those that use RPC.
