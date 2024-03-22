/* eslint-disable */
import _m0 from "protobufjs/minimal";
import { Pose } from "./Pose";
import { Timestamp } from "./google/protobuf/timestamp";
import { messageTypeRegistry } from "./typeRegistry";

/** A timestamped pose for an object or reference frame in 3D space */
export interface PoseInFrame {
  $type: "foxglove.PoseInFrame";
  /** Timestamp of pose */
  timestamp:
    | Date
    | undefined;
  /** Frame of reference for pose position and orientation */
  frameId: string;
  /** Pose in 3D space */
  pose: Pose | undefined;
}

function createBasePoseInFrame(): PoseInFrame {
  return { $type: "foxglove.PoseInFrame", timestamp: undefined, frameId: "", pose: undefined };
}

export const PoseInFrame = {
  $type: "foxglove.PoseInFrame" as const,

  encode(message: PoseInFrame, writer: _m0.Writer = _m0.Writer.create()): _m0.Writer {
    if (message.timestamp !== undefined) {
      Timestamp.encode(toTimestamp(message.timestamp), writer.uint32(10).fork()).ldelim();
    }
    if (message.frameId !== "") {
      writer.uint32(18).string(message.frameId);
    }
    if (message.pose !== undefined) {
      Pose.encode(message.pose, writer.uint32(26).fork()).ldelim();
    }
    return writer;
  },

  decode(input: _m0.Reader | Uint8Array, length?: number): PoseInFrame {
    const reader = input instanceof _m0.Reader ? input : _m0.Reader.create(input);
    let end = length === undefined ? reader.len : reader.pos + length;
    const message = createBasePoseInFrame();
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

          message.pose = Pose.decode(reader, reader.uint32());
          continue;
      }
      if ((tag & 7) === 4 || tag === 0) {
        break;
      }
      reader.skipType(tag & 7);
    }
    return message;
  },

  fromJSON(object: any): PoseInFrame {
    return {
      $type: PoseInFrame.$type,
      timestamp: isSet(object.timestamp) ? fromJsonTimestamp(object.timestamp) : undefined,
      frameId: isSet(object.frameId) ? globalThis.String(object.frameId) : "",
      pose: isSet(object.pose) ? Pose.fromJSON(object.pose) : undefined,
    };
  },

  toJSON(message: PoseInFrame): unknown {
    const obj: any = {};
    if (message.timestamp !== undefined) {
      obj.timestamp = message.timestamp.toISOString();
    }
    if (message.frameId !== "") {
      obj.frameId = message.frameId;
    }
    if (message.pose !== undefined) {
      obj.pose = Pose.toJSON(message.pose);
    }
    return obj;
  },

  create<I extends Exact<DeepPartial<PoseInFrame>, I>>(base?: I): PoseInFrame {
    return PoseInFrame.fromPartial(base ?? ({} as any));
  },
  fromPartial<I extends Exact<DeepPartial<PoseInFrame>, I>>(object: I): PoseInFrame {
    const message = createBasePoseInFrame();
    message.timestamp = object.timestamp ?? undefined;
    message.frameId = object.frameId ?? "";
    message.pose = (object.pose !== undefined && object.pose !== null) ? Pose.fromPartial(object.pose) : undefined;
    return message;
  },
};

messageTypeRegistry.set(PoseInFrame.$type, PoseInFrame);

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
