# coturn behind Traefik — TURN/TLS for MediaMTX over CGNAT

A minimal, set-once [coturn](https://github.com/coturn/coturn) that gives
MediaMTX a **TURN relay over TLS on `443`**, so WebRTC media reaches viewers
even when MediaMTX is behind CGNAT.

```
camera → MediaMTX (CGNAT) ──relay──▶ coturn (turn.<domain>:443/TLS) ──relay──▶ viewer
```

Traefik terminates TLS on `443` (its ACME manages the cert) and forwards the
decrypted TURN stream to coturn's plain `listening-port` on loopback — coturn
needs no cert of its own.

## Files

| File | Purpose |
|---|---|
| `docker-compose.yml` | coturn in `network_mode: host` |
| `turnserver.conf` | credentials, relay range, relay-IP |
| `traefik-dynamic.yml` | Traefik file-provider TCP router: `HostSNI(turn.<domain>)` → coturn |

## Setup

1. Set a secret in `turnserver.conf` (`openssl rand -hex 24`); client uses `username=turnuser` + that secret.
2. Point `turn.<domain>` DNS at the host's public IP.
3. Open **inbound UDP `49150-49200`** (matches `min-port`/`max-port`). `443` is already served by Traefik.
4. If behind 1:1 NAT or with Docker bridges present, set `relay-ip`/`external-ip` (see `turnserver.conf`).
5. In `traefik-dynamic.yml`, pick the backend address matching how Traefik reaches coturn.

Verify TLS: `openssl s_client -connect turn.<domain>:443 -servername turn.<domain> -brief </dev/null`.
For an end-to-end media check, use [`../verify-whep-turn.py`](../verify-whep-turn.py).

## Troubleshooting

The `turns:` path has three hops, each failing differently:

| Symptom | Cause | Fix |
|---|---|---|
| TLS handshake EOF, no cert | Traefik router matched but ACME hasn't issued a cert | check `certresolver` name + Traefik ACME logs |
| backend **refused** (instant) | wrong loopback (`127.0.0.1` while Traefik is bridged) | use `host.docker.internal:3478` + `extra_hosts: ["host.docker.internal:host-gateway"]` |
| backend **hangs** | host firewall drops the bridge→host hop | `ufw allow from 172.16.0.0/12 to any port 3478 proto tcp` |
| connects, no media; `typ relay` candidate has a **private** IP | coturn advertised a non-public interface | set `relay-ip`/`external-ip` in `turnserver.conf` |
| `403 Forbidden IP` in client logs | `denied-peer-ip` refusing a private target | harmless if a valid relay pair is still found |
