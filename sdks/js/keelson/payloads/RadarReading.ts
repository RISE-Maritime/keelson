/* eslint-disable */
import _m0 from "protobufjs/minimal";
import { PackedElementField } from "./PackedElementField";
import { Pose } from "./Pose";
import { Timestamp } from "./google/protobuf/timestamp";
import { messageTypeRegistry } from "./typeRegistry";

export interface RadarSpoke {
  $type: "keelson.compound.RadarSpoke";
  /** Timestamp of radar spoke */
  timestamp:
    | Date
    | undefined;
  /** Frame of reference */
  frameId: string;
  /** The origin of the radar spoke relative to the frame of reference */
  pose:
    | Pose
    | undefined;
  /** Azimuth angle [rad] of this spoke */
  azimuth: number;
  /** Range of radar spoke */
  range: number;
  /** Fields in `data`. Generally just one field with the ´intensity´. */
  fields: PackedElementField[];
  /** Intensities */
  data: Uint8Array;
}

export interface RadarSweep {
  $type: "keelson.compound.RadarSweep";
  spokes: RadarSpoke[];
}

function createBaseRadarSpoke(): RadarSpoke {
  return {
    $type: "keelson.compound.RadarSpoke",
    timestamp: undefined,
    frameId: "",
    pose: undefined,
    azimuth: 0,
    range: 0,
    fields: [],
    data: new Uint8Array(0),
  };
}

export const RadarSpoke = {
  $type: "keelson.compound.RadarSpoke" as const,

  encode(message: RadarSpoke, writer: _m0.Writer = _m0.Writer.create()): _m0.Writer {
    if (message.timestamp !== undefined) {
      Timestamp.encode(toTimestamp(message.timestamp), writer.uint32(10).fork()).ldelim();
    }
    if (message.frameId !== "") {
      writer.uint32(18).string(message.frameId);
    }
    if (message.pose !== undefined) {
      Pose.encode(message.pose, writer.uint32(26).fork()).ldelim();
    }
    if (message.azimuth !== 0) {
      writer.uint32(37).float(message.azimuth);
    }
    if (message.range !== 0) {
      writer.uint32(45).float(message.range);
    }
    for (const v of message.fields) {
      PackedElementField.encode(v!, writer.uint32(50).fork()).ldelim();
    }
    if (message.data.length !== 0) {
      writer.uint32(58).bytes(message.data);
    }
    return writer;
  },

  decode(input: _m0.Reader | Uint8Array, length?: number): RadarSpoke {
    const reader = input instanceof _m0.Reader ? input : _m0.Reader.create(input);
    let end = length === undefined ? reader.len : reader.pos + length;
    const message = createBaseRadarSpoke();
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
          if (tag !== 37) {
            break;
          }

          message.azimuth = reader.float();
          continue;
        case 5:
          if (tag !== 45) {
            break;
          }

          message.range = reader.float();
          continue;
        case 6:
          if (tag !== 50) {
            break;
          }

          message.fields.push(PackedElementField.decode(reader, reader.uint32()));
          continue;
        case 7:
          if (tag !== 58) {
            break;
          }

          message.data = reader.bytes();
          continue;
      }
      if ((tag & 7) === 4 || tag === 0) {
        break;
      }
      reader.skipType(tag & 7);
    }
    return message;
  },

  fromJSON(object: any): RadarSpoke {
    return {
      $type: RadarSpoke.$type,
      timestamp: isSet(object.timestamp) ? fromJsonTimestamp(object.timestamp) : undefined,
      frameId: isSet(object.frameId) ? globalThis.String(object.frameId) : "",
      pose: isSet(object.pose) ? Pose.fromJSON(object.pose) : undefined,
      azimuth: isSet(object.azimuth) ? globalThis.Number(object.azimuth) : 0,
      range: isSet(object.range) ? globalThis.Number(object.range) : 0,
      fields: globalThis.Array.isArray(object?.fields)
        ? object.fields.map((e: any) => PackedElementField.fromJSON(e))
        : [],
      data: isSet(object.data) ? bytesFromBase64(object.data) : new Uint8Array(0),
    };
  },

  toJSON(message: RadarSpoke): unknown {
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
    if (message.azimuth !== 0) {
      obj.azimuth = message.azimuth;
    }
    if (message.range !== 0) {
      obj.range = message.range;
    }
    if (message.fields?.length) {
      obj.fields = message.fields.map((e) => PackedElementField.toJSON(e));
    }
    if (message.data.length !== 0) {
      obj.data = base64FromBytes(message.data);
    }
    return obj;
  },

  create<I extends Exact<DeepPartial<RadarSpoke>, I>>(base?: I): RadarSpoke {
    return RadarSpoke.fromPartial(base ?? ({} as any));
  },
  fromPartial<I extends Exact<DeepPartial<RadarSpoke>, I>>(object: I): RadarSpoke {
    const message = createBaseRadarSpoke();
    message.timestamp = object.timestamp ?? undefined;
    message.frameId = object.frameId ?? "";
    message.pose = (object.pose !== undefined && object.pose !== null) ? Pose.fromPartial(object.pose) : undefined;
    message.azimuth = object.azimuth ?? 0;
    message.range = object.range ?? 0;
    message.fields = object.fields?.map((e) => PackedElementField.fromPartial(e)) || [];
    message.data = object.data ?? new Uint8Array(0);
    return message;
  },
};

messageTypeRegistry.set(RadarSpoke.$type, RadarSpoke);

function createBaseRadarSweep(): RadarSweep {
  return { $type: "keelson.compound.RadarSweep", spokes: [] };
}

export const RadarSweep = {
  $type: "keelson.compound.RadarSweep" as const,

  encode(message: RadarSweep, writer: _m0.Writer = _m0.Writer.create()): _m0.Writer {
    for (const v of message.spokes) {
      RadarSpoke.encode(v!, writer.uint32(10).fork()).ldelim();
    }
    return writer;
  },

  decode(input: _m0.Reader | Uint8Array, length?: number): RadarSweep {
    const reader = input instanceof _m0.Reader ? input : _m0.Reader.create(input);
    let end = length === undefined ? reader.len : reader.pos + length;
    const message = createBaseRadarSweep();
    while (reader.pos < end) {
      const tag = reader.uint32();
      switch (tag >>> 3) {
        case 1:
          if (tag !== 10) {
            break;
          }

          message.spokes.push(RadarSpoke.decode(reader, reader.uint32()));
          continue;
      }
      if ((tag & 7) === 4 || tag === 0) {
        break;
      }
      reader.skipType(tag & 7);
    }
    return message;
  },

  fromJSON(object: any): RadarSweep {
    return {
      $type: RadarSweep.$type,
      spokes: globalThis.Array.isArray(object?.spokes) ? object.spokes.map((e: any) => RadarSpoke.fromJSON(e)) : [],
    };
  },

  toJSON(message: RadarSweep): unknown {
    const obj: any = {};
    if (message.spokes?.length) {
      obj.spokes = message.spokes.map((e) => RadarSpoke.toJSON(e));
    }
    return obj;
  },

  create<I extends Exact<DeepPartial<RadarSweep>, I>>(base?: I): RadarSweep {
    return RadarSweep.fromPartial(base ?? ({} as any));
  },
  fromPartial<I extends Exact<DeepPartial<RadarSweep>, I>>(object: I): RadarSweep {
    const message = createBaseRadarSweep();
    message.spokes = object.spokes?.map((e) => RadarSpoke.fromPartial(e)) || [];
    return message;
  },
};

messageTypeRegistry.set(RadarSweep.$type, RadarSweep);

function bytesFromBase64(b64: string): Uint8Array {
  if ((globalThis as any).Buffer) {
    return Uint8Array.from(globalThis.Buffer.from(b64, "base64"));
  } else {
    const bin = globalThis.atob(b64);
    const arr = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; ++i) {
      arr[i] = bin.charCodeAt(i);
    }
    return arr;
  }
}

function base64FromBytes(arr: Uint8Array): string {
  if ((globalThis as any).Buffer) {
    return globalThis.Buffer.from(arr).toString("base64");
  } else {
    const bin: string[] = [];
    arr.forEach((byte) => {
      bin.push(globalThis.String.fromCharCode(byte));
    });
    return globalThis.btoa(bin.join(""));
  }
}

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
