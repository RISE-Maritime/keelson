const { uncover } = require('../dist/index.js');

module.exports = function(RED) {
    function KeelsonUncoverNode(config) {
        RED.nodes.createNode(this, config);
        const node = this;

        node.on('input', function(msg, send, done) {
            // For compatibility with Node-RED 0.x, fallback to node.send
            send = send || function() { node.send.apply(node, arguments); };
            done = done || function(err) { if (err) node.error(err, msg); };

            try {
                // Get the serialized envelope from msg.payload
                let encodedEnvelope = msg.payload;

                // Convert Buffer to Uint8Array if needed
                if (Buffer.isBuffer(encodedEnvelope)) {
                    encodedEnvelope = new Uint8Array(encodedEnvelope);
                } else if (!(encodedEnvelope instanceof Uint8Array)) {
                    node.error('Payload must be a Buffer or Uint8Array containing a serialized Envelope', msg);
                    done();
                    return;
                }

                // Uncover the envelope
                const result = uncover(encodedEnvelope);

                if (!result) {
                    node.error('Failed to uncover envelope', msg);
                    done();
                    return;
                }

                const [uncovered_at, enclosed_at, payload] = result;

                // Set the payload to the uncovered bytes as Buffer
                msg.payload = Buffer.from(payload);

                // Add timestamp information
                msg.enclosed_at = enclosed_at;
                msg.uncovered_at = uncovered_at;

                // Pass through any other properties
                send(msg);
                done();
            } catch (err) {
                done(err);
            }
        });
    }

    RED.nodes.registerType("keelson-uncover", KeelsonUncoverNode);
};
