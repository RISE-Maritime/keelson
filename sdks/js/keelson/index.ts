import { Envelope } from './Envelope';
import SUBJECTS from './subjects.json';
import { MessageType, messageTypeRegistry as payloadsRegistry } from './payloads/typeRegistry';
import './payloads';

type SUBJECT_KEY = keyof typeof SUBJECTS;

// KEY HELPER FUNCTIONS
const KEELSON_BASE_KEY_FORMAT = "{realm}/v0/{entity_id}"
const KEELSON_PUB_SUB_KEY_FORMAT = KEELSON_BASE_KEY_FORMAT + "/pubsub/{subject}/{source_id}"
const KEELSON_REQ_REP_KEY_FORMAT = KEELSON_BASE_KEY_FORMAT + "/rpc/{procedure}/{subject_in}/{subject_out}/{source_id}"



export function construct_pubSub_key(
    realm: string,
    entityId: string,
    subject: string,
    sourceId: string,
): string {
    /**
    * Construct a key expression for a publish and subscribe.
    */
    return KEELSON_PUB_SUB_KEY_FORMAT.replace("{realm}", realm)
        .replace("{entity_id}", entityId)
        .replace("{subject}", subject)
        .replace("{source_id}", sourceId);
}

export function construct_rpc_key(
    realm: string,
    entityId: string,
    procedure: string,
    subjectIn: string,
    subjectOut: string,
    sourceId: string,
): string {
    /**
     * Construct a key expression for a request reply interaction (Queryable).
     */
    return KEELSON_REQ_REP_KEY_FORMAT.replace("{realm}", realm)
        .replace("{entity_id}", entityId)
        .replace("{procedure}", procedure)
        .replace("{subject_in}", subjectIn)
        .replace("{subject_out}", subjectOut)
        .replace("{source_id}", sourceId);
}

export function parse_pubsub_key(key: string): Record<string, string> {
    const parts = key.split("/");
    return {
        realm: parts[0],
        entityId: parts[2],
        subject: parts[4],
        sourceId: parts.slice(5).join("/")
    }
}

export function parse_rpc_key(key: string): Record<string, string> {
    const parts = key.split("/");
    return {
        realm: parts[0],
        entityId: parts[2],
        procedure: parts[4],
        subjectIn: parts[5],
        subjectOut: parts[6],
        sourceId: parts.slice(7).join("/")
    }
}

export function get_subject_from_pubsub_key(key: string): string {
    return key.split("/")[4];
}

export function get_subjects_from_rpc_key(key: string): { subjectIn: string, subjectOut: string } {
    return { 
        subjectIn:  key.split("/")[5],
        subjectOut: key.split("/")[6]
    };
}

// ENVELOPE HELPER FUNCTIONS
export function enclose(payload: Uint8Array, enclosed_at?: Date, source_timestamp?: Date): Envelope {
    const env = Envelope.create({ payload: payload, enclosedAt: enclosed_at ?? new Date()})
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
