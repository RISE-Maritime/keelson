# pc

Publishes the health of the computer Keelson runs on — the logging PC, the
onboard NUC, the ROV topside — using [psutil](https://github.com/giampaolo/psutil).
Runs on Linux, macOS and Windows.

**Scope is deliberately narrow:** only what a vessel-level consumer needs to
make a decision — is the box healthy, how long can it keep recording. Per-core
load, per-process usage, interface counters, load averages and fans are the job
of node-exporter or an equivalent running beside Keelson, not of the bus.

Metrics a platform does not expose are not published rather than reported as
zero. CPU temperature, for example, is only available on Linux.

## `pc2keelson`

```
usage: pc2keelson [-h] [--log-level LOG_LEVEL] [--mode {peer,client}] [--connect CONNECT]
                  [--listen LISTEN] [--zenoh-config ZENOH_CONFIG] -r REALM -e ENTITY_ID
                  [-s SOURCE_ID] [--interval INTERVAL] [--info-interval INFO_INTERVAL]
                  [--no-host-info] [--no-cpu] [--no-memory] [--no-disk] [--no-network]
                  [--no-sensors] [--disk-mountpoint PATH] [--disk-fstype-exclude FSTYPES]
                  [--nic-exclude NICS] [--procfs-path PATH] [--host-root PATH]

Monitor this computer and publish its health to Keelson/Zenoh

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
  -r, --realm REALM     Realm/base path to publish under, ex. rise (default: None)
  -e, --entity-id ENTITY_ID
                        Unique id of the entity within the realm, ex. nuc01 (default: None)
  -s, --source-id SOURCE_ID
                        Source-id base; per-instance suffixes are appended to it, ex. pc/disk/data
                        (default: pc)
  --interval INTERVAL   Seconds between samples of the live metrics (default: 5.0)
  --info-interval INFO_INTERVAL
                        Seconds between publishes of host identity (default: 60.0)
  --no-host-info        Do not publish host name or boot time (default: True)
  --no-cpu              Do not publish CPU load (default: True)
  --no-memory           Do not publish memory or swap usage (default: True)
  --no-disk             Do not publish disk usage (default: True)
  --no-network          Do not publish network interface state (default: True)
  --no-sensors          Do not publish CPU temperature (default: True)
  --disk-mountpoint PATH
                        Report only this mountpoint; repeatable. Default is every real filesystem
                        psutil reports (default: None)
  --disk-fstype-exclude FSTYPES
                        Comma-separated filesystem types to skip when auto-detecting mountpoints
                        (default: autofs,binfmt_misc,bpf,cgroup,cgroup2,configfs,debugfs,devfs,dev
                        pts,devtmpfs,fusectl,hugetlbfs,mqueue,overlay,proc,pstore,ramfs,securityfs
                        ,squashfs,sysfs,tmpfs,tracefs)
  --nic-exclude NICS    Comma-separated network interfaces not to report. Pass an empty string to
                        include loopback (default: lo,lo0)
  --procfs-path PATH    Read /proc from here instead (Linux). Set this to the host's /proc when
                        running in a container, ex. /host/proc (default: None)
  --host-root PATH      Bind-mount prefix to strip from mountpoint labels so a containerised run
                        names host paths as the host does, ex. /host/root (default: None)
```

### Subjects

`source_id` is the `--source-id` base (default `pc`) plus an instance suffix.

| Subject | Type | source_id | Cadence |
|---|---|---|---|
| `host_name` | `TimestampedString` | `pc` | `--info-interval` |
| `host_boot_time` | `TimestampedTimestamp` | `pc` | `--info-interval` |
| `cpu_load_pct` | `TimestampedFloat` | `pc` | `--interval` |
| `cpu_temperature_celsius` | `TimestampedFloat` | `pc/sensor/<chip>/<label>` | `--interval` |
| `memory_used_pct` | `TimestampedFloat` | `pc` | `--interval` |
| `swap_used_pct` | `TimestampedFloat` | `pc` | `--interval` |
| `disk_used_pct` | `TimestampedFloat` | `pc/disk/<mount>` | `--interval` |
| `disk_free_bytes` | `TimestampedInt64` | `pc/disk/<mount>` | `--interval` |
| `network_interface_up` | `TimestampedBool` | `pc/net/<nic>` (loopback excluded by default, see `--nic-exclude`) | `--interval` |

Mount and interface names are reduced to a single safe key chunk: `/` becomes
`root`, `/mnt/data` becomes `mnt_data`, `C:\` becomes `c`.

Only CPU sensor chips (`coretemp`, `k10temp`, `cpu_thermal`, ...) are published.
Other chips are not relabelled onto `cpu_temperature_celsius`.

The connector declares a source-level liveliness token on the `--source-id`
base, plus one subject-level token for each enabled metric group.

### Example

```bash
pc2keelson -r rise -e nuc01 --interval 5
```

### Running in Docker

A container sees its own namespaces, so a naive run reports the container, not
the host. Share the host PID and network namespaces, bind-mount `/proc` and the
root filesystem read-only, and point the connector at them:

```bash
docker run --rm --network host --pid host \
    -v /proc:/host/proc:ro -v /:/host/root:ro \
    ghcr.io/rise-maritime/keelson \
    "pc2keelson -r rise -e nuc01 --procfs-path /host/proc --host-root /host/root --disk-mountpoint /host/root"
```

On macOS and Windows, Docker runs a Linux VM, so run the connector on the host
itself there.
