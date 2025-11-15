const helper = require("node-red-node-test-helper");
const encloseNode = require("./keelson-enclose.js");
const uncoverNode = require("./keelson-uncover.js");
const encodePayloadNode = require("./keelson-encode-payload.js");
const decodePayloadNode = require("./keelson-decode-payload.js");

helper.init(require.resolve('node-red'));

describe('Keelson Node-RED Nodes', function () {
    beforeEach(function (done) {
        helper.startServer(done);
    });

    afterEach(function (done) {
        helper.unload();
        helper.stopServer(done);
    });

    describe('keelson-enclose node', function () {
        it('should be loaded', function (done) {
            const flow = [{ id: "n1", type: "keelson-enclose", name: "test enclose" }];
            helper.load(encloseNode, flow, function () {
                const n1 = helper.getNode("n1");
                try {
                    n1.should.have.property('name', 'test enclose');
                    done();
                } catch (err) {
                    done(err);
                }
            });
        });

        it('should enclose a payload', function (done) {
            const flow = [
                { id: "n1", type: "keelson-enclose", name: "enclose", wires: [["n2"]] },
                { id: "n2", type: "helper" }
            ];
            helper.load(encloseNode, flow, function () {
                const n2 = helper.getNode("n2");
                const n1 = helper.getNode("n1");
                n2.on("input", function (msg) {
                    try {
                        msg.should.have.property('payload');
                        msg.payload.should.be.instanceof(Buffer);
                        msg.payload.length.should.be.above(0);
                        done();
                    } catch (err) {
                        done(err);
                    }
                });
                n1.receive({ payload: Buffer.from("Hello, Keelson!") });
            });
        });

        it('should accept Uint8Array as payload', function (done) {
            const flow = [
                { id: "n1", type: "keelson-enclose", name: "enclose", wires: [["n2"]] },
                { id: "n2", type: "helper" }
            ];
            helper.load(encloseNode, flow, function () {
                const n2 = helper.getNode("n2");
                const n1 = helper.getNode("n1");
                n2.on("input", function (msg) {
                    try {
                        msg.should.have.property('payload');
                        msg.payload.should.be.instanceof(Buffer);
                        done();
                    } catch (err) {
                        done(err);
                    }
                });
                n1.receive({ payload: new Uint8Array([1, 2, 3]) });
            });
        });

        it('should use custom enclosed_at timestamp', function (done) {
            const flow = [
                { id: "n1", type: "keelson-enclose", name: "enclose", wires: [["n2"]] },
                { id: "n2", type: "helper" }
            ];
            helper.load(encloseNode, flow, function () {
                const n2 = helper.getNode("n2");
                const n1 = helper.getNode("n1");
                const customDate = new Date('2024-01-01T00:00:00Z');
                n2.on("input", function (msg) {
                    try {
                        msg.should.have.property('payload');
                        msg.payload.should.be.instanceof(Buffer);
                        done();
                    } catch (err) {
                        done(err);
                    }
                });
                n1.receive({
                    payload: Buffer.from("test"),
                    enclosed_at: customDate
                });
            });
        });

        it('should pass through other message properties', function (done) {
            const flow = [
                { id: "n1", type: "keelson-enclose", name: "enclose", wires: [["n2"]] },
                { id: "n2", type: "helper" }
            ];
            helper.load(encloseNode, flow, function () {
                const n2 = helper.getNode("n2");
                const n1 = helper.getNode("n1");
                n2.on("input", function (msg) {
                    try {
                        msg.should.have.property('topic', 'test/topic');
                        msg.should.have.property('custom_prop', 'custom_value');
                        done();
                    } catch (err) {
                        done(err);
                    }
                });
                n1.receive({
                    payload: Buffer.from("test"),
                    topic: "test/topic",
                    custom_prop: "custom_value"
                });
            });
        });
    });

    describe('keelson-uncover node', function () {
        it('should be loaded', function (done) {
            const flow = [{ id: "n1", type: "keelson-uncover", name: "test uncover" }];
            helper.load(uncoverNode, flow, function () {
                const n1 = helper.getNode("n1");
                try {
                    n1.should.have.property('name', 'test uncover');
                    done();
                } catch (err) {
                    done(err);
                }
            });
        });

        it('should uncover an envelope', function (done) {
            const flow = [
                { id: "n1", type: "keelson-enclose", name: "enclose", wires: [["n2"]] },
                { id: "n2", type: "keelson-uncover", name: "uncover", wires: [["n3"]] },
                { id: "n3", type: "helper" }
            ];
            helper.load([encloseNode, uncoverNode], flow, function () {
                const n3 = helper.getNode("n3");
                const n1 = helper.getNode("n1");
                const testPayload = Buffer.from("Hello, Keelson!");

                n3.on("input", function (msg) {
                    try {
                        msg.should.have.property('payload');
                        msg.payload.should.be.instanceof(Buffer);
                        msg.payload.toString().should.equal("Hello, Keelson!");
                        msg.should.have.property('enclosed_at');
                        msg.should.have.property('uncovered_at');
                        msg.uncovered_at.should.be.instanceof(Date);
                        done();
                    } catch (err) {
                        done(err);
                    }
                });
                n1.receive({ payload: testPayload });
            });
        });

        it('should preserve enclosed_at timestamp', function (done) {
            const flow = [
                { id: "n1", type: "keelson-enclose", name: "enclose", wires: [["n2"]] },
                { id: "n2", type: "keelson-uncover", name: "uncover", wires: [["n3"]] },
                { id: "n3", type: "helper" }
            ];
            helper.load([encloseNode, uncoverNode], flow, function () {
                const n3 = helper.getNode("n3");
                const n1 = helper.getNode("n1");
                const customDate = new Date('2024-01-01T00:00:00Z');

                n3.on("input", function (msg) {
                    try {
                        msg.should.have.property('enclosed_at');
                        msg.enclosed_at.should.be.instanceof(Date);
                        msg.enclosed_at.getTime().should.equal(customDate.getTime());
                        done();
                    } catch (err) {
                        done(err);
                    }
                });
                n1.receive({
                    payload: Buffer.from("test"),
                    enclosed_at: customDate
                });
            });
        });

        it('should pass through other message properties', function (done) {
            const flow = [
                { id: "n1", type: "keelson-enclose", name: "enclose", wires: [["n2"]] },
                { id: "n2", type: "keelson-uncover", name: "uncover", wires: [["n3"]] },
                { id: "n3", type: "helper" }
            ];
            helper.load([encloseNode, uncoverNode], flow, function () {
                const n3 = helper.getNode("n3");
                const n1 = helper.getNode("n1");

                n3.on("input", function (msg) {
                    try {
                        msg.should.have.property('topic', 'test/topic');
                        msg.should.have.property('custom_prop', 'custom_value');
                        done();
                    } catch (err) {
                        done(err);
                    }
                });
                n1.receive({
                    payload: Buffer.from("test"),
                    topic: "test/topic",
                    custom_prop: "custom_value"
                });
            });
        });
    });

    describe('keelson-encode-payload node', function () {
        it('should be loaded', function (done) {
            const flow = [{
                id: "n1",
                type: "keelson-encode-payload",
                name: "test encode",
                subject: "location_fix"
            }];
            helper.load(encodePayloadNode, flow, function () {
                const n1 = helper.getNode("n1");
                try {
                    n1.should.have.property('name', 'test encode');
                    n1.should.have.property('subject', 'location_fix');
                    done();
                } catch (err) {
                    done(err);
                }
            });
        });

        it('should encode a payload with configured subject', function (done) {
            const flow = [
                {
                    id: "n1",
                    type: "keelson-encode-payload",
                    name: "encode",
                    subject: "raw",
                    wires: [["n2"]]
                },
                { id: "n2", type: "helper" }
            ];
            helper.load(encodePayloadNode, flow, function () {
                const n2 = helper.getNode("n2");
                const n1 = helper.getNode("n1");

                n2.on("input", function (msg) {
                    try {
                        msg.should.have.property('payload');
                        msg.payload.should.be.instanceof(Buffer);
                        msg.payload.length.should.be.above(0);
                        msg.should.have.property('keelson_subject', 'raw');
                        msg.should.have.property('keelson_type');
                        done();
                    } catch (err) {
                        done(err);
                    }
                });
                n1.receive({
                    payload: {
                        value: new Uint8Array([1, 2, 3, 4])
                    }
                });
            });
        });

        it('should extract subject from topic', function (done) {
            const flow = [
                {
                    id: "n1",
                    type: "keelson-encode-payload",
                    name: "encode",
                    subject: "",
                    wires: [["n2"]]
                },
                { id: "n2", type: "helper" }
            ];
            helper.load(encodePayloadNode, flow, function () {
                const n2 = helper.getNode("n2");
                const n1 = helper.getNode("n1");

                n2.on("input", function (msg) {
                    try {
                        msg.should.have.property('payload');
                        msg.payload.should.be.instanceof(Buffer);
                        msg.should.have.property('keelson_subject', 'raw');
                        done();
                    } catch (err) {
                        done(err);
                    }
                });
                n1.receive({
                    payload: {
                        value: new Uint8Array([1, 2, 3, 4])
                    },
                    topic: "vessel/@v0/123/pubsub/raw/sensor"
                });
            });
        });

        it('should pass through other message properties', function (done) {
            const flow = [
                {
                    id: "n1",
                    type: "keelson-encode-payload",
                    name: "encode",
                    subject: "raw",
                    wires: [["n2"]]
                },
                { id: "n2", type: "helper" }
            ];
            helper.load(encodePayloadNode, flow, function () {
                const n2 = helper.getNode("n2");
                const n1 = helper.getNode("n1");

                n2.on("input", function (msg) {
                    try {
                        msg.should.have.property('custom_prop', 'custom_value');
                        done();
                    } catch (err) {
                        done(err);
                    }
                });
                n1.receive({
                    payload: { value: new Uint8Array([1, 2, 3, 4]) },
                    custom_prop: "custom_value"
                });
            });
        });
    });

    describe('keelson-decode-payload node', function () {
        it('should be loaded', function (done) {
            const flow = [{
                id: "n1",
                type: "keelson-decode-payload",
                name: "test decode",
                subject: "raw"
            }];
            helper.load(decodePayloadNode, flow, function () {
                const n1 = helper.getNode("n1");
                try {
                    n1.should.have.property('name', 'test decode');
                    n1.should.have.property('subject', 'raw');
                    done();
                } catch (err) {
                    done(err);
                }
            });
        });

        it('should decode a payload with configured subject', function (done) {
            const flow = [
                {
                    id: "n1",
                    type: "keelson-encode-payload",
                    name: "encode",
                    subject: "raw",
                    wires: [["n2"]]
                },
                {
                    id: "n2",
                    type: "keelson-decode-payload",
                    name: "decode",
                    subject: "raw",
                    wires: [["n3"]]
                },
                { id: "n3", type: "helper" }
            ];
            helper.load([encodePayloadNode, decodePayloadNode], flow, function () {
                const n3 = helper.getNode("n3");
                const n1 = helper.getNode("n1");
                const testPayload = {
                    value: new Uint8Array([1, 2, 3, 4])
                };

                n3.on("input", function (msg) {
                    try {
                        msg.should.have.property('payload');
                        msg.payload.should.be.instanceof(Object);
                        msg.payload.should.have.property('value');
                        msg.payload.value.should.be.instanceof(Uint8Array);
                        msg.payload.value.length.should.equal(4);
                        msg.should.have.property('keelson_subject', 'raw');
                        msg.should.have.property('keelson_type');
                        done();
                    } catch (err) {
                        done(err);
                    }
                });
                n1.receive({ payload: testPayload });
            });
        });

        it('should extract subject from topic', function (done) {
            const flow = [
                {
                    id: "n1",
                    type: "keelson-encode-payload",
                    name: "encode",
                    subject: "raw",
                    wires: [["n2"]]
                },
                {
                    id: "n2",
                    type: "keelson-decode-payload",
                    name: "decode",
                    subject: "",
                    wires: [["n3"]]
                },
                { id: "n3", type: "helper" }
            ];
            helper.load([encodePayloadNode, decodePayloadNode], flow, function () {
                const n3 = helper.getNode("n3");
                const n1 = helper.getNode("n1");

                n3.on("input", function (msg) {
                    try {
                        msg.should.have.property('payload');
                        msg.payload.should.be.instanceof(Object);
                        msg.should.have.property('keelson_subject', 'raw');
                        done();
                    } catch (err) {
                        done(err);
                    }
                });
                n1.receive({
                    payload: { value: new Uint8Array([1, 2, 3, 4]) },
                    topic: "vessel/@v0/123/pubsub/raw/sensor"
                });
            });
        });

        it('should pass through other message properties', function (done) {
            const flow = [
                {
                    id: "n1",
                    type: "keelson-encode-payload",
                    name: "encode",
                    subject: "raw",
                    wires: [["n2"]]
                },
                {
                    id: "n2",
                    type: "keelson-decode-payload",
                    name: "decode",
                    subject: "raw",
                    wires: [["n3"]]
                },
                { id: "n3", type: "helper" }
            ];
            helper.load([encodePayloadNode, decodePayloadNode], flow, function () {
                const n3 = helper.getNode("n3");
                const n1 = helper.getNode("n1");

                n3.on("input", function (msg) {
                    try {
                        msg.should.have.property('custom_prop', 'custom_value');
                        done();
                    } catch (err) {
                        done(err);
                    }
                });
                n1.receive({
                    payload: { value: new Uint8Array([1, 2, 3, 4]) },
                    custom_prop: "custom_value"
                });
            });
        });
    });

    describe('Full integration flow', function () {
        it('should encode, enclose, uncover, and decode', function (done) {
            const flow = [
                {
                    id: "n1",
                    type: "keelson-encode-payload",
                    name: "encode",
                    subject: "raw",
                    wires: [["n2"]]
                },
                {
                    id: "n2",
                    type: "keelson-enclose",
                    name: "enclose",
                    wires: [["n3"]]
                },
                {
                    id: "n3",
                    type: "keelson-uncover",
                    name: "uncover",
                    wires: [["n4"]]
                },
                {
                    id: "n4",
                    type: "keelson-decode-payload",
                    name: "decode",
                    subject: "raw",
                    wires: [["n5"]]
                },
                { id: "n5", type: "helper" }
            ];
            helper.load([encodePayloadNode, encloseNode, uncoverNode, decodePayloadNode], flow, function () {
                const n5 = helper.getNode("n5");
                const n1 = helper.getNode("n1");
                const testPayload = {
                    value: new Uint8Array([1, 2, 3, 4])
                };

                n5.on("input", function (msg) {
                    try {
                        msg.should.have.property('payload');
                        msg.payload.should.be.instanceof(Object);
                        msg.payload.should.have.property('value');
                        msg.payload.value.should.be.instanceof(Uint8Array);
                        msg.payload.value.length.should.equal(4);
                        msg.should.have.property('keelson_subject', 'raw');
                        msg.should.have.property('enclosed_at');
                        msg.should.have.property('uncovered_at');
                        done();
                    } catch (err) {
                        done(err);
                    }
                });
                n1.receive({ payload: testPayload });
            });
        });
    });
});
