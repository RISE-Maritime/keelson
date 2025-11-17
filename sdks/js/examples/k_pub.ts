
import { Config, Publisher, Session } from "@eclipse-zenoh/zenoh-ts";
import { construct_pubSub_key, encodePayloadFromTypeName, displayTypeNames } from "../keelson"
import { TimestampedDouble } from "../keelson/payloads/Primitives";
import { Envelope } from "../keelson/Envelope";


async function sleep(ms: number): Promise<void> {
    return new Promise((resolve) => setTimeout(resolve, ms));
}

export async function main() {

  displayTypeNames();

  //Create Keelson specifics

  // const key = construct_pubSub_key(  );
  const key = "test/float/0"

  console.log("Opening session....");
  const session = await Session.open(new Config("ws/127.0.0.1:10000"));

  const publisher: Publisher = await session.declarePublisher(key);

  let startTime = performance.now();
  console.log("Starting loop at:", startTime);
  let n = 0;

  function sleep(ms: number) {
    return new Promise((resolve) => setTimeout(resolve, ms));
  }


  while (true) {
    console.log("Firing..", n);
    let testVal = TimestampedDouble.create({value: 5.0 + Math.random()});
    const payloadTest = encodePayloadFromTypeName("keelson.TimestampedDouble", testVal); 
    console.log(testVal);
    try { publisher.put(payloadTest); 
          console.log("Pushed..",);
    }
    catch {console.log("Failed..", n)}
    await sleep(250);
  }
  await session.close();
}

main();