import { KeelsonClient } from "../keelson/KeelsonClient.ts";



// ---------- Config ----------
const env = (k: string, d?: string) =>
  (typeof Deno !== "undefined" ? Deno.env.get(k) : (globalThis as any).process?.env?.[k]) ?? d;

const LOCATOR   = env("ZENOH_LOCATOR", "ws://127.0.0.1:10000");
const MODE      = (env("ZENOH_MODE", "client") as "client" | "peer");
const SUBJECT   = env("IMU_SUBJECT", "sensors/imu");
const SOURCE_ID = env("IMU_SOURCE_ID", "sim/imu/0");
const RATE_HZ   = Number(env("IMU_RATE_HZ", "10"));
// const KEY       = `pubsub/${SUBJECT}/${SOURCE_ID}`;
const KEY = "demo/example/zenoh-ts-pub/grav"
// IMPORTANT: this must match the generated $type in ImuReading.ts
const TYPE_NAME = "keelson.TimestamptedFloat";


// ---------- Main ----------
async function main() {
  const kc = new KeelsonClient({ locator: LOCATOR, mode: MODE });
  await kc.connect();
  console.log(`[IMU Pub] Connected to ${LOCATOR}; publishing to ${KEY} (${TYPE_NAME}) @ ${RATE_HZ} Hz`);

  const periodMs = Math.max(1, Math.floor(1000 / Math.max(1, RATE_HZ)));
  setInterval(async () => {
    const msg = -9.8*Math.random();
    console.log(msg);
    try {
      await kc.publish(KEY, msg, TYPE_NAME); // explicit typeName; no subjects.json required
    } catch (e) {
      console.error("[IMU Pub] Publish error:", e);
    }
  }, periodMs);
}

main().catch((e) => {
  console.error("[IMU Pub] Fatal:", e);
  (typeof Deno !== "undefined" ? Deno.exit(1) : process.exit(1));
});
