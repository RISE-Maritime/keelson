import "../src/keelson/payloads/index.ts"; // IMPORTANT: side-effects populate the registry
import { messageTypeRegistry } from "../src/keelson/payloads/typeRegistry.ts";

const keys = [...messageTypeRegistry.keys()].sort();
console.log("Registered types:", keys.length);
for (const k of keys) console.log(" -", k);