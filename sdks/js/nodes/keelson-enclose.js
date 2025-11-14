const { enclose } = require('../dist/index.js');
const { Envelope } = require('../dist/Envelope.js');

module.exports = function(RED) {
    function KeelsonEncloseNode(config) {
        RED.nodes.createNode(this, config);
        const node = this;

        node.on('input', function(msg, send, done) {
            // For compatibility with Node-RED 0.x, fallback to node.send
            send = send || function() { node.send.apply(node, arguments); };
            done = done || function(err) { if (err) node.error(err, msg); };

            try {
                // Get payload from msg.payload (should be Buffer or Uint8Array)
                let payload = msg.payload;

                // Convert Buffer to Uint8Array if needed
                if (Buffer.isBuffer(payload)) {
                    payload = new Uint8Array(payload);
                } else if (!(payload instanceof Uint8Array)) {
                    node.error('Payload must be a Buffer or Uint8Array', msg);
                    done();
                    return;
                }

                // Get optional enclosed_at timestamp from msg.enclosed_at
                let enclosed_at = msg.enclosed_at;
                if (enclosed_at && !(enclosed_at instanceof Date)) {
                    // Try to parse as date if it's a string or number
                    enclosed_at = new Date(enclosed_at);
                    if (isNaN(enclosed_at.getTime())) {
                        node.warn('Invalid enclosed_at timestamp, using current time');
                        enclosed_at = undefined;
                    }
                }

                // Enclose the payload
                const envelope = enclose(payload, enclosed_at);

                // Serialize the envelope
                const serialized = Envelope.encode(envelope).finish();

                // Output the serialized envelope as Buffer for MQTT compatibility
                msg.payload = Buffer.from(serialized);

                // Pass through any other properties
                send(msg);
                done();
            } catch (err) {
                done(err);
            }
        });
    }

    RED.nodes.registerType("keelson-enclose", KeelsonEncloseNode);
};
