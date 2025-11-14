const {
    get_subject_from_pubsub_key,
    getSubjectSchema,
    isSubjectWellKnown,
    decodePayloadFromTypeName
} = require('../dist/index.js');

module.exports = function(RED) {
    function KeelsonUnpackNode(config) {
        RED.nodes.createNode(this, config);
        const node = this;
        node.subject = config.subject;

        node.on('input', function(msg, send, done) {
            // For compatibility with Node-RED 0.x, fallback to node.send
            send = send || function() { node.send.apply(node, arguments); };
            done = done || function(err) { if (err) node.error(err, msg); };

            try {
                // Determine the subject to use
                let subject = node.subject;

                // If no subject configured, try to extract from topic
                if (!subject || subject === '') {
                    if (msg.topic) {
                        try {
                            subject = get_subject_from_pubsub_key(msg.topic);
                        } catch (err) {
                            node.error('Failed to extract subject from topic. Configure a subject or ensure topic is a valid Keelson key.', msg);
                            done();
                            return;
                        }
                    } else {
                        node.error('No subject configured and no topic provided', msg);
                        done();
                        return;
                    }
                }

                // Validate subject is well-known
                if (!isSubjectWellKnown(subject)) {
                    node.error(`Subject "${subject}" is not a well-known subject`, msg);
                    done();
                    return;
                }

                // Get the schema (protobuf type name) for this subject
                const typeName = getSubjectSchema(subject);
                if (!typeName) {
                    node.error(`No schema found for subject "${subject}"`, msg);
                    done();
                    return;
                }

                // Get payload bytes
                let payload = msg.payload;

                // Convert Buffer to Uint8Array if needed
                if (Buffer.isBuffer(payload)) {
                    payload = new Uint8Array(payload);
                } else if (!(payload instanceof Uint8Array)) {
                    node.error('Payload must be a Buffer or Uint8Array', msg);
                    done();
                    return;
                }

                // Decode the payload
                const decoded = decodePayloadFromTypeName(typeName, payload);
                if (!decoded) {
                    node.error(`Failed to decode payload using type "${typeName}"`, msg);
                    done();
                    return;
                }

                // Set the decoded object as the payload
                msg.payload = decoded;

                // Add metadata
                msg.keelson_subject = subject;
                msg.keelson_type = typeName;

                // Pass through any other properties
                send(msg);
                done();
            } catch (err) {
                done(err);
            }
        });
    }

    RED.nodes.registerType("keelson-unpack", KeelsonUnpackNode);
};
