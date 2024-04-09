import {isSubjectWellKnown, getSubjectSchema, getProtobufClassFromTypeName, encodePayloadFromTypeName, decodePayloadFromTypeName, encloseFromTypeName} from './index';
import { Log } from './payloads/Log';

describe("isSubjectWellKnown", () => {
    it("knows when something is well known", () => {
        expect(isSubjectWellKnown("raw")).toBe(true);
    });
    it("knows when something is not well known", () => {
        expect(isSubjectWellKnown("dfsg")).toBe(false);
    });
});

describe("getSubjectSchema", () => {
    it("gets schema", () => {
        expect(getSubjectSchema("compressed_image")).toBe("foxglove.CompressedImage");
    });
    it("returns undefined from incorrect subject", () => {
        expect(getSubjectSchema("dfsg")).toBeUndefined();
    });
});

describe("getProtobufClassFromTypeName", () => {
    it("It finds existing typename", () => {
        const res = getProtobufClassFromTypeName("foxglove.CompressedImage");
        expect(res?.$type).toBe("foxglove.CompressedImage");
        expect(res?.encode).toBeTruthy();
        expect(res?.decode).toBeTruthy();
    });

    it("returns undefined for none-existing typenames", () => {
        const res = getProtobufClassFromTypeName("fsdfdsfs");
        expect(res).toBeUndefined();
    });

    it("requires fully specified typename", () => {
        expect(getProtobufClassFromTypeName("CompressedImage")).toBeUndefined();
    })

    it("is case sensitive", () => {
        expect(getProtobufClassFromTypeName("foxglove.compressedImage")).toBeUndefined();
    })
});

describe("encodePayloadFromTypeName", () => {
    it ("can encode", () => {
        const log = Log.create({level: 1, message: "johan"});
        const res = encodePayloadFromTypeName("foxglove.Log", log);
        
        expect(res).toBeTruthy();
    });

    it ("returns undefined if cannot encode", () => {
        const log = Log.create({level: 1, message: "johan"});
        const res = encodePayloadFromTypeName("fdsfsdfoxglove.Log", log);
        expect(res).toBeFalsy();
    });
})

describe("decodePayloadFromTypeName", () => {
    it("can decode", () => {
        const log = Log.create({level: 1, message: "johan"});
        const encoded = encodePayloadFromTypeName("foxglove.Log", log);
        expect(encoded).toBeTruthy();
      
        const decoded = decodePayloadFromTypeName("foxglove.Log", encoded!);
        expect(decoded).toBeTruthy();
        expect(decoded!["$type"]).toBe("foxglove.Log");
      
        const decodedLog = decoded as Log;
        expect(decodedLog.level).toBe(1);
        expect(decodedLog.message).toBe("johan");
    })
});


describe("encloseFromTypeName", () => {
    it("can enclose stuff", () => {
        const log = Log.create({level: 1, message: "johan"});
        const enclosed = encloseFromTypeName("foxglove.Log", log);
        expect(enclosed).toBeTruthy();
    })
});
