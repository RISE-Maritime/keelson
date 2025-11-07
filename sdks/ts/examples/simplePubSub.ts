import { KeelsonClient } from "../src/keelson/KeelsonClient.js";

async function main() {
  const client = new KeelsonClient();
  await client.connect();

  await client.subscribe("demo/topic", msg => {
    console.log("Received:", msg.value);
  });

  await client.publish("demo/topic", { hello: "keelson-ts" }, "keelson.TimestamptedString");
}

main().catch(console.error);
