/* eslint-disable */
import _m0 from "protobufjs/minimal";
import { Quaternion } from "./Quaternion";
import { Vector3 } from "./Vector3";
import { Timestamp } from "./google/protobuf/timestamp";
import { messageTypeRegistry } from "./typeRegistry";

/** A transform between two reference frames in 3D space */
export interface FrameTransform {
  $type: "foxglove.FrameTransform";
  /** Timestamp of transform */
  timestamp:
    | Date
    | undefined;
  /** Name of the parent frame */
  parentFrameId: string;
  /** Name of the child frame */
  childFrameId: string;
  /** Translation component of the transform */
  translation:
    | Vector3
    | undefined;
  /** Rotation component of the transform */
  rotation: Quaternion | undefined;
}

function createBaseFrameTransform(): FrameTransform {
  return {
    $type: "foxglove.FrameTransform",
    timestamp: undefined,
    parentFrameId: "",
    childFrameId: "",
    translation: undefined,
    rotation: undefined,
  };
}

export const FrameTransform = {
  $type: "foxglove.FrameTransform" as const,

  encode(message: FrameTransform, writer: _m0.Writer = _m0.Writer.create()): _m0.Writer {
    if (message.timestamp !== undefined) {
      Timestamp.encode(toTimestamp(message.timestamp), writer.uint32(10).fork()).ldelim();
    }
    if (message.parentFrameId !== "") {
      writer.uint32(18).string(message.parentFrameId);
    }
    if (message.childFrameId !== "") {
      writer.uint32(26).string(message.childFrameId);
    }
    if (message.translation !== undefined) {
      Vector3.encode(message.translation, writer.uint32(34).fork()).ldelim();
    }
    if (message.rotation !== undefined) {
      Quaternion.encode(message.rotation, writer.uint32(42).fork()).ldelim();
    }
    return writer;
  },

  decode(input: _m0.Reader | Uint8Array, length?: number): FrameTransform {
    const reader = input instanceof _m0.Reader ? input : _m0.Reader.create(input);
    let end = length === undefined ? reader.len : reader.pos + length;
    const message = createBaseFrameTransform();
    while (reader.pos < end) {
      const tag = reader.uint32();
      switch (tag >>> 3) {
        case 1:
          if (tag !== 10) {
            break;
          }

          message.timestamp = fromTimestamp(Timestamp.decode(reader, reader.uint32()));
          continue;
        case 2:
          if (tag !== 18) {
            break;
          }

          message.parentFrameId = reader.string();
          continue;
        case 3:
          if (tag !== 26) {
            break;
          }

          message.childFrameId = reader.string();
          continue;
        case 4:
          if (tag !== 34) {
            break;
          }

          message.translation = Vector3.decode(reader, reader.uint32());
          continue;
        case 5:
          if (tag !== 42) {
            break;
          }

          message.rotation = Quaternion.decode(reader, reader.uint32());
          continue;
      }
      if ((tag & 7) === 4 || tag === 0) {
        break;
      }
      reader.skipType(tag & 7);
    }
    return message;
  },

  fromJSON(object: any): FrameTransform {
    return {
      $type: FrameTransform.$type,
      timestamp: isSet(object.timestamp) ? fromJsonTimestamp(object.timestamp) : undefined,
      parentFrameId: isSet(object.parentFrameId) ? globalThis.String(object.parentFrameId) : "",
      childFrameId: isSet(object.childFrameId) ? globalThis.String(object.childFrameId) : "",
      translation: isSet(object.translation) ? Vector3.fromJSON(object.translation) : undefined,
      rotation: isSet(object.rotation) ? Quaternion.fromJSON(object.rotation) : undefined,
    };
  },

  toJSON(message: FrameTransform): unknown {
    const obj: any = {};
    if (message.timestamp !== undefined) {
      obj.timestamp = message.timestamp.toISOString();
    }
    if (message.parentFrameId !== "") {
      obj.parentFrameId = message.parentFrameId;
    }
    if (message.childFrameId !== "") {
      obj.childFrameId = message.childFrameId;
    }
    if (message.translation !== undefined) {
      obj.translation = Vector3.toJSON(message.translation);
    }
    if (message.rotation !== undefined) {
      obj.rotation = Quaternion.toJSON(message.rotation);
    }
    return obj;
  },

  create<I extends Exact<DeepPartial<FrameTransform>, I>>(base?: I): FrameTransform {
    return FrameTransform.fromPartial(base ?? ({} as any));
  },
  fromPartial<I extends Exact<DeepPartial<FrameTransform>, I>>(object: I): FrameTransform {
    const message = createBaseFrameTransform();
    message.timestamp = object.timestamp ?? undefined;
    message.parentFrameId = object.parentFrameId ?? "";
    message.childFrameId = object.childFrameId ?? "";
    message.translation = (object.translation !== undefined && object.translation !== null)
      ? Vector3.fromPartial(object.translation)
      : undefined;
    message.rotation = (object.rotation !== undefined && object.rotation !== null)
      ? Quaternion.fromPartial(object.rotation)
      : undefined;
    return message;
  },
};

messageTypeRegistry.set(FrameTransform.$type, FrameTransform);

type Builtin = Date | Function | Uint8Array | string | number | boolean | undefined;

type DeepPartial<T> = T extends Builtin ? T
  : T extends globalThis.Array<infer U> ? globalThis.Array<DeepPartial<U>>
  : T extends ReadonlyArray<infer U> ? ReadonlyArray<DeepPartial<U>>
  : T extends {} ? { [K in Exclude<keyof T, "$type">]?: DeepPartial<T[K]> }
  : Partial<T>;

type KeysOfUnion<T> = T extends T ? keyof T : never;
type Exact<P, I extends P> = P extends Builtin ? P
  : P & { [K in keyof P]: Exact<P[K], I[K]> } & { [K in Exclude<keyof I, KeysOfUnion<P> | "$type">]: never };

function toTimestamp(date: Date): Timestamp {
  const seconds = Math.trunc(date.getTime() / 1_000);
  const nanos = (date.getTime() % 1_000) * 1_000_000;
  return { $type: "google.protobuf.Timestamp", seconds, nanos };
}

function fromTimestamp(t: Timestamp): Date {
  let millis = (t.seconds || 0) * 1_000;
  millis += (t.nanos || 0) / 1_000_000;
  return new globalThis.Date(millis);
}

function fromJsonTimestamp(o: any): Date {
  if (o instanceof globalThis.Date) {
    return o;
  } else if (typeof o === "string") {
    return new globalThis.Date(o);
  } else {
    return fromTimestamp(Timestamp.fromJSON(o));
  }
}

function isSet(value: any): boolean {
  return value !== null && value !== undefined;
}
