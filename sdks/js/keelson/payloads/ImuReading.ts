/* eslint-disable */
import _m0 from "protobufjs/minimal";
import { Quaternion } from "./Quaternion";
import { Vector3 } from "./Vector3";
import { Timestamp } from "./google/protobuf/timestamp";
import { messageTypeRegistry } from "./typeRegistry";

export interface ImuReading {
  $type: "keelson.compound.ImuReading";
  timestamp: Date | undefined;
  frameId: string;
  orientation:
    | Quaternion
    | undefined;
  /** array with 9 elements, row major */
  orientationCovariance: number[];
  angularVelocity:
    | Vector3
    | undefined;
  /** array with 9 elements, row major */
  angularVelocityCovariance: number[];
  linearAcceleration:
    | Vector3
    | undefined;
  /** array with 9 elements, row major */
  linearAccelerationCovariance: number[];
}

function createBaseImuReading(): ImuReading {
  return {
    $type: "keelson.compound.ImuReading",
    timestamp: undefined,
    frameId: "",
    orientation: undefined,
    orientationCovariance: [],
    angularVelocity: undefined,
    angularVelocityCovariance: [],
    linearAcceleration: undefined,
    linearAccelerationCovariance: [],
  };
}

export const ImuReading = {
  $type: "keelson.compound.ImuReading" as const,

  encode(message: ImuReading, writer: _m0.Writer = _m0.Writer.create()): _m0.Writer {
    if (message.timestamp !== undefined) {
      Timestamp.encode(toTimestamp(message.timestamp), writer.uint32(10).fork()).ldelim();
    }
    if (message.frameId !== "") {
      writer.uint32(18).string(message.frameId);
    }
    if (message.orientation !== undefined) {
      Quaternion.encode(message.orientation, writer.uint32(26).fork()).ldelim();
    }
    writer.uint32(34).fork();
    for (const v of message.orientationCovariance) {
      writer.double(v);
    }
    writer.ldelim();
    if (message.angularVelocity !== undefined) {
      Vector3.encode(message.angularVelocity, writer.uint32(42).fork()).ldelim();
    }
    writer.uint32(50).fork();
    for (const v of message.angularVelocityCovariance) {
      writer.double(v);
    }
    writer.ldelim();
    if (message.linearAcceleration !== undefined) {
      Vector3.encode(message.linearAcceleration, writer.uint32(58).fork()).ldelim();
    }
    writer.uint32(66).fork();
    for (const v of message.linearAccelerationCovariance) {
      writer.double(v);
    }
    writer.ldelim();
    return writer;
  },

  decode(input: _m0.Reader | Uint8Array, length?: number): ImuReading {
    const reader = input instanceof _m0.Reader ? input : _m0.Reader.create(input);
    let end = length === undefined ? reader.len : reader.pos + length;
    const message = createBaseImuReading();
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

          message.frameId = reader.string();
          continue;
        case 3:
          if (tag !== 26) {
            break;
          }

          message.orientation = Quaternion.decode(reader, reader.uint32());
          continue;
        case 4:
          if (tag === 33) {
            message.orientationCovariance.push(reader.double());

            continue;
          }

          if (tag === 34) {
            const end2 = reader.uint32() + reader.pos;
            while (reader.pos < end2) {
              message.orientationCovariance.push(reader.double());
            }

            continue;
          }

          break;
        case 5:
          if (tag !== 42) {
            break;
          }

          message.angularVelocity = Vector3.decode(reader, reader.uint32());
          continue;
        case 6:
          if (tag === 49) {
            message.angularVelocityCovariance.push(reader.double());

            continue;
          }

          if (tag === 50) {
            const end2 = reader.uint32() + reader.pos;
            while (reader.pos < end2) {
              message.angularVelocityCovariance.push(reader.double());
            }

            continue;
          }

          break;
        case 7:
          if (tag !== 58) {
            break;
          }

          message.linearAcceleration = Vector3.decode(reader, reader.uint32());
          continue;
        case 8:
          if (tag === 65) {
            message.linearAccelerationCovariance.push(reader.double());

            continue;
          }

          if (tag === 66) {
            const end2 = reader.uint32() + reader.pos;
            while (reader.pos < end2) {
              message.linearAccelerationCovariance.push(reader.double());
            }

            continue;
          }

          break;
      }
      if ((tag & 7) === 4 || tag === 0) {
        break;
      }
      reader.skipType(tag & 7);
    }
    return message;
  },

  fromJSON(object: any): ImuReading {
    return {
      $type: ImuReading.$type,
      timestamp: isSet(object.timestamp) ? fromJsonTimestamp(object.timestamp) : undefined,
      frameId: isSet(object.frameId) ? globalThis.String(object.frameId) : "",
      orientation: isSet(object.orientation) ? Quaternion.fromJSON(object.orientation) : undefined,
      orientationCovariance: globalThis.Array.isArray(object?.orientationCovariance)
        ? object.orientationCovariance.map((e: any) => globalThis.Number(e))
        : [],
      angularVelocity: isSet(object.angularVelocity) ? Vector3.fromJSON(object.angularVelocity) : undefined,
      angularVelocityCovariance: globalThis.Array.isArray(object?.angularVelocityCovariance)
        ? object.angularVelocityCovariance.map((e: any) => globalThis.Number(e))
        : [],
      linearAcceleration: isSet(object.linearAcceleration) ? Vector3.fromJSON(object.linearAcceleration) : undefined,
      linearAccelerationCovariance: globalThis.Array.isArray(object?.linearAccelerationCovariance)
        ? object.linearAccelerationCovariance.map((e: any) => globalThis.Number(e))
        : [],
    };
  },

  toJSON(message: ImuReading): unknown {
    const obj: any = {};
    if (message.timestamp !== undefined) {
      obj.timestamp = message.timestamp.toISOString();
    }
    if (message.frameId !== "") {
      obj.frameId = message.frameId;
    }
    if (message.orientation !== undefined) {
      obj.orientation = Quaternion.toJSON(message.orientation);
    }
    if (message.orientationCovariance?.length) {
      obj.orientationCovariance = message.orientationCovariance;
    }
    if (message.angularVelocity !== undefined) {
      obj.angularVelocity = Vector3.toJSON(message.angularVelocity);
    }
    if (message.angularVelocityCovariance?.length) {
      obj.angularVelocityCovariance = message.angularVelocityCovariance;
    }
    if (message.linearAcceleration !== undefined) {
      obj.linearAcceleration = Vector3.toJSON(message.linearAcceleration);
    }
    if (message.linearAccelerationCovariance?.length) {
      obj.linearAccelerationCovariance = message.linearAccelerationCovariance;
    }
    return obj;
  },

  create<I extends Exact<DeepPartial<ImuReading>, I>>(base?: I): ImuReading {
    return ImuReading.fromPartial(base ?? ({} as any));
  },
  fromPartial<I extends Exact<DeepPartial<ImuReading>, I>>(object: I): ImuReading {
    const message = createBaseImuReading();
    message.timestamp = object.timestamp ?? undefined;
    message.frameId = object.frameId ?? "";
    message.orientation = (object.orientation !== undefined && object.orientation !== null)
      ? Quaternion.fromPartial(object.orientation)
      : undefined;
    message.orientationCovariance = object.orientationCovariance?.map((e) => e) || [];
    message.angularVelocity = (object.angularVelocity !== undefined && object.angularVelocity !== null)
      ? Vector3.fromPartial(object.angularVelocity)
      : undefined;
    message.angularVelocityCovariance = object.angularVelocityCovariance?.map((e) => e) || [];
    message.linearAcceleration = (object.linearAcceleration !== undefined && object.linearAcceleration !== null)
      ? Vector3.fromPartial(object.linearAcceleration)
      : undefined;
    message.linearAccelerationCovariance = object.linearAccelerationCovariance?.map((e) => e) || [];
    return message;
  },
};

messageTypeRegistry.set(ImuReading.$type, ImuReading);

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
