/* eslint-disable */
import _m0 from "protobufjs/minimal";
import { FrameTransform } from "./FrameTransform";
import { messageTypeRegistry } from "./typeRegistry";

/** An array of FrameTransform messages */
export interface FrameTransforms {
  $type: "foxglove.FrameTransforms";
  /** Array of transforms */
  transforms: FrameTransform[];
}

function createBaseFrameTransforms(): FrameTransforms {
  return { $type: "foxglove.FrameTransforms", transforms: [] };
}

export const FrameTransforms = {
  $type: "foxglove.FrameTransforms" as const,

  encode(message: FrameTransforms, writer: _m0.Writer = _m0.Writer.create()): _m0.Writer {
    for (const v of message.transforms) {
      FrameTransform.encode(v!, writer.uint32(10).fork()).ldelim();
    }
    return writer;
  },

  decode(input: _m0.Reader | Uint8Array, length?: number): FrameTransforms {
    const reader = input instanceof _m0.Reader ? input : _m0.Reader.create(input);
    let end = length === undefined ? reader.len : reader.pos + length;
    const message = createBaseFrameTransforms();
    while (reader.pos < end) {
      const tag = reader.uint32();
      switch (tag >>> 3) {
        case 1:
          if (tag !== 10) {
            break;
          }

          message.transforms.push(FrameTransform.decode(reader, reader.uint32()));
          continue;
      }
      if ((tag & 7) === 4 || tag === 0) {
        break;
      }
      reader.skipType(tag & 7);
    }
    return message;
  },

  fromJSON(object: any): FrameTransforms {
    return {
      $type: FrameTransforms.$type,
      transforms: globalThis.Array.isArray(object?.transforms)
        ? object.transforms.map((e: any) => FrameTransform.fromJSON(e))
        : [],
    };
  },

  toJSON(message: FrameTransforms): unknown {
    const obj: any = {};
    if (message.transforms?.length) {
      obj.transforms = message.transforms.map((e) => FrameTransform.toJSON(e));
    }
    return obj;
  },

  create<I extends Exact<DeepPartial<FrameTransforms>, I>>(base?: I): FrameTransforms {
    return FrameTransforms.fromPartial(base ?? ({} as any));
  },
  fromPartial<I extends Exact<DeepPartial<FrameTransforms>, I>>(object: I): FrameTransforms {
    const message = createBaseFrameTransforms();
    message.transforms = object.transforms?.map((e) => FrameTransform.fromPartial(e)) || [];
    return message;
  },
};

messageTypeRegistry.set(FrameTransforms.$type, FrameTransforms);

type Builtin = Date | Function | Uint8Array | string | number | boolean | undefined;

type DeepPartial<T> = T extends Builtin ? T
  : T extends globalThis.Array<infer U> ? globalThis.Array<DeepPartial<U>>
  : T extends ReadonlyArray<infer U> ? ReadonlyArray<DeepPartial<U>>
  : T extends {} ? { [K in Exclude<keyof T, "$type">]?: DeepPartial<T[K]> }
  : Partial<T>;

type KeysOfUnion<T> = T extends T ? keyof T : never;
type Exact<P, I extends P> = P extends Builtin ? P
  : P & { [K in keyof P]: Exact<P[K], I[K]> } & { [K in Exclude<keyof I, KeysOfUnion<P> | "$type">]: never };
