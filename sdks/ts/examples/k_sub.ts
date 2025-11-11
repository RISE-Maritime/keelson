
import {
  Config, Subscriber, Session, KeyExpr, Sample,
  SampleKind
} from "@eclipse-zenoh/zenoh-ts";
import {construct_pubSub_key, decodePayloadFromTypeName, displayTypeNames} from "../keelson"

export async function main() {
  displayTypeNames();
  console.warn('Opening session...');
  const session = await Session.open(new Config("ws/127.0.0.1:10000"));
  // const key = construct_pubSub_key(  );
  const key = "test/float/0"
  const keyExpr = new KeyExpr(key);

  const subscriberCallback = function (_sample: Sample): void {
    let val = decodePayloadFromTypeName("keelson.TimestampedDouble", _sample.payload().toBytes());
    console.log(
      ">> [Subscriber] Received " +
      SampleKind[_sample.kind()]
    );
    console.log(val)
  };

  console.warn(`Declaring Subscriber on '${key}'...`);
  const pollSubscriber: Subscriber = await session.declareSubscriber(key, { handler: subscriberCallback });
  console.warn("Press CTRL-C to quit...");
  
  function sleep(ms: number) {
    return new Promise((resolve) => setTimeout(resolve, ms));
  }

  let stopper = false;
  let n = 0;
  while (n < 10000) { 
    await sleep(1000);
    n++;
  }

  await pollSubscriber.undeclare();
  await session.close();
}

main();