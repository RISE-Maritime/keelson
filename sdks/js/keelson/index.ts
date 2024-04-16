import { Envelope } from './Envelope';
import SUBJECTS from './subjects.json';
import { MessageType, messageTypeRegistry as payloadsRegistry } from './payloads/typeRegistry';
import './payloads';
import ByteBuffer from "bytebuffer"

type SUBJECT_KEY = keyof typeof SUBJECTS;

/**
 * Constructs a key expression for a publish and subscribe.
 * @param realm - The realm value.
 * @param entityId - The entity ID value.
 * @param subject - The subject value.
 * @param sourceId - The source ID value.
 * @returns The constructed key expression.
 */
export function constructPubSubKey(
    realm: string,
    entityId: string,
    subject: string,
    sourceId: string,
): string {
    return KEELSON_PUB_SUB_KEY_FORMAT.replace("{realm}", realm)
        .replace("{entity_id}", entityId)
        .replace("{subject}", subject)
        .replace("{source_id}", sourceId);
}

/**
 * Constructs a key expression for a request reply interaction (Querable).
 * @param realm - The realm value.
 * @param entityId - The entity ID value.
 * @param responderId - The responder ID value.
 * @param procedure - The procedure value.
 * @returns The constructed key expression.
 */
export function constructReqRepKey(
    realm: string,
    entityId: string,
    responderId: string,
    procedure: string
): string {
    return KEELSON_REQ_REP_KEY_FORMAT.replace("{realm}", realm)
        .replace("{entity_id}", entityId)
        .replace("{responder_id}", responderId)
        .replace("{procedure}", procedure);
}

/**
 * Parses a publish and subscribe key expression and returns its components.
 * @param key - The key expression to parse.
 * @returns An object containing the parsed components.
 */
export function parse_pub_sub_key(key: string): Record<string, string> {
    const parts = key.split("/");
    return {
        realm: parts[0],
        entityId: parts[2],
        subject: parts[4],
        sourceId: parts[5]
    }
}

/**
 * Retrieves the subject from a publish and subscribe key expression.
 * @param key - The key expression.
 * @returns The subject extracted from the key expression.
 */
export function get_subject_from_pub_sub_key(key: string): string {
    return key.split("/")[4];
}

/**
 * Encloses a payload in an envelope.
 * @param payload - The payload to enclose.
 * @param enclosed_at - The optional enclosedAt date.
 * @returns The enclosed envelope.
 */
export function enclose(payload: Uint8Array, enclosed_at?: Date) {
    const env = Envelope.create({ payload, enclosedAt: enclosed_at ?? new Date(), })
    return env;
}

/**
 * Uncovers an encoded envelope and returns its components.
 * @param encodedEnvelope - The encoded envelope to uncover.
 * @returns An array containing the receivedAt date, enclosedAt date, and payload.
 */
export function uncover(encodedEnvelope: Uint8Array): [Date, Date | undefined, Uint8Array] | undefined {
    const env = Envelope.decode(encodedEnvelope);
    return [new Date(), env.enclosedAt, env.payload];
}

/**
 * Checks if a subject is well-known.
 * @param subject - The subject to check.
 * @returns True if the subject is well-known, false otherwise.
 */
export function isSubjectWellKnown(subject: string): boolean {
    return SUBJECTS[subject as SUBJECT_KEY] != null;
}

/**
 * Retrieves the schema for a subject.
 * @param subject - The subject to retrieve the schema for.
 * @returns The schema for the subject, or undefined if not found.
 */
export function getSubjectSchema(subject: string): string | undefined {
    return SUBJECTS[subject as SUBJECT_KEY]?.schema;
}

/**
 * Retrieves the Protobuf class from a type name.
 * @param typeName - The type name to retrieve the Protobuf class for.
 * @returns The Protobuf class for the type name.
 */
export function getProtobufClassFromTypeName(typeName: string) {
    return payloadsRegistry.get(typeName)
}

/**
 * Decodes a payload from a type name.
 * @param typeName - The type name of the payload.
 * @param payload - The payload to decode.
 * @returns The decoded payload.
 */
export function decodePayloadFromTypeName(typeName: string, payload: Uint8Array) {
    return payloadsRegistry.get(typeName)?.decode(payload);
}

/**
 * Encodes a payload from a type name.
 * @param typeName - The type name of the payload.
 * @param payload - The payload to encode.
 * @returns The encoded payload.
 */
export function encodePayloadFromTypeName(typeName: string, payload: any) {
    return payloadsRegistry.get(typeName)?.encode(payload).finish();
}

/**
 * Encloses a payload value in an envelope using the specified type name.
 * @param typeName - The type name of the payload.
 * @param payloadValue - The value of the payload to enclose.
 * @returns The enclosed envelope.
 */
export function encloseFromTypeName(typeName: string, payloadValue: any) {
    const payload = encodePayloadFromTypeName(typeName, payloadValue);

    if (payload != null) {
        return enclose(payload);
    }

    return undefined;
}

/**
 * Parses a Keelson message and returns its components.
 * @param envelope - The Keelson message envelope.
 * @returns An object containing the receivedAt date, enclosedAt date, and data.
 */
export function parseKeelsonMessage(envelope : any) {
    let bytes = new Uint8Array(ByteBuffer.fromBase64(envelope.value).toArrayBuffer())
    let parsed = uncover(bytes);
    let received_at = parsed[0];
    let enclosed_at = parsed[1];
    let payload = parsed[2];

    const subject = get_subject_from_pub_sub_key(envelope.key);
    let schemaProtoMsg = getSubjectSchema(subject);
    let data = decodePayloadFromTypeName(schemaProtoMsg, payload);

    return { received_at, enclosed_at, data };
}
