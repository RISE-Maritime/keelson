import { Envelope } from './Envelope.ts';
import SUBJECTS from './subjects.json' with { type: "json" };
import { MessageType, messageTypeRegistry as payloadsRegistry } from './payloads/typeRegistry.ts';
import './payloads';


// Side-effect: load all payloads so they self-register into the registry
import "../keelson/payloads/index.ts";

type SUBJECT_KEY = keyof typeof SUBJECTS;

// KEY HELPER FUNCTIONS
const KEELSON_BASE_KEY_FORMAT = "{base_path}/@v0/{entity_id}"
const KEELSON_PUB_SUB_KEY_FORMAT = KEELSON_BASE_KEY_FORMAT + "/pubsub/{subject}/{source_id}"
const KEELSON_REQ_REP_KEY_FORMAT = KEELSON_BASE_KEY_FORMAT + "/@rpc/{procedure}/{source_id}"



export function construct_pubSub_key(
    base_path: string,
    entityId: string,
    subject: string,
    sourceId: string,
): string {
    /**
    * Construct a key expression for a publish and subscribe.
    */
    if (!isSubjectWellKnown(subject)) {
        console.warn(`Subject: ${subject} is NOT well-known!`)
    }
    return KEELSON_PUB_SUB_KEY_FORMAT.replace("{base_path}", base_path)
        .replace("{entity_id}", entityId)
        .replace("{subject}", subject)
        .replace("{source_id}", sourceId);
}

export function construct_rpc_key(
    base_path: string,
    entityId: string,
    procedure: string,
    sourceId: string,
): string {
    /**
     * Construct a key expression for a request reply interaction (Queryable).
     */
    return KEELSON_REQ_REP_KEY_FORMAT.replace("{base_path}", base_path)
        .replace("{entity_id}", entityId)
        .replace("{procedure}", procedure)
        .replace("{source_id}", sourceId);
}

export function parse_pubsub_key(key: string): Record<string, string> {
    const parts = key.split("/");
    return {
        base_path: parts[0],
        entityId: parts[2],
        subject: parts[4],
        sourceId: parts.slice(5).join("/")
    }
}

export function parse_rpc_key(key: string): Record<string, string> {
    const parts = key.split("/");
    return {
        base_path: parts[0],
        entityId: parts[2],
        procedure: parts[4],
        sourceId: parts.slice(5).join("/")
    }
}

export function get_subject_from_pubsub_key(key: string): string {
    return key.split("/")[4];
}


// ENVELOPE HELPER FUNCTIONS
export function enclose(payload: Uint8Array, enclosed_at?: Date): Envelope {
    const env = Envelope.create({ payload: payload, enclosedAt: enclosed_at ?? new Date() })
    return env;
}

export function uncover(encodedEnvelope: Uint8Array): [Date, Date | undefined, Uint8Array] | undefined {
    const env = Envelope.decode(encodedEnvelope);
    return [new Date(), env.enclosedAt, env.payload];
}

// SUBJECTS HELPER FUNCTIONS
export function isSubjectWellKnown(subject: string): boolean {
    return SUBJECTS[subject as SUBJECT_KEY] != null;
}

export function getSubjectSchema(subject: string): string | undefined {
    return SUBJECTS[subject as SUBJECT_KEY];
}

// PAYLOADS
export function getProtobufClassFromTypeName(typeName: string) {
    return payloadsRegistry.get(typeName)
}

export function decodePayloadFromTypeName(typeName: string, payload: Uint8Array) {
    return payloadsRegistry.get(typeName)?.decode(payload);
}

export function encodePayloadFromTypeName(typeName: string, payload: any) {
    return payloadsRegistry.get(typeName)?.encode(payload).finish();
}


export function encloseFromTypeName(typeName: string, payloadValue: any) {
    const payload = encodePayloadFromTypeName(typeName, payloadValue);

    if (payload != null) {
        return enclose(payload);
    }

    return undefined;
}

import * as zenoh from "@eclipse-zenoh/zenoh-ts";

export interface KeelsonConfig {
  locator?: string;           // e.g. "ws://127.0.0.1:10000"
  session?: zenoh.Session;    // optional pre-existing session
}


export class KeelsonClient {
  private session?: zenoh.Session;
  private subscribers: zenoh.Subscriber[] = [];

  constructor(private config: KeelsonConfig = {}) {
    if (!payloadsRegistry || payloadsRegistry.size === 0) {
      console.warn(
        "[KeelsonClient] ⚠️ Payload registry is empty — ensure helper imports ran!"
      );
    }
  }

  /** Connect to Zenoh using websocket locator (or reuse provided session). */
  async connect(): Promise<void> {
    if (this.config.session) {
      this.session = this.config.session;
      console.log("[KeelsonClient] Using provided Zenoh session.");
      return;
    }

    if (this.session) {
      console.warn("[KeelsonClient] Session already active.");
      return;
    }

    const locator = this.config.locator ?? "ws://127.0.0.1:10000";

    console.log(`[KeelsonClient] Connecting to Zenoh at ${locator}...`);

    try {
      this.session = await zenoh.Session.open(new zenoh.Config(locator));
      console.log(`[KeelsonClient] Connected. Session ID: ${this.session.zid}`);
    } catch (err) {
      console.error("[KeelsonClient] ❌ Failed to connect:", err);
      throw err;
    }
  }

  /**
   * Publish a Keelson payload
   * @param key - Zenoh key expression
   * @param payloadValue - Plain JS object representing the message
   * @param typeName - Type name (must exist in payloadsRegistry)
   */
  async publish(key: string, payloadValue: any, typeName: string): Promise<void> {
    if (!this.session) throw new Error("Session not connected.");

    const TypeClass = payloadsRegistry.get(typeName);
    if (!TypeClass) {
      throw new Error(`[KeelsonClient] Unknown payload type: ${typeName}`);
    }

    // Encode + wrap
    const envelope = encloseFromTypeName(typeName, payloadValue);
    if (!envelope) {
      throw new Error(`[KeelsonClient] Failed to encode envelope for ${typeName}`);
    }

    try {
      const encoded = Envelope.encode(envelope).finish();
      await this.session.put(key, encoded);
      console.log(`[KeelsonClient] ✅ Published ${typeName} → ${key}`);
    } catch (err) {
      console.error("[KeelsonClient] ❌ Failed to publish:", err);
      throw err;
    }
  }

  /**
   * Subscribe to a key and receive decoded payloads.
   * @param key - Key expression (supports wildcards)
   * @param onMessage - Callback(payload, metadata)
   * @returns Unsubscribe function
   */
  async subscribe(
    key: string,
    onMessage: (decoded: any, meta: { key: string; typeName?: string }) => void,
  ): Promise<() => Promise<void>> {
    if (!this.session) throw new Error("Session not connected.");

    console.log(`[KeelsonClient] Subscribing to ${key}`);

    const subscriber = await this.session.declareSubscriber(key, (sample) => {
      try {
        const data = new Uint8Array(sample.payload);
        const env = Envelope.decode(data);

        // Find message type
        const typeName = env.payload_type ?? env.type ?? undefined;
        if (!typeName) {
          console.warn("[KeelsonClient] Received envelope without type info");
          return;
        }

        const decoded = decodePayloadFromTypeName(typeName, env.payload);
        if (!decoded) {
          console.warn(`[KeelsonClient] Unknown payload type received: ${typeName}`);
          return;
        }

        onMessage(decoded, { key: sample.key_expr.toString(), typeName });
      } catch (err) {
        console.error("[KeelsonClient] ❌ Error decoding message:", err);
      }
    });

    this.subscribers.push(subscriber);

    return async () => {
      await subscriber.undeclare();
      console.log(`[KeelsonClient] Unsubscribed from ${key}`);
    };
  }

  /** Close all subscribers and the session safely. */
  async close(): Promise<void> {
    try {
      for (const sub of this.subscribers) {
        await sub.undeclare();
      }
      this.subscribers = [];

      if (this.session) {
        await this.session.close();
        console.log("[KeelsonClient] 🔒 Session closed.");
      }
    } catch (err) {
      console.error("[KeelsonClient] ❌ Error during close:", err);
    } finally {
      this.session = undefined;
    }
  }
}
