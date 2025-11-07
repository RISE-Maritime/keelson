import { describe, it, expect, vi, beforeEach } from "vitest";

// Simple local waitFor (polls until the assertion passes or times out)
async function waitForAssert(
    fn: () => void | Promise<void>,
    timeoutMs = 1000,
    intervalMs = 10
) {
    const start = Date.now();
    // Keep the last error so we can throw it on timeout
    let lastErr: unknown;
    while (Date.now() - start < timeoutMs) {
        try {
            await fn();
            return;
        } catch (err) {
            lastErr = err;
            await new Promise((r) => setTimeout(r, intervalMs));
        }
    }
    throw lastErr instanceof Error ? lastErr : new Error(String(lastErr));
}

// --- HOISTED CONSTANTS (safe to use inside vi.mock factories) ---
const h = vi.hoisted(() => {
    const enc = (o: any) => new TextEncoder().encode(JSON.stringify(o));
    const dec = (b: Uint8Array) => JSON.parse(new TextDecoder().decode(b));
    const PoseCtor = {
        $type: "keelson.payloads.nav.Pose",
        encode: (msg: any) => ({ finish: () => enc(msg) }),
        decode: (bytes: Uint8Array) => dec(bytes),
    };
    return { enc, dec, PoseCtor };
});

// -------------------- Mocks --------------------

function makeAsyncQueue<T>() {
    const q: T[] = [];
    let notify: (() => void) | null = null;
    return {
        push(item: T) {
            q.push(item);
            if (notify) { notify(); notify = null; }
        },
        async *stream() {
            while (true) {
                if (q.length === 0) {
                    await new Promise<void>((r) => (notify = r));
                }
                yield q.shift() as T;
            }
        },
    };
}

const openSpy = vi.fn();
const sessionOpenSpy = vi.fn();
const published: Uint8Array[] = [];
const subQueues = new Map<string, ReturnType<typeof makeAsyncQueue>>();

vi.mock("@eclipse-zenoh/zenoh-ts", () => {
    class MockPublisher {
        constructor(public topic: string) { }
        async put(payload: Uint8Array) { published.push(payload); }
        async undeclare() { }
    }
    class MockSubscriber {
        q = makeAsyncQueue<{ payload: Uint8Array | string }>();
        constructor(public topic: string) { subQueues.set(topic, this.q); }
        async *stream() { yield* this.q.stream(); }
        async undeclare() { }
    }
    class MockSession {
        async declarePublisher(topic: string) { return new MockPublisher(topic); }
        async declareSubscriber(topic: string) { return new MockSubscriber(topic); }
        async close() { }
    }
    return {
        open: vi.fn(async (cfg: any) => { openSpy(cfg); return new MockSession(); }),
        Session: {
            open: vi.fn(async (cfg: any) => { sessionOpenSpy(cfg); return new MockSession(); }),
        },
    };
});

// IMPORTANT: mock IDs must match how KeelsonClient imports them (from /src)
vi.mock("../src/keelson/Envelope", () => {
    // Turn Uint8Array into a plain number[] for JSON
    const normalize = (env: any) => {
        const out = { ...env };
        if (out.payload && out.payload.value instanceof Uint8Array) {
            out.payload = { ...out.payload, value: Array.from(out.payload.value) };
        }
        return out;
    };

    // On decode, revive number[] back to Uint8Array
    const revive = (env: any) => {
        const out = { ...env };
        if (out.payload && Array.isArray(out.payload.value)) {
            out.payload = { ...out.payload, value: new Uint8Array(out.payload.value) };
        }
        return out;
    };

    const enc = (o: any) => new TextEncoder().encode(JSON.stringify(o));
    const dec = (b: Uint8Array) => JSON.parse(new TextDecoder().decode(b));

    return {
        Envelope: {
            encode: (env: any) => ({ finish: () => enc(normalize(env)) }),
            decode: (bytes: Uint8Array) => revive(dec(bytes)),
        },
    };
});

vi.mock("../src/keelson/google/protobuf/any", () => ({ Any: {} }));

vi.mock("../src/keelson/payloads", () => {
    const map = new Map<string, any>([[h.PoseCtor.$type, h.PoseCtor]]);
    return { messageTypeRegistry: map };
});

vi.mock(
    "../src/keelson/subjects.json",
    () => ({ default: { "nav/pose": "keelson.payloads.nav.Pose" } }),
    { virtual: true }
);

// -------------------- Under test --------------------
import { KeelsonClient } from "../src/keelson/KeelsonClient";

function decodeLastPublished(): any {
    const last = published[published.length - 1];
    return JSON.parse(new TextDecoder().decode(last));
}

// -------------------- Tests --------------------
beforeEach(() => {
    published.length = 0;
    subQueues.clear();
    openSpy.mockClear();
    sessionOpenSpy.mockClear();
});

describe("KeelsonClient", () => {
    it("opens a session using any available zenoh-ts API", async () => {
        const kc = new KeelsonClient({ locator: "ws://localhost:10000" });
        await kc.connect();
        expect(openSpy.mock.calls.length + sessionOpenSpy.mock.calls.length).toBeGreaterThan(0);
    });

    it("publishes Envelope[Any] with correct typeUrl and payload", async () => {
        const kc = new KeelsonClient();
        await kc.connect();

        await kc.publish("nav/pose", { x: 1, y: 2, z: 3 });

        const env = decodeLastPublished();
        expect(env.subject).toBe("nav/pose");
        expect(env.payload.typeUrl).toBe("type.googleapis.com/keelson.payloads.nav.Pose");

        const decodedPose = JSON.parse(new TextDecoder().decode(new Uint8Array(env.payload.value)));
        expect(decodedPose).toEqual({ x: 1, y: 2, z: 3 });
    });

    it("throws when publishing without a mapping or explicit typeName", async () => {
        const kc = new KeelsonClient();
        await kc.connect();
        await expect(kc.publish("unknown/subject", { foo: 1 }))
            .rejects.toThrow(/No protobuf type mapping/);
    });

    it("subscribes and decodes payload, invoking handler", async () => {
        const { Envelope } = await import("../src/keelson/Envelope");
        const kc = new KeelsonClient();
        await kc.connect();

        const handler = vi.fn();
        await kc.subscribe("nav/pose", handler);

        const payloadBytes = h.PoseCtor.encode({ x: 9, y: 8, z: 7 }).finish();
        const envMock = {
            subject: "nav/pose",
            payload: {
                typeUrl: "type.googleapis.com/keelson.payloads.nav.Pose",
                value: payloadBytes, // Uint8Array here is OK — Envelope.encode will normalize it
            },
        };

        // IMPORTANT: encode the envelope via the mocked Envelope.encode
        const bytesForSub = Envelope.encode(envMock).finish();

        // push bytes into the mock subscriber queue
        const q = subQueues.get("nav/pose")!;
        q.push({ payload: bytesForSub });

        // give the async loop a moment — prefer waitFor over setTimeout
        await waitForAssert(() => {
            expect(handler).toHaveBeenCalledWith({ x: 9, y: 8, z: 7 });
        });

    });

    it("skips messages with unknown type", async () => {
        const warnSpy = vi.spyOn(console, "warn").mockImplementation(() => { });
        const kc = new KeelsonClient();
        await kc.connect();

        const handler = vi.fn();
        await kc.subscribe("nav/pose", handler);

        const envMock = {
            subject: "nav/pose",
            payload: {
                typeUrl: "type.googleapis.com/some.Unknown",
                value: h.enc({ whatever: true }),
            },
        };
        const q = subQueues.get("nav/pose")!;
        q.push({ payload: h.enc(envMock) });

        await new Promise((r) => setTimeout(r, 10));

        expect(handler).not.toHaveBeenCalled();
        expect(warnSpy).toHaveBeenCalled();

        warnSpy.mockRestore();
    });

    /// High value tests:

    it("reuses provided session without calling open()", async () => {
        const custom = new (class {
            async declarePublisher() { return { put: vi.fn(), undeclare: vi.fn() } as any; }
            async declareSubscriber() { return { stream: async function*(){}, undeclare: vi.fn() } as any; }
            async close() {}
        })();

        const kc = new KeelsonClient({ session: custom as any });
        await kc.connect();

        expect(openSpy).not.toHaveBeenCalled();
        expect(sessionOpenSpy).not.toHaveBeenCalled();
    });

    it("caches publisher per subject", async () => {
        const kc = new KeelsonClient();
        await kc.connect();

        const spyDeclare = vi.spyOn((kc as any).session, "declarePublisher");

        await kc.publish("nav/pose", { x: 1, y: 2, z: 3 });
        await kc.publish("nav/pose", { x: 4, y: 5, z: 6 });

        expect(spyDeclare).toHaveBeenCalledTimes(1);
    });

    it("publishes with explicit typeName override", async () => {
        const kc = new KeelsonClient();
        await kc.connect();

        // Reuse PoseCtor but pretend the subject has no mapping
        await kc.publish("unknown/pose", { x: 7, y: 8, z: 9 }, "keelson.payloads.nav.Pose");

        const env = decodeLastPublished();
        expect(env.subject).toBe("unknown/pose");
        expect(env.payload.typeUrl).toBe("type.googleapis.com/keelson.payloads.nav.Pose");
    });

    it("throws if publish() called before connect()", async () => {
        const kc = new KeelsonClient();
        await expect(kc.publish("nav/pose", { x: 1 })).rejects.toThrow(/Session not open/);
    });

    it("throws if subscribe() called before connect()", async () => {
        const kc = new KeelsonClient();
        await expect(kc.subscribe("nav/pose", vi.fn())).rejects.toThrow(/Session not open/);
    });

    it("falls back to subjects.json when Any.typeUrl is missing", async () => {
        const { Envelope } = await import("../src/keelson/Envelope");
        const kc = new KeelsonClient();
        await kc.connect();

        const handler = vi.fn();
        await kc.subscribe("nav/pose", handler);

        const payloadBytes = h.PoseCtor.encode({ x: 1, y: 2, z: 3 }).finish();
        const envMock = { subject: "nav/pose", payload: { /* no typeUrl */ value: payloadBytes } };

        const bytes = Envelope.encode(envMock).finish();
        const q = subQueues.get("nav/pose")!;
        q.push({ payload: bytes });

        await waitForAssert(() => {
            expect(handler).toHaveBeenCalledWith({ x: 1, y: 2, z: 3 });
        });
    });

    it("close() undeclares pubs/subs and clears maps", async () => {
        const kc = new KeelsonClient();
        await kc.connect();

        await kc.subscribe("nav/pose", vi.fn());
        await kc.publish("nav/pose", { x: 0, y: 0, z: 0 });

        const pubs = Array.from((kc as any).publisherMap.values());
        const subs = Array.from((kc as any).subscriberMap.values());
        const pubUndeclareSpies = pubs.map((p: any) => vi.spyOn(p, "undeclare"));
        const subUndeclareSpies = subs.map((s: any) => vi.spyOn(s, "undeclare"));

        await kc.close();

        pubUndeclareSpies.forEach(s => expect(s).toHaveBeenCalled());
        subUndeclareSpies.forEach(s => expect(s).toHaveBeenCalled());
        expect((kc as any).publisherMap.size).toBe(0);
        expect((kc as any).subscriberMap.size).toBe(0);
    });

    it("logs and skips undecodable envelopes/payloads", async () => {
        const errSpy = vi.spyOn(console, "error").mockImplementation(() => { });
        const kc = new KeelsonClient();
        await kc.connect();

        const handler = vi.fn();
        await kc.subscribe("nav/pose", handler);

        // Push garbage bytes
        const q = subQueues.get("nav/pose")!;
        q.push({ payload: new Uint8Array([1, 2, 3, 4, 5]) });

        await new Promise(r => setTimeout(r, 20));
        expect(handler).not.toHaveBeenCalled();
        expect(errSpy).toHaveBeenCalled();

        errSpy.mockRestore();
    });

    it("handles two subjects concurrently and routes correctly", async () => {
        const { Envelope } = await import("../src/keelson/Envelope");
        const kc = new KeelsonClient();
        await kc.connect();

        const h1 = vi.fn();
        const h2 = vi.fn();
        await kc.subscribe("nav/pose", h1);
        await kc.subscribe("nav/pose2", h2);

        // Add mapping + ctor for pose2 via the existing mocks
        const reg = (await import("../src/keelson/payloads")).messageTypeRegistry as Map<string, any>;
        const pose2Type = "keelson.payloads.nav.Pose";
        reg.set(pose2Type, h.PoseCtor); // reuse PoseCtor

        // Push one message for each subject
        const bytes1 = Envelope.encode({
            subject: "nav/pose",
            payload: { typeUrl: `type.googleapis.com/${pose2Type}`, value: h.PoseCtor.encode({ x: 1, y: 1, z: 1 }).finish() }
        }).finish();

        // Also add mapping for pose2 in subjects.json mock at runtime (if needed)
        const q1 = subQueues.get("nav/pose")!;
        q1.push({ payload: bytes1 });

        // For second subject, ensure queue exists (subscriber created it)
        const q2 = subQueues.get("nav/pose2")!;
        const bytes2 = Envelope.encode({
            subject: "nav/pose2",
            payload: { typeUrl: `type.googleapis.com/${pose2Type}`, value: h.PoseCtor.encode({ x: 2, y: 2, z: 2 }).finish() }
        }).finish();
        q2.push({ payload: bytes2 });

        await waitForAssert(() => {
            expect(h1).toHaveBeenCalledWith({ x: 1, y: 1, z: 1 });
            expect(h2).toHaveBeenCalledWith({ x: 2, y: 2, z: 2 });
        });
    });


});
