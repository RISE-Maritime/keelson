import { isSubjectWellKnown, getSubjectSchema, getProtobufClassFromTypeName, encodePayloadFromTypeName, decodePayloadFromTypeName, encloseFromTypeName, construct_pubSub_key, parse_pubsub_key, get_subject_from_pubsub_key } from './index';
import { Log } from './payloads/foxglove/Log';

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
        expect(getSubjectSchema("image_compressed")).toBe("foxglove.CompressedImage");
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
    it("can encode", () => {
        const log = Log.create({ level: 1, message: "johan" });
        const res = encodePayloadFromTypeName("foxglove.Log", log);

        expect(res).toBeTruthy();
    });

    it("returns undefined if cannot encode", () => {
        const log = Log.create({ level: 1, message: "johan" });
        const res = encodePayloadFromTypeName("fdsfsdfoxglove.Log", log);
        expect(res).toBeFalsy();
    });

    it("can encode partially defined messages", () => {
        const log = { level: 1 };
        const res = encodePayloadFromTypeName("foxglove.Log", log);
        expect(res).toBeTruthy();
    });
});

describe("decodePayloadFromTypeName", () => {
    it("can decode", () => {
        const log = Log.create({ level: 1, message: "johan" });
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
        const log = Log.create({ level: 1, message: "johan" });
        const enclosed = encloseFromTypeName("foxglove.Log", log);
        expect(enclosed).toBeTruthy();
    })
});

// Tests for pubsub key construction and parsing

describe("construct_pubSub_key", () => {
    it("constructs a basic pubsub key", () => {
        const key = construct_pubSub_key(
            "base_path",
            "entity_id",
            "subject",
            "source_id"
        );
        expect(key).toBe("base_path/@v0/entity_id/pubsub/subject/source_id");
    });

    it("constructs a pubsub key with target_id", () => {
        const key = construct_pubSub_key(
            "keelson",
            "shore_station",
            "heading_true_north_deg",
            "ais",
            "mmsi_245060000"
        );
        expect(key).toBe("keelson/@v0/shore_station/pubsub/heading_true_north_deg/ais/@target/mmsi_245060000");
    });

    it("constructs a key without @target when targetId is undefined", () => {
        const key = construct_pubSub_key(
            "keelson",
            "entity",
            "subject",
            "source",
            undefined
        );
        expect(key).toBe("keelson/@v0/entity/pubsub/subject/source");
        expect(key).not.toContain("@target");
    });

    it("constructs a key with slashed source_id and target_id", () => {
        const key = construct_pubSub_key(
            "keelson",
            "vessel",
            "location_fix",
            "ais/receiver/0",
            "mmsi_123456789"
        );
        expect(key).toBe("keelson/@v0/vessel/pubsub/location_fix/ais/receiver/0/@target/mmsi_123456789");
    });
});

describe("parse_pubsub_key", () => {
    it("parses a basic pubsub key", () => {
        const parsed = parse_pubsub_key("base_path/@v0/entity_id/pubsub/subject/source_id");
        expect(parsed).toEqual({
            base_path: "base_path",
            entityId: "entity_id",
            subject: "subject",
            sourceId: "source_id",
            targetId: null
        });
    });

    it("parses a pubsub key with slashed source_id", () => {
        const parsed = parse_pubsub_key("base_path/@v0/entity_id/pubsub/subject/source_id/sub_id");
        expect(parsed).toEqual({
            base_path: "base_path",
            entityId: "entity_id",
            subject: "subject",
            sourceId: "source_id/sub_id",
            targetId: null
        });
    });

    it("parses a pubsub key with @target extension", () => {
        const parsed = parse_pubsub_key("keelson/@v0/shore_station/pubsub/heading_true_north_deg/ais/@target/mmsi_245060000");
        expect(parsed).toEqual({
            base_path: "keelson",
            entityId: "shore_station",
            subject: "heading_true_north_deg",
            sourceId: "ais",
            targetId: "mmsi_245060000"
        });
    });

    it("parses a pubsub key with slashed source_id and @target extension", () => {
        const parsed = parse_pubsub_key("keelson/@v0/vessel/pubsub/location_fix/ais/receiver/0/@target/mmsi_123456789");
        expect(parsed).toEqual({
            base_path: "keelson",
            entityId: "vessel",
            subject: "location_fix",
            sourceId: "ais/receiver/0",
            targetId: "mmsi_123456789"
        });
    });
});

describe("pubsub key roundtrip", () => {
    it("roundtrips a key with target_id", () => {
        const originalKey = construct_pubSub_key(
            "keelson",
            "shore_station",
            "speed_over_ground_knots",
            "ais/receiver",
            "mmsi_987654321"
        );
        const parsed = parse_pubsub_key(originalKey);
        const reconstructedKey = construct_pubSub_key(
            parsed.base_path,
            parsed.entityId,
            parsed.subject,
            parsed.sourceId,
            parsed.targetId ?? undefined
        );
        expect(originalKey).toBe(reconstructedKey);
    });

    it("roundtrips a key without target_id", () => {
        const originalKey = construct_pubSub_key(
            "keelson",
            "landkrabban",
            "location_fix",
            "gnss/0"
        );
        const parsed = parse_pubsub_key(originalKey);
        const reconstructedKey = construct_pubSub_key(
            parsed.base_path,
            parsed.entityId,
            parsed.subject,
            parsed.sourceId,
            parsed.targetId ?? undefined
        );
        expect(originalKey).toBe(reconstructedKey);
    });
});

describe("get_subject_from_pubsub_key", () => {
    it("extracts subject from key without @target", () => {
        const subject = get_subject_from_pubsub_key("keelson/@v0/entity/pubsub/heading_true_north_deg/source");
        expect(subject).toBe("heading_true_north_deg");
    });

    it("extracts subject from key with @target", () => {
        const subject = get_subject_from_pubsub_key("keelson/@v0/shore_station/pubsub/heading_true_north_deg/ais/@target/mmsi_245060000");
        expect(subject).toBe("heading_true_north_deg");
    });
});
