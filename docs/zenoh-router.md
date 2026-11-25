# Running a Zenoh router for keelson

keelson is a set of conventions on top of [Zenoh](https://zenoh.io). Every
deployment needs at least one Zenoh **router**: the process that connects the
connectors, processors and user interfaces to each other. This page sets one
up from scratch and explains what each part is for.

## Do you need a router?

Two Zenoh sessions on the same network segment can find each other as
**peers**, with no router involved (`--mode peer`, the SDK's default for
examples in [How to use](how-to-use.md)). You need a router as soon as any of
these is true:

- the participants are not on one LAN, or are in containers whose multicast
  scouting does not reach each other;
- a **browser** joins the bus (Crowsnest, a simulator's control station): a
  browser can only open a WebSocket, which a router plugin provides;
- some subjects must be **stored**, so that a late joiner can ask for the
  latest value instead of waiting for the next one.

Every keelson connector takes the SDK's common connection flags
(`keelson.scaffolding.add_common_arguments`): to join through a router, run it
with `--mode client --connect tcp/<router host>:7447`.

## Start one

The official image runs `zenohd`:

```bash
docker run -d --name zenoh-router \
  -p 7447:7447 -p 8000:8000 \
  eclipse/zenoh:<version> \
  --rest-http-port=8000
```

- **`<version>`** — use the Zenoh version keelson itself uses: the
  `eclipse-zenoh` version in keelson's `uv.lock`, which is also the one in the
  connector image. Zenoh sessions of different minor versions are not
  guaranteed to talk to each other, and the plugins below must match the router
  exactly.
- **7447** is the port clients connect to. **8000** is Zenoh's REST API, handy
  for checking the router (below).
- To run `zenohd` without Docker, see Zenoh's
  [installation guide](https://zenoh.io/docs/getting-started/installation/).

## Connect to it

| From | Endpoint |
|---|---|
| the same host | `tcp/localhost:7447` |
| another container on the same Docker host | `tcp/host.docker.internal:7447` (Docker Desktop; on Linux add `--add-host=host.docker.internal:host-gateway`), or the router's service name in the same compose project |
| another machine | `tcp/<router host or IP>:7447` |

If your organisation already runs a shared router, connect to that instead of
starting your own.

## The base path is yours to choose

The router does not know about keelson's key layout. The **base path** — the
first chunk of every key, `{base_path}/@v0/...` (protocol specification §2) —
is chosen per deployment and given to every connector (`--realm`). Pick one
name for a deployment and use it everywhere: two connectors on different base
paths share a router but never see each other.

## Browsers: the WebSocket plugin

A browser reaches Zenoh through the **`remote_api`** plugin, a WebSocket that
the [zenoh-ts](https://github.com/eclipse-zenoh/zenoh-ts) JavaScript client
connects to. It is not in the image; you add it:

1. Download the plugin from the
   [zenoh-ts releases](https://github.com/eclipse-zenoh/zenoh-ts/releases) for
   **exactly** the router's Zenoh version, and for the Docker host's
   architecture (`x86_64` or `aarch64`).
2. The `eclipse/zenoh` image is Alpine (musl): take the **`-musl`** build. A
   `-gnu` build fails to load.
3. Put `libzenoh_plugin_remote_api.so` in a directory mounted at
   `/root/.zenoh/lib`, and enable it:

```bash
docker run -d --name zenoh-router \
  -p 7447:7447 -p 8000:8000 -p 10000:10000 \
  -v "$PWD/zenoh_plugins:/root/.zenoh" \
  eclipse/zenoh:<version> \
  --rest-http-port=8000 \
  --cfg='plugins/remote_api/websocket_port:"0.0.0.0:10000"'
```

A browser client then connects to `ws://<router host>:10000`.

> **A mismatched plugin fails quietly.** Zenoh refuses a plugin built for a
> different version, logs it once, and carries on: TCP clients and REST keep
> working while every browser sees an empty bus. After changing the router's
> version, replace the plugin too, and check the log (below).

## Stored subjects: the storage plugin

Some subjects are meant to be stored: `messages/subject_keys.yaml` and each
protocol's key rows say which, with `storage: latest`. A router stores them
through its `storage_manager` plugin: one **storage** per key expression,
kept in a **volume**.

- Use a **persistent** volume (the `rocksdb` backend) for anything that
  must survive a restart. The `memory` volume loses everything on restart,
  and does not answer wildcard queries — a `get` on `.../checklist_state/*`
  returns nothing while a `get` on one exact key works (protocol specification
  §7.3).
- The rocksdb backend is a plugin too, with the same rule: built for exactly
  the router's version, `-musl` for this image. Its releases are at
  [zenoh-backend-rocksdb](https://github.com/eclipse-zenoh/zenoh-backend-rocksdb/releases).

A storage covers one subject across every entity of one base path. Added to the
`docker run` above (a fragment, not a whole command):

```bash
  -v "$PWD/zenoh_backends:/zenoh_backends" \
  -v "$PWD/zenoh_data:/zenoh_data" \
  -e ZENOH_BACKEND_ROCKSDB_ROOT=/zenoh_data \
  --cfg='plugins/storage_manager/backend_search_dirs:["/zenoh_backends"]' \
  --cfg='plugins/storage_manager/volumes/rocksdb:{}' \
  --cfg='plugins/storage_manager/storages/route/volume:{"id":"rocksdb","dir":"route","create_db":true}' \
  --cfg='plugins/storage_manager/storages/route/key_expr:"<base_path>/@v0/*/pubsub/route/**"'
```

A storage whose key expression names the wrong base path stores nothing and
says so nowhere: check it against a key a connector actually publishes.

## Check it

- **REST:** `curl http://localhost:8000/@/local/router` answers with the
  router's identity, and `curl 'http://localhost:8000/<base_path>/@v0/**'`
  with whatever is stored.
- **Plugins:** `docker logs zenoh-router | grep -i plugin` should show
  `remote_api` and `storage_manager` started, and no `compatibility mismatch`.
- **The bus:** [`zenoh-cli`](https://pypi.org/project/zenoh-cli) with
  keelson's plugin (see [How to use](how-to-use.md)) subscribes and decodes
  keelson subjects.

## A compose file to start from

```yaml
services:
  zenoh-router:
    image: eclipse/zenoh:<version>   # = eclipse-zenoh in keelson's uv.lock
    restart: unless-stopped
    ports:
      - "7447:7447"    # clients
      - "8000:8000"    # REST
      - "10000:10000"  # WebSocket, for browsers
    environment:
      - RUST_LOG=info
      - ZENOH_BACKEND_ROCKSDB_ROOT=/zenoh_data
    volumes:
      - ./zenoh_plugins:/root/.zenoh   # lib/libzenoh_plugin_remote_api.so
      - ./zenoh_backends:/zenoh_backends   # libzenoh_backend_rocksdb.so
      - ./zenoh_data:/zenoh_data
    # A folded scalar: no comments inside it, or they become arguments.
    command: >-
      --listen tcp/[::]:7447
      --cfg='mode:"router"'
      --rest-http-port=8000
      --cfg='plugins/remote_api/websocket_port:"0.0.0.0:10000"'
      --cfg='plugins/storage_manager/backend_search_dirs:["/zenoh_backends"]'
      --cfg='plugins/storage_manager/volumes/rocksdb:{}'
```

Add one pair of `storages/<name>/volume` and `storages/<name>/key_expr` lines
per stored subject. To link routers at several sites, add
`--connect tcp/<other router>:7447`; Zenoh's
[deployment documentation](https://zenoh.io/docs/getting-started/deployment/)
covers TLS and access control.
