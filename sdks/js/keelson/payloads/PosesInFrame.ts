/* eslint-disable */
import _m0 from "protobufjs/minimal";
import { Pose } from "./Pose";
import { Timestamp } from "./google/protobuf/timestamp";
import { messageTypeRegistry } from "./typeRegistry";

/** An array of timestamped poses for an object or reference frame in 3D space */
export interface PosesInFrame {
  $type: "foxglove.PosesInFrame";
  /** Timestamp of pose */
  timestamp:
    | Date
    | undefined;
  /** Frame of reference for pose position and orientation */
  frameId: string;
  /** Poses in 3D space */
  poses: Pose[];
}

function createBasePosesInFrame(): PosesInFrame {
  return { $type: "foxglove.PosesInFrame", timestamp: undefined, frameId: "", poses: [] };
}

export const PosesInFrame = {
  $type: "foxglove.PosesInFrame" as const,

  encode(message: PosesInFrame, writer: _m0.Writer = _m0.Writer.create()): _m0.Writer {
    if (message.timestamp !== undefined) {
      Timestamp.encode(toTimestamp(message.timestamp), writer.uint32(10).fork()).ldelim();
    }
    if (message.frameId !== "") {
      writer.uint32(18).string(message.frameId);
    }
    for (const v of message.poses) {
      Pose.encode(v!, writer.uint32(26).fork()).ldelim();
    }
    return writer;
  },

  decode(input: _m0.Reader | Uint8Array, length?: number): PosesInFrame {
    const reader = input instanceof _m0.Reader ? input : _m0.Reader.create(input);
    let end = length === undefined ? reader.len : reader.pos + length;
    const message = createBasePosesInFrame();
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

          message.poses.push(Pose.decode(reader, reader.uint32()));
          continue;
      }
      if ((tag & 7) === 4 || tag === 0) {
        break;
      }
      reader.skipType(tag & 7);
    }
    return message;
  },

  fromJSON(object: any): PosesInFrame {
    return {
      $type: PosesInFrame.$type,
      timestamp: isSet(object.timestamp) ? fromJsonTimestamp(object.timestamp) : undefined,
      frameId: isSet(object.frameId) ? globalThis.String(object.frameId) : "",
      poses: globalThis.Array.isArray(object?.poses) ? object.poses.map((e: any) => Pose.fromJSON(e)) : [],
    };
  },

  toJSON(message: PosesInFrame): unknown {
    const obj: any = {};
    if (message.timestamp !== undefined) {
      obj.timestamp = message.timestamp.toISOString();
    }
    if (message.frameId !== "") {
      obj.frameId = message.frameId;
    }
    if (message.poses?.length) {
      obj.poses = message.poses.map((e) => Pose.toJSON(e));
    }
    return obj;
  },

  create<I extends Exact<DeepPartial<PosesInFrame>, I>>(base?: I): PosesInFrame {
    return PosesInFrame.fromPartial(base ?? ({} as any));
  },
  fromPartial<I extends Exact<DeepPartial<PosesInFrame>, I>>(object: I): PosesInFrame {
    const message = createBasePosesInFrame();
    message.timestamp = object.timestamp ?? undefined;
    message.frameId = object.frameId ?? "";
    message.poses = object.poses?.map((e) => Pose.fromPartial(e)) || [];
    return message;
  },
};

messageTypeRegistry.set(PosesInFrame.$type, PosesInFrame);

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
