/* eslint-disable */
import _m0 from "protobufjs/minimal";
import { messageTypeRegistry } from "./typeRegistry";

/** A [quaternion](https://eater.net/quaternions) representing a rotation in 3D space */
export interface Quaternion {
  $type: "foxglove.Quaternion";
  /** x value */
  x: number;
  /** y value */
  y: number;
  /** z value */
  z: number;
  /** w value */
  w: number;
}

function createBaseQuaternion(): Quaternion {
  return { $type: "foxglove.Quaternion", x: 0, y: 0, z: 0, w: 0 };
}

export const Quaternion = {
  $type: "foxglove.Quaternion" as const,

  encode(message: Quaternion, writer: _m0.Writer = _m0.Writer.create()): _m0.Writer {
    if (message.x !== 0) {
      writer.uint32(9).double(message.x);
    }
    if (message.y !== 0) {
      writer.uint32(17).double(message.y);
    }
    if (message.z !== 0) {
      writer.uint32(25).double(message.z);
    }
    if (message.w !== 0) {
      writer.uint32(33).double(message.w);
    }
    return writer;
  },

  decode(input: _m0.Reader | Uint8Array, length?: number): Quaternion {
    const reader = input instanceof _m0.Reader ? input : _m0.Reader.create(input);
    let end = length === undefined ? reader.len : reader.pos + length;
    const message = createBaseQuaternion();
    while (reader.pos < end) {
      const tag = reader.uint32();
      switch (tag >>> 3) {
        case 1:
          if (tag !== 9) {
            break;
          }

          message.x = reader.double();
          continue;
        case 2:
          if (tag !== 17) {
            break;
          }

          message.y = reader.double();
          continue;
        case 3:
          if (tag !== 25) {
            break;
          }

          message.z = reader.double();
          continue;
        case 4:
          if (tag !== 33) {
            break;
          }

          message.w = reader.double();
          continue;
      }
      if ((tag & 7) === 4 || tag === 0) {
        break;
      }
      reader.skipType(tag & 7);
    }
    return message;
  },

  fromJSON(object: any): Quaternion {
    return {
      $type: Quaternion.$type,
      x: isSet(object.x) ? globalThis.Number(object.x) : 0,
      y: isSet(object.y) ? globalThis.Number(object.y) : 0,
      z: isSet(object.z) ? globalThis.Number(object.z) : 0,
      w: isSet(object.w) ? globalThis.Number(object.w) : 0,
    };
  },

  toJSON(message: Quaternion): unknown {
    const obj: any = {};
    if (message.x !== 0) {
      obj.x = message.x;
    }
    if (message.y !== 0) {
      obj.y = message.y;
    }
    if (message.z !== 0) {
      obj.z = message.z;
    }
    if (message.w !== 0) {
      obj.w = message.w;
    }
    return obj;
  },

  create<I extends Exact<DeepPartial<Quaternion>, I>>(base?: I): Quaternion {
    return Quaternion.fromPartial(base ?? ({} as any));
  },
  fromPartial<I extends Exact<DeepPartial<Quaternion>, I>>(object: I): Quaternion {
    const message = createBaseQuaternion();
    message.x = object.x ?? 0;
    message.y = object.y ?? 0;
    message.z = object.z ?? 0;
    message.w = object.w ?? 0;
    return message;
  },
};

messageTypeRegistry.set(Quaternion.$type, Quaternion);

type Builtin = Date | Function | Uint8Array | string | number | boolean | undefined;

type DeepPartial<T> = T extends Builtin ? T
  : T extends globalThis.Array<infer U> ? globalThis.Array<DeepPartial<U>>
  : T extends ReadonlyArray<infer U> ? ReadonlyArray<DeepPartial<U>>
  : T extends {} ? { [K in Exclude<keyof T, "$type">]?: DeepPartial<T[K]> }
  : Partial<T>;

type KeysOfUnion<T> = T extends T ? keyof T : never;
type Exact<P, I extends P> = P extends Builtin ? P
  : P & { [K in keyof P]: Exact<P[K], I[K]> } & { [K in Exclude<keyof I, KeysOfUnion<P> | "$type">]: never };

function isSet(value: any): boolean {
  return value !== null && value !== undefined;
}
