#!/usr/bin/env python3
"""Verify a Keelson WHEP + TURN deployment end to end.

Checks, in order:
  1. The whep_signal RPC returns an SDP answer (not an error).
  2. MediaMTX advertised a `typ relay` candidate with a PUBLIC address
     (catches the private-relay-IP misconfig: missing relay-ip/external-ip).
  3. With --turn, the local client also relays through coturn, ICE reaches
     `completed`, and real video frames are decoded — proof media flows.

Requires aiortc for the media test:  pip install aiortc

  # signalling + relay-candidate check (no TURN creds needed):
  ./verify-whep-turn.py -r <realm> -e <entity> -p <path>

  # full media test (local client relays through coturn too):
  ./verify-whep-turn.py -r <realm> -e <entity> -p <path> \
      --turn 'turns:turn.example.com:443?transport=tcp' \
      --turn-user turnuser --turn-pass "$TURN_SECRET"
"""
import argparse
import asyncio
import ipaddress
import json
import re
import sys

import zenoh
import keelson
from keelson.interfaces.WHEPProxy_pb2 import WHEPRequest, WHEPResponse
from keelson.interfaces.ErrorResponse_pb2 import ErrorResponse


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        description="Verify a Keelson WHEP + TURN deployment end to end.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    ap.add_argument("-r", "--realm", required=True, help="Keelson base path / realm")
    ap.add_argument("-e", "--entity-id", required=True, help="connector entity id")
    ap.add_argument(
        "-i", "--responder-id", default="mediamtx", help="whep responder id"
    )
    ap.add_argument("-p", "--path", required=True, help="MediaMTX path (lower-cased)")
    ap.add_argument(
        "--connect",
        default="tcp/localhost:7447",
        help="zenoh router endpoint to connect to (client mode)",
    )
    ap.add_argument("--timeout", type=int, default=15, help="zenoh GET timeout (s)")
    ap.add_argument(
        "--turn", help="TURN url, e.g. turns:turn.example.com:443?transport=tcp"
    )
    ap.add_argument("--turn-user", help="TURN username")
    ap.add_argument("--turn-pass", help="TURN credential")
    ap.add_argument("--stun", default="stun:stun.l.google.com:19302", help="STUN url")
    ap.add_argument(
        "--media-timeout",
        type=int,
        default=30,
        help="seconds to wait for ICE + first frames",
    )
    return ap.parse_args()


def relay_candidates(sdp: str) -> list[str]:
    return [
        c.strip()
        for c in re.findall(r"^a=candidate:.*$", sdp, re.MULTILINE)
        if " typ relay" in c
    ]


def is_public(addr: str) -> bool:
    try:
        ip = ipaddress.ip_address(addr)
        return not (ip.is_private or ip.is_loopback or ip.is_link_local)
    except ValueError:
        return False  # mDNS / hostname -> treat as "not a verifiable public IP"


def signal(args: argparse.Namespace, offer_sdp: str) -> tuple[bool, str]:
    """Send the WHEPRequest via the whep_signal RPC; return (ok, sdp-or-error)."""
    conf = zenoh.Config()
    conf.insert_json5("mode", json.dumps("client"))
    conf.insert_json5("connect/endpoints", json.dumps([args.connect]))
    key = keelson.construct_rpc_key(
        base_path=args.realm,
        entity_id=args.entity_id,
        procedure="whep_signal",
        responder_id=args.responder_id,
    )
    req = WHEPRequest(path=args.path, sdp=offer_sdp)
    with zenoh.open(conf) as session:
        for reply in session.get(
            key, payload=req.SerializeToString(), timeout=args.timeout
        ):
            if reply.ok:
                return True, WHEPResponse.FromString(reply.ok.payload.to_bytes()).sdp
            try:
                return False, str(
                    ErrorResponse.FromString(reply.err.payload.to_bytes())
                )
            except Exception:  # pylint: disable=broad-exception-caught
                return False, reply.err.payload.to_bytes().decode("utf-8", "replace")
    return (
        False,
        "NO REPLY (queryable unreachable — check realm/entity/responder/router)",
    )


async def wait_for_gathering(pc) -> None:
    if pc.iceGatheringState == "complete":
        return
    done = asyncio.Event()

    @pc.on("icegatheringstatechange")
    def _():  # noqa: ANN202
        if pc.iceGatheringState == "complete":
            done.set()

    await asyncio.wait_for(done.wait(), timeout=30)


async def run(args: argparse.Namespace) -> int:
    # Import here so the signalling-only path works without aiortc installed.
    try:
        from aiortc import (  # noqa: PLC0415
            RTCPeerConnection,
            RTCSessionDescription,
            RTCConfiguration,
            RTCIceServer,
        )
    except ImportError:
        print(
            "ERROR: aiortc is required. Install with:  pip install aiortc",
            file=sys.stderr,
        )
        return 2

    print(
        f"=== verify WHEP+TURN: realm={args.realm} entity={args.entity_id} "
        f"responder={args.responder_id} path={args.path!r} ==="
    )

    ice = []
    if args.turn:
        if not (args.turn_user and args.turn_pass):
            print("ERROR: --turn requires --turn-user and --turn-pass", file=sys.stderr)
            return 2
        ice.append(
            RTCIceServer(
                urls=[args.turn], username=args.turn_user, credential=args.turn_pass
            )
        )
    if args.stun:
        ice.append(RTCIceServer(urls=[args.stun]))

    pc = RTCPeerConnection(RTCConfiguration(iceServers=ice))
    pc.addTransceiver("video", direction="recvonly")
    pc.addTransceiver("audio", direction="recvonly")

    video = {"track": None}

    @pc.on("track")
    def on_track(track):  # noqa: ANN001, ANN202
        print(f"  [track] {track.kind}")
        if track.kind == "video":
            video["track"] = track

    @pc.on("iceconnectionstatechange")
    def on_ice():  # noqa: ANN202
        print(f"  [ice] {pc.iceConnectionState}")

    await pc.setLocalDescription(await pc.createOffer())
    if args.turn:
        # WHEP is non-trickle: the offer must carry our candidates (incl. relay).
        print("  gathering local ICE candidates (incl. TURN relay)...")
        await wait_for_gathering(pc)
        mine = relay_candidates(pc.localDescription.sdp)
        print(f"  local relay candidates: {len(mine)}")

    # --- Check 1: signalling round-trip --------------------------------------
    print("\n[1] signalling whep_signal RPC...")
    ok, answer = signal(args, pc.localDescription.sdp)
    if not ok:
        print(f"  FAIL — RPC returned an error:\n    {answer}")
        await pc.close()
        return 1
    print("  OK — got SDP answer")

    # --- Check 2: public relay candidate -------------------------------------
    print("\n[2] inspecting MediaMTX relay candidate(s)...")
    relays = relay_candidates(answer)
    if not relays:
        print(
            "  FAIL — no `typ relay` candidate. MediaMTX did not allocate a relay "
            "(check its TURN url/creds and coturn reachability)."
        )
        await pc.close()
        return 1
    public_ok = False
    for c in relays:
        addr = c.split()[
            4
        ]  # a=candidate:<foundation> <comp> <proto> <prio> <ADDR> <port> ...
        tag = "PUBLIC ✓" if is_public(addr) else "PRIVATE ✗"
        print(f"  {tag}  {c}")
        public_ok = public_ok or is_public(addr)
    if not public_ok:
        print(
            "  FAIL — relay candidate has a private/internal address. "
            "Set relay-ip + external-ip in turnserver.conf (see examples/coturn/README.md #3)."
        )
        await pc.close()
        return 1
    print("  OK — relay candidate advertises a public address")

    # --- Check 3: media (only when this client can relay too) ----------------
    if not args.turn:
        print(
            "\n[3] media test SKIPPED (no --turn on this client; "
            "signalling + relay-candidate checks passed). "
            "Use a browser on a real network, or pass --turn/--turn-user/--turn-pass."
        )
        await pc.close()
        return 0

    # Applying the answer starts ICE — only do it when we intend to test media.
    await pc.setRemoteDescription(RTCSessionDescription(sdp=answer, type="answer"))

    print("\n[3] waiting for ICE + decoding frames...")
    for _ in range(args.media_timeout * 2):
        if pc.iceConnectionState in ("connected", "completed", "failed", "closed"):
            break
        await asyncio.sleep(0.5)
    if (
        pc.iceConnectionState not in ("connected", "completed")
        or video["track"] is None
    ):
        print(
            f"  FAIL — ICE never connected (state {pc.iceConnectionState}, "
            f"video track={'yes' if video['track'] else 'no'}). "
            "Transport-level failure: check TURN creds and relay reachability."
        )
        await pc.close()
        return 1
    try:
        for n in range(5):
            frame = await asyncio.wait_for(video["track"].recv(), timeout=15)
            print(f"  frame {n}: {frame.width}x{frame.height} pts={frame.pts}")
    except (
        asyncio.TimeoutError,
        Exception,
    ) as exc:  # pylint: disable=broad-exception-caught
        kind = "timed out" if isinstance(exc, asyncio.TimeoutError) else repr(exc)
        print(
            f"  FAIL — ICE connected ({pc.iceConnectionState}) but no frames decoded ({kind}).\n"
            "         The relay path is up but no RTP arrived — most likely the upstream\n"
            f"         source for path '{args.path}' is not producing media right now\n"
            "         (camera offline / RTSP source down), not a TURN problem."
        )
        await pc.close()
        return 1

    print("\n*** PASS — media flowing end to end through coturn ***")
    await pc.close()
    return 0


def main() -> None:
    args = parse_args()
    try:
        sys.exit(asyncio.run(run(args)))
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":
    main()
