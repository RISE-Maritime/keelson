import { KeelsonClient } from "../keelson/KeelsonClient.ts";

const env = (k: string, d?: string) =>
  (typeof Deno !== "undefined" ? Deno.env.get(k) : (globalThis as any).process?.env?.[k]) ?? d;

const LOCATOR  = env("ZENOH_LOCATOR", "ws://127.0.0.1:10000");
const MODE     = (env("ZENOH_MODE", "client") as "client" | "peer");
const KEY_EXPR = env("IMU_KEY_EXPR", "pubsub/sensors/imu/**");

function fmt3(v?: {x?: number; y?: number; z?: number}) {
  return v ? `x:${v.x?.toFixed?.(3)} y:${v.y?.toFixed?.(3)} z:${v.z?.toFixed?.(3)}` : "—";
}

async function main() {
  const kc = new KeelsonClient({ locator: LOCATOR, mode: MODE });
  await kc.connect();
  console.log(`[IMU Rx] Connected to ${LOCATOR}; subscribing ${KEY_EXPR}`);

  await kc.subscribe(KEY_EXPR, (msg: any) => {
    console.log("Msg seen");
    // msg is a decoded ImuReading (from registry via typeUrl)
    const la = msg.linearAcceleration;
    const av = msg.angularVelocity;
    const ts = msg.timestamp ? new Date(msg.timestamp).toISOString() : "";
    console.log(`[IMU] ${ts} LA{${fmt3(la)}} AV{${fmt3(av)}} frame=${msg.frameId ?? ""}`);
  });
}

main().catch((e) => {
  console.error("[IMU Rx] Fatal:", e);
  (typeof Deno !== "undefined" ? Deno.exit(1) : process.exit(1));
});
