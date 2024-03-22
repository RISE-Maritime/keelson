/* eslint-disable */
import _m0 from "protobufjs/minimal";
import { Quaternion } from "./Quaternion";
import { Vector3 } from "./Vector3";
import { messageTypeRegistry } from "./typeRegistry";

/** A position and orientation for an object or reference frame in 3D space */
export interface Pose {
  $type: "foxglove.Pose";
  /** Point denoting position in 3D space */
  position:
    | Vector3
    | undefined;
  /** Quaternion denoting orientation in 3D space */
  orientation: Quaternion | undefined;
}

function createBasePose(): Pose {
  return { $type: "foxglove.Pose", position: undefined, orientation: undefined };
}

export const Pose = {
  $type: "foxglove.Pose" as const,

  encode(message: Pose, writer: _m0.Writer = _m0.Writer.create()): _m0.Writer {
    if (message.position !== undefined) {
      Vector3.encode(message.position, writer.uint32(10).fork()).ldelim();
    }
    if (message.orientation !== undefined) {
      Quaternion.encode(message.orientation, writer.uint32(18).fork()).ldelim();
    }
    return writer;
  },

  decode(input: _m0.Reader | Uint8Array, length?: number): Pose {
    const reader = input instanceof _m0.Reader ? input : _m0.Reader.create(input);
    let end = length === undefined ? reader.len : reader.pos + length;
    const message = createBasePose();
    while (reader.pos < end) {
      const tag = reader.uint32();
      switch (tag >>> 3) {
        case 1:
          if (tag !== 10) {
            break;
          }

          message.position = Vector3.decode(reader, reader.uint32());
          continue;
        case 2:
          if (tag !== 18) {
            break;
          }

          message.orientation = Quaternion.decode(reader, reader.uint32());
          continue;
      }
      if ((tag & 7) === 4 || tag === 0) {
        break;
      }
      reader.skipType(tag & 7);
    }
    return message;
  },

  fromJSON(object: any): Pose {
    return {
      $type: Pose.$type,
      position: isSet(object.position) ? Vector3.fromJSON(object.position) : undefined,
      orientation: isSet(object.orientation) ? Quaternion.fromJSON(object.orientation) : undefined,
    };
  },

  toJSON(message: Pose): unknown {
    const obj: any = {};
    if (message.position !== undefined) {
      obj.position = Vector3.toJSON(message.position);
    }
    if (message.orientation !== undefined) {
      obj.orientation = Quaternion.toJSON(message.orientation);
    }
    return obj;
  },

  create<I extends Exact<DeepPartial<Pose>, I>>(base?: I): Pose {
    return Pose.fromPartial(base ?? ({} as any));
  },
  fromPartial<I extends Exact<DeepPartial<Pose>, I>>(object: I): Pose {
    const message = createBasePose();
    message.position = (object.position !== undefined && object.position !== null)
      ? Vector3.fromPartial(object.position)
      : undefined;
    message.orientation = (object.orientation !== undefined && object.orientation !== null)
      ? Quaternion.fromPartial(object.orientation)
      : undefined;
    return message;
  },
};

messageTypeRegistry.set(Pose.$type, Pose);

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
