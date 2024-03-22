import {Envelope} from './Envelope';
import SUBJECTS from './subjects.json';
import { MessageType, messageTypeRegistry as payloadsRegistry } from './payloads/typeRegistry';
import './payloads';

type SUBJECT_KEY = keyof typeof SUBJECTS;

// ENVELOPE HELPER FUNCTIONS
export function enclose(payload: Uint8Array, enclosed_at?: Date) {
    const env = Envelope.create({payload, enclosedAt: enclosed_at ?? new Date(), })
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
