import { Envelope } from './Envelope';
import SUBJECTS from './subjects.json';
import { MessageType, messageTypeRegistry as payloadsRegistry } from './payloads/typeRegistry';
import './payloads';

type SUBJECT_KEY = keyof typeof SUBJECTS;

// KEY HELPER FUNCTIONS
const KEELSON_BASE_KEY_FORMAT = "{realm}/v0/{entity_id}"
const KEELSON_PUB_SUB_KEY_FORMAT = KEELSON_BASE_KEY_FORMAT + "/pubsub/{subject}/{source_id}"
const KEELSON_REQ_REP_KEY_FORMAT = KEELSON_BASE_KEY_FORMAT + "/rpc/{responder_id}/{procedure}"



export function constructPubSubKey(
    realm: string,
    entityId: string,
    subject: string,
    sourceId: string,
): string {
    /**
    * Construct a keyexpression for a publish and subscribe.
    */
    return KEELSON_PUB_SUB_KEY_FORMAT.replace("{realm}", realm)
        .replace("{entity_id}", entityId)
        .replace("{subject}", subject)
        .replace("{source_id}", sourceId);
}

export function constructReqRepKey(
    realm: string,
    entityId: string,
    responderId: string,
    procedure: string
): string {
    /**
     * Construct a keyexpression for a request reply interaction (Querable).
     */
    return KEELSON_REQ_REP_KEY_FORMAT.replace("{realm}", realm)
        .replace("{entity_id}", entityId)
        .replace("{responder_id}", responderId)
        .replace("{procedure}", procedure);
}

// Unsure if this is correct functionallity 
export function parse_pub_sub_key(key: string): Record<string, string> {
    const parts = key.split("/");
    return {
        realm: parts[0],
        entityId: parts[2],
        subject: parts[4],
        sourceId: parts[5]
    }
}

export function get_subject_from_pub_sub_key(key: string): string {
    return key.split("/")[4];
}

// ENVELOPE HELPER FUNCTIONS
export function enclose(payload: Uint8Array, enclosed_at?: Date) {
    const env = Envelope.create({ payload, enclosedAt: enclosed_at ?? new Date(), })
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
    return SUBJECTS[subject as SUBJECT_KEY]?.schema;
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
