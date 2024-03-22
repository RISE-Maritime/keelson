/* eslint-disable */
import _m0 from "protobufjs/minimal";
import { Pose } from "./Pose";
import { Timestamp } from "./google/protobuf/timestamp";
import { messageTypeRegistry } from "./typeRegistry";

/** A single scan from a planar laser range-finder */
export interface LaserScan {
  $type: "foxglove.LaserScan";
  /** Timestamp of scan */
  timestamp:
    | Date
    | undefined;
  /** Frame of reference */
  frameId: string;
  /** Origin of scan relative to frame of reference; points are positioned in the x-y plane relative to this origin; angles are interpreted as counterclockwise rotations around the z axis with 0 rad being in the +x direction */
  pose:
    | Pose
    | undefined;
  /** Bearing of first point, in radians */
  startAngle: number;
  /** Bearing of last point, in radians */
  endAngle: number;
  /** Distance of detections from origin; assumed to be at equally-spaced angles between `start_angle` and `end_angle` */
  ranges: number[];
  /** Intensity of detections */
  intensities: number[];
}

function createBaseLaserScan(): LaserScan {
  return {
    $type: "foxglove.LaserScan",
    timestamp: undefined,
    frameId: "",
    pose: undefined,
    startAngle: 0,
    endAngle: 0,
    ranges: [],
    intensities: [],
  };
}

export const LaserScan = {
  $type: "foxglove.LaserScan" as const,

  encode(message: LaserScan, writer: _m0.Writer = _m0.Writer.create()): _m0.Writer {
    if (message.timestamp !== undefined) {
      Timestamp.encode(toTimestamp(message.timestamp), writer.uint32(10).fork()).ldelim();
    }
    if (message.frameId !== "") {
      writer.uint32(18).string(message.frameId);
    }
    if (message.pose !== undefined) {
      Pose.encode(message.pose, writer.uint32(26).fork()).ldelim();
    }
    if (message.startAngle !== 0) {
      writer.uint32(33).double(message.startAngle);
    }
    if (message.endAngle !== 0) {
      writer.uint32(41).double(message.endAngle);
    }
    writer.uint32(50).fork();
    for (const v of message.ranges) {
      writer.double(v);
    }
    writer.ldelim();
    writer.uint32(58).fork();
    for (const v of message.intensities) {
      writer.double(v);
    }
    writer.ldelim();
    return writer;
  },

  decode(input: _m0.Reader | Uint8Array, length?: number): LaserScan {
    const reader = input instanceof _m0.Reader ? input : _m0.Reader.create(input);
    let end = length === undefined ? reader.len : reader.pos + length;
    const message = createBaseLaserScan();
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
        case 4:
          if (tag !== 33) {
            break;
          }

          message.startAngle = reader.double();
          continue;
        case 5:
          if (tag !== 41) {
            break;
          }

          message.endAngle = reader.double();
          continue;
        case 6:
          if (tag === 49) {
            message.ranges.push(reader.double());

            continue;
          }

          if (tag === 50) {
            const end2 = reader.uint32() + reader.pos;
            while (reader.pos < end2) {
              message.ranges.push(reader.double());
            }

            continue;
          }

          break;
        case 7:
          if (tag === 57) {
            message.intensities.push(reader.double());

            continue;
          }

          if (tag === 58) {
            const end2 = reader.uint32() + reader.pos;
            while (reader.pos < end2) {
              message.intensities.push(reader.double());
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

  fromJSON(object: any): LaserScan {
    return {
      $type: LaserScan.$type,
      timestamp: isSet(object.timestamp) ? fromJsonTimestamp(object.timestamp) : undefined,
      frameId: isSet(object.frameId) ? globalThis.String(object.frameId) : "",
      pose: isSet(object.pose) ? Pose.fromJSON(object.pose) : undefined,
      startAngle: isSet(object.startAngle) ? globalThis.Number(object.startAngle) : 0,
      endAngle: isSet(object.endAngle) ? globalThis.Number(object.endAngle) : 0,
      ranges: globalThis.Array.isArray(object?.ranges) ? object.ranges.map((e: any) => globalThis.Number(e)) : [],
      intensities: globalThis.Array.isArray(object?.intensities)
        ? object.intensities.map((e: any) => globalThis.Number(e))
        : [],
    };
  },

  toJSON(message: LaserScan): unknown {
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
    if (message.startAngle !== 0) {
      obj.startAngle = message.startAngle;
    }
    if (message.endAngle !== 0) {
      obj.endAngle = message.endAngle;
    }
    if (message.ranges?.length) {
      obj.ranges = message.ranges;
    }
    if (message.intensities?.length) {
      obj.intensities = message.intensities;
    }
    return obj;
  },

  create<I extends Exact<DeepPartial<LaserScan>, I>>(base?: I): LaserScan {
    return LaserScan.fromPartial(base ?? ({} as any));
  },
  fromPartial<I extends Exact<DeepPartial<LaserScan>, I>>(object: I): LaserScan {
    const message = createBaseLaserScan();
    message.timestamp = object.timestamp ?? undefined;
    message.frameId = object.frameId ?? "";
    message.pose = (object.pose !== undefined && object.pose !== null) ? Pose.fromPartial(object.pose) : undefined;
    message.startAngle = object.startAngle ?? 0;
    message.endAngle = object.endAngle ?? 0;
    message.ranges = object.ranges?.map((e) => e) || [];
    message.intensities = object.intensities?.map((e) => e) || [];
    return message;
  },
};

messageTypeRegistry.set(LaserScan.$type, LaserScan);

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
