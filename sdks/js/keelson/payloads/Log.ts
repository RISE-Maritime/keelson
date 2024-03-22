/* eslint-disable */
import _m0 from "protobufjs/minimal";
import { Timestamp } from "./google/protobuf/timestamp";
import { messageTypeRegistry } from "./typeRegistry";

/** A log message */
export interface Log {
  $type: "foxglove.Log";
  /** Timestamp of log message */
  timestamp:
    | Date
    | undefined;
  /** Log level */
  level: Log_Level;
  /** Log message */
  message: string;
  /** Process or node name */
  name: string;
  /** Filename */
  file: string;
  /** Line number in the file */
  line: number;
}

/** Log level */
export enum Log_Level {
  UNKNOWN = 0,
  DEBUG = 1,
  INFO = 2,
  WARNING = 3,
  ERROR = 4,
  FATAL = 5,
  UNRECOGNIZED = -1,
}

export function log_LevelFromJSON(object: any): Log_Level {
  switch (object) {
    case 0:
    case "UNKNOWN":
      return Log_Level.UNKNOWN;
    case 1:
    case "DEBUG":
      return Log_Level.DEBUG;
    case 2:
    case "INFO":
      return Log_Level.INFO;
    case 3:
    case "WARNING":
      return Log_Level.WARNING;
    case 4:
    case "ERROR":
      return Log_Level.ERROR;
    case 5:
    case "FATAL":
      return Log_Level.FATAL;
    case -1:
    case "UNRECOGNIZED":
    default:
      return Log_Level.UNRECOGNIZED;
  }
}

export function log_LevelToJSON(object: Log_Level): string {
  switch (object) {
    case Log_Level.UNKNOWN:
      return "UNKNOWN";
    case Log_Level.DEBUG:
      return "DEBUG";
    case Log_Level.INFO:
      return "INFO";
    case Log_Level.WARNING:
      return "WARNING";
    case Log_Level.ERROR:
      return "ERROR";
    case Log_Level.FATAL:
      return "FATAL";
    case Log_Level.UNRECOGNIZED:
    default:
      return "UNRECOGNIZED";
  }
}

function createBaseLog(): Log {
  return { $type: "foxglove.Log", timestamp: undefined, level: 0, message: "", name: "", file: "", line: 0 };
}

export const Log = {
  $type: "foxglove.Log" as const,

  encode(message: Log, writer: _m0.Writer = _m0.Writer.create()): _m0.Writer {
    if (message.timestamp !== undefined) {
      Timestamp.encode(toTimestamp(message.timestamp), writer.uint32(10).fork()).ldelim();
    }
    if (message.level !== 0) {
      writer.uint32(16).int32(message.level);
    }
    if (message.message !== "") {
      writer.uint32(26).string(message.message);
    }
    if (message.name !== "") {
      writer.uint32(34).string(message.name);
    }
    if (message.file !== "") {
      writer.uint32(42).string(message.file);
    }
    if (message.line !== 0) {
      writer.uint32(53).fixed32(message.line);
    }
    return writer;
  },

  decode(input: _m0.Reader | Uint8Array, length?: number): Log {
    const reader = input instanceof _m0.Reader ? input : _m0.Reader.create(input);
    let end = length === undefined ? reader.len : reader.pos + length;
    const message = createBaseLog();
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
          if (tag !== 16) {
            break;
          }

          message.level = reader.int32() as any;
          continue;
        case 3:
          if (tag !== 26) {
            break;
          }

          message.message = reader.string();
          continue;
        case 4:
          if (tag !== 34) {
            break;
          }

          message.name = reader.string();
          continue;
        case 5:
          if (tag !== 42) {
            break;
          }

          message.file = reader.string();
          continue;
        case 6:
          if (tag !== 53) {
            break;
          }

          message.line = reader.fixed32();
          continue;
      }
      if ((tag & 7) === 4 || tag === 0) {
        break;
      }
      reader.skipType(tag & 7);
    }
    return message;
  },

  fromJSON(object: any): Log {
    return {
      $type: Log.$type,
      timestamp: isSet(object.timestamp) ? fromJsonTimestamp(object.timestamp) : undefined,
      level: isSet(object.level) ? log_LevelFromJSON(object.level) : 0,
      message: isSet(object.message) ? globalThis.String(object.message) : "",
      name: isSet(object.name) ? globalThis.String(object.name) : "",
      file: isSet(object.file) ? globalThis.String(object.file) : "",
      line: isSet(object.line) ? globalThis.Number(object.line) : 0,
    };
  },

  toJSON(message: Log): unknown {
    const obj: any = {};
    if (message.timestamp !== undefined) {
      obj.timestamp = message.timestamp.toISOString();
    }
    if (message.level !== 0) {
      obj.level = log_LevelToJSON(message.level);
    }
    if (message.message !== "") {
      obj.message = message.message;
    }
    if (message.name !== "") {
      obj.name = message.name;
    }
    if (message.file !== "") {
      obj.file = message.file;
    }
    if (message.line !== 0) {
      obj.line = Math.round(message.line);
    }
    return obj;
  },

  create<I extends Exact<DeepPartial<Log>, I>>(base?: I): Log {
    return Log.fromPartial(base ?? ({} as any));
  },
  fromPartial<I extends Exact<DeepPartial<Log>, I>>(object: I): Log {
    const message = createBaseLog();
    message.timestamp = object.timestamp ?? undefined;
    message.level = object.level ?? 0;
    message.message = object.message ?? "";
    message.name = object.name ?? "";
    message.file = object.file ?? "";
    message.line = object.line ?? 0;
    return message;
  },
};

messageTypeRegistry.set(Log.$type, Log);

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
