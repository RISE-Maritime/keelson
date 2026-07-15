import { Envelope } from './Envelope';
import SUBJECTS from './subjects.json';
import INTERFACES from './interfaces.json';
import QOS from './qos.json';
import { MessageType, messageTypeRegistry as payloadsRegistry } from './payloads/typeRegistry';
import './payloads';

type SUBJECT_KEY = keyof typeof SUBJECTS;
type INTERFACE_KEY = keyof typeof INTERFACES;

// KEY HELPER FUNCTIONS
const KEELSON_BASE_KEY_FORMAT = "{base_path}/@v0/{entity_id}"
const KEELSON_PUB_SUB_KEY_FORMAT = KEELSON_BASE_KEY_FORMAT + "/pubsub/{subject}/{source_id}"
const KEELSON_REQ_REP_KEY_FORMAT = KEELSON_BASE_KEY_FORMAT + "/@rpc/{interface}/{version}/{procedure}/{source_id}"
const KEELSON_RPC_INTERFACE_LIVELINESS_KEY_FORMAT = KEELSON_BASE_KEY_FORMAT + "/@rpc/{interface}/{version}/*/{source_id}"

// An interface version chunk is v{N} with N a positive integer.
function isValidInterfaceVersion(version: string): boolean {
    return /^v[1-9][0-9]*$/.test(version);
}



export function construct_pubSub_key(
    base_path: string,
    entityId: string,
    subject: string,
    sourceId: string,
    targetId?: string,
): string {
    /**
    * Construct a key expression for a publish and subscribe.
    *
    * @param base_path - The base path of the entity
    * @param entityId - The entity id
    * @param subject - The subject of the interaction
    * @param sourceId - The source id of the entity
    * @param targetId - Optional target id for @target extension (e.g., "mmsi_245060000")
    * @returns The constructed key expression
    */
    if (!isSubjectWellKnown(subject)) {
        console.warn(`Subject: ${subject} is NOT well-known!`)
    }
    const key = KEELSON_PUB_SUB_KEY_FORMAT.replace("{base_path}", base_path)
        .replace("{entity_id}", entityId)
        .replace("{subject}", subject)
        .replace("{source_id}", sourceId);

    return targetId ? `${key}/@target/${targetId}` : key;
}

export function construct_rpc_key(
    base_path: string,
    entityId: string,
    iface: string,
    version: string,
    procedure: string,
    sourceId: string,
): string {
    /**
     * Construct a key expression for a request reply interaction (Queryable).
     *
     * @param iface - Well-known RPC interface name (see interfaces.yaml)
     * @param version - Interface version chunk, v{N} (e.g. "v1")
     */
    if (!isValidInterfaceVersion(version)) {
        throw new Error(`Interface version '${version}' is not of the required form v{N}`);
    }
    if (!isInterfaceWellKnown(`${iface}/${version}`)) {
        console.warn(`Interface: ${iface}/${version} is NOT well-known!`)
    }
    return KEELSON_REQ_REP_KEY_FORMAT.replace("{base_path}", base_path)
        .replace("{entity_id}", entityId)
        .replace("{interface}", iface)
        .replace("{version}", version)
        .replace("{procedure}", procedure)
        .replace("{source_id}", sourceId);
}

export function construct_rpc_interface_liveliness_key(
    base_path: string,
    entityId: string,
    iface: string,
    version: string,
    sourceId: string,
): string {
    /**
     * Construct the liveliness token key for one served RPC
     * (interface, version) pair. The `*` in the procedure slot means
     * "any procedure in this scope"; under the full-interface rule the
     * token also claims full coverage of the interface version.
     */
    if (!isValidInterfaceVersion(version)) {
        throw new Error(`Interface version '${version}' is not of the required form v{N}`);
    }
    if (!isInterfaceWellKnown(`${iface}/${version}`)) {
        console.warn(`Interface: ${iface}/${version} is NOT well-known!`)
    }
    return KEELSON_RPC_INTERFACE_LIVELINESS_KEY_FORMAT.replace("{base_path}", base_path)
        .replace("{entity_id}", entityId)
        .replace("{interface}", iface)
        .replace("{version}", version)
        .replace("{source_id}", sourceId);
}

export interface ParsedPubSubKey {
    base_path: string;
    entityId: string;
    subject: string;
    sourceId: string;
    targetId: string | null;
}

export function parse_pubsub_key(key: string): ParsedPubSubKey {
    /**
     * Parse a key expression for a publish subscribe interaction.
     *
     * @param key - The key expression to parse
     * @returns The parsed key with base_path, entityId, subject, sourceId, and targetId
     */
    const TARGET_MARKER = "/@target/";

    let baseKey = key;
    let targetId: string | null = null;

    // Check for @target extension
    const targetIndex = key.indexOf(TARGET_MARKER);
    if (targetIndex !== -1) {
        baseKey = key.substring(0, targetIndex);
        targetId = key.substring(targetIndex + TARGET_MARKER.length);
    }

    const parts = baseKey.split("/");
    return {
        base_path: parts[0],
        entityId: parts[2],
        subject: parts[4],
        sourceId: parts.slice(5).join("/"),
        targetId: targetId
    }
}

export function parse_rpc_key(key: string): Record<string, string> {
    const parts = key.split("/");
    return {
        base_path: parts[0],
        entityId: parts[2],
        interface: parts[4],
        version: parts[5],
        procedure: parts[6],
        sourceId: parts.slice(7).join("/")
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

// INTERFACES HELPER FUNCTIONS
export function isInterfaceWellKnown(interfaceAndVersion: string): boolean {
    return INTERFACES[interfaceAndVersion as INTERFACE_KEY] != null;
}

export function getInterfaceService(interfaceAndVersion: string): string | undefined {
    return INTERFACES[interfaceAndVersion as INTERFACE_KEY];
}

// QoS HELPER FUNCTIONS
// Transport-neutral QoS profile for a subject. Map these string values onto
// the zenoh-ts QoS enums at the call site (the keelson-js SDK stays a
// transport/envelope toolkit and does not depend on a Zenoh client).
export interface QoSProfile {
    name: string;
    priority: "REAL_TIME" | "INTERACTIVE_HIGH" | "INTERACTIVE_LOW" | "DATA_HIGH" | "DATA" | "DATA_LOW" | "BACKGROUND";
    congestion_control: "DROP" | "BLOCK";
    reliability: "RELIABLE" | "BEST_EFFORT";
    express: boolean;
}

const QOS_PROFILES: Record<string, Omit<QoSProfile, "name">> = (QOS as any).profiles ?? {};
const QOS_SUBJECTS: Record<string, string> = (QOS as any).subjects ?? {};
const QOS_DEFAULT: string = (QOS as any).default ?? "default";

/** Return the QoS profile assigned to a well-known subject (or the default). */
export function qosFor(subject: string): QoSProfile {
    const name = QOS_SUBJECTS[subject] ?? QOS_DEFAULT;
    const fields = QOS_PROFILES[name];
    return { name, ...fields };
}

// PAYLOADS
export function getProtobufClassFromTypeName(typeName: string) {
    return payloadsRegistry.get(typeName)
}

export function decodePayloadFromTypeName(typeName: string, payload: Uint8Array) {
    return payloadsRegistry.get(typeName)?.decode(payload);
}

export function encodePayloadFromTypeName(typeName: string, payload: any) {
    let typeClass: MessageType | undefined = payloadsRegistry.get(typeName);
    if (!typeClass) {
        return undefined;
    }
    let message = typeClass.fromPartial(payload);
    return typeClass.encode(message).finish();
}


export function encloseFromTypeName(typeName: string, payloadValue: any) {
    const payload = encodePayloadFromTypeName(typeName, payloadValue);

    if (payload != null) {
        return enclose(payload);
    }

    return undefined;
}
