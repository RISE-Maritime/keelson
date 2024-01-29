/*eslint-disable block-scoped-var, id-length, no-control-regex, no-magic-numbers, no-prototype-builtins, no-redeclare, no-shadow, no-var, sort-vars*/
"use strict";

var $protobuf = require("protobufjs/minimal");

// Common aliases
var $Reader = $protobuf.Reader, $Writer = $protobuf.Writer, $util = $protobuf.util;

// Exported root namespace
var $root = $protobuf.roots["default"] || ($protobuf.roots["default"] = {});

$root.brefv = (function() {

    /**
     * Namespace brefv.
     * @exports brefv
     * @namespace
     */
    var brefv = {};

    brefv.Envelope = (function() {

        /**
         * Properties of an Envelope.
         * @memberof brefv
         * @interface IEnvelope
         * @property {google.protobuf.ITimestamp|null} [enclosedAt] Envelope enclosedAt
         * @property {Uint8Array|null} [payload] Envelope payload
         */

        /**
         * Constructs a new Envelope.
         * @memberof brefv
         * @classdesc Represents an Envelope.
         * @implements IEnvelope
         * @constructor
         * @param {brefv.IEnvelope=} [properties] Properties to set
         */
        function Envelope(properties) {
            if (properties)
                for (var keys = Object.keys(properties), i = 0; i < keys.length; ++i)
                    if (properties[keys[i]] != null)
                        this[keys[i]] = properties[keys[i]];
        }

        /**
         * Envelope enclosedAt.
         * @member {google.protobuf.ITimestamp|null|undefined} enclosedAt
         * @memberof brefv.Envelope
         * @instance
         */
        Envelope.prototype.enclosedAt = null;

        /**
         * Envelope payload.
         * @member {Uint8Array} payload
         * @memberof brefv.Envelope
         * @instance
         */
        Envelope.prototype.payload = $util.newBuffer([]);

        /**
         * Creates a new Envelope instance using the specified properties.
         * @function create
         * @memberof brefv.Envelope
         * @static
         * @param {brefv.IEnvelope=} [properties] Properties to set
         * @returns {brefv.Envelope} Envelope instance
         */
        Envelope.create = function create(properties) {
            return new Envelope(properties);
        };

        /**
         * Encodes the specified Envelope message. Does not implicitly {@link brefv.Envelope.verify|verify} messages.
         * @function encode
         * @memberof brefv.Envelope
         * @static
         * @param {brefv.IEnvelope} message Envelope message or plain object to encode
         * @param {$protobuf.Writer} [writer] Writer to encode to
         * @returns {$protobuf.Writer} Writer
         */
        Envelope.encode = function encode(message, writer) {
            if (!writer)
                writer = $Writer.create();
            if (message.enclosedAt != null && Object.hasOwnProperty.call(message, "enclosedAt"))
                $root.google.protobuf.Timestamp.encode(message.enclosedAt, writer.uint32(/* id 1, wireType 2 =*/10).fork()).ldelim();
            if (message.payload != null && Object.hasOwnProperty.call(message, "payload"))
                writer.uint32(/* id 2, wireType 2 =*/18).bytes(message.payload);
            return writer;
        };

        /**
         * Encodes the specified Envelope message, length delimited. Does not implicitly {@link brefv.Envelope.verify|verify} messages.
         * @function encodeDelimited
         * @memberof brefv.Envelope
         * @static
         * @param {brefv.IEnvelope} message Envelope message or plain object to encode
         * @param {$protobuf.Writer} [writer] Writer to encode to
         * @returns {$protobuf.Writer} Writer
         */
        Envelope.encodeDelimited = function encodeDelimited(message, writer) {
            return this.encode(message, writer).ldelim();
        };

        /**
         * Decodes an Envelope message from the specified reader or buffer.
         * @function decode
         * @memberof brefv.Envelope
         * @static
         * @param {$protobuf.Reader|Uint8Array} reader Reader or buffer to decode from
         * @param {number} [length] Message length if known beforehand
         * @returns {brefv.Envelope} Envelope
         * @throws {Error} If the payload is not a reader or valid buffer
         * @throws {$protobuf.util.ProtocolError} If required fields are missing
         */
        Envelope.decode = function decode(reader, length) {
            if (!(reader instanceof $Reader))
                reader = $Reader.create(reader);
            var end = length === undefined ? reader.len : reader.pos + length, message = new $root.brefv.Envelope();
            while (reader.pos < end) {
                var tag = reader.uint32();
                switch (tag >>> 3) {
                case 1: {
                        message.enclosedAt = $root.google.protobuf.Timestamp.decode(reader, reader.uint32());
                        break;
                    }
                case 2: {
                        message.payload = reader.bytes();
                        break;
                    }
                default:
                    reader.skipType(tag & 7);
                    break;
                }
            }
            return message;
        };

        /**
         * Decodes an Envelope message from the specified reader or buffer, length delimited.
         * @function decodeDelimited
         * @memberof brefv.Envelope
         * @static
         * @param {$protobuf.Reader|Uint8Array} reader Reader or buffer to decode from
         * @returns {brefv.Envelope} Envelope
         * @throws {Error} If the payload is not a reader or valid buffer
         * @throws {$protobuf.util.ProtocolError} If required fields are missing
         */
        Envelope.decodeDelimited = function decodeDelimited(reader) {
            if (!(reader instanceof $Reader))
                reader = new $Reader(reader);
            return this.decode(reader, reader.uint32());
        };

        /**
         * Verifies an Envelope message.
         * @function verify
         * @memberof brefv.Envelope
         * @static
         * @param {Object.<string,*>} message Plain object to verify
         * @returns {string|null} `null` if valid, otherwise the reason why it is not
         */
        Envelope.verify = function verify(message) {
            if (typeof message !== "object" || message === null)
                return "object expected";
            if (message.enclosedAt != null && message.hasOwnProperty("enclosedAt")) {
                var error = $root.google.protobuf.Timestamp.verify(message.enclosedAt);
                if (error)
                    return "enclosedAt." + error;
            }
            if (message.payload != null && message.hasOwnProperty("payload"))
                if (!(message.payload && typeof message.payload.length === "number" || $util.isString(message.payload)))
                    return "payload: buffer expected";
            return null;
        };

        /**
         * Creates an Envelope message from a plain object. Also converts values to their respective internal types.
         * @function fromObject
         * @memberof brefv.Envelope
         * @static
         * @param {Object.<string,*>} object Plain object
         * @returns {brefv.Envelope} Envelope
         */
        Envelope.fromObject = function fromObject(object) {
            if (object instanceof $root.brefv.Envelope)
                return object;
            var message = new $root.brefv.Envelope();
            if (object.enclosedAt != null) {
                if (typeof object.enclosedAt !== "object")
                    throw TypeError(".brefv.Envelope.enclosedAt: object expected");
                message.enclosedAt = $root.google.protobuf.Timestamp.fromObject(object.enclosedAt);
            }
            if (object.payload != null)
                if (typeof object.payload === "string")
                    $util.base64.decode(object.payload, message.payload = $util.newBuffer($util.base64.length(object.payload)), 0);
                else if (object.payload.length >= 0)
                    message.payload = object.payload;
            return message;
        };

        /**
         * Creates a plain object from an Envelope message. Also converts values to other types if specified.
         * @function toObject
         * @memberof brefv.Envelope
         * @static
         * @param {brefv.Envelope} message Envelope
         * @param {$protobuf.IConversionOptions} [options] Conversion options
         * @returns {Object.<string,*>} Plain object
         */
        Envelope.toObject = function toObject(message, options) {
            if (!options)
                options = {};
            var object = {};
            if (options.defaults) {
                object.enclosedAt = null;
                if (options.bytes === String)
                    object.payload = "";
                else {
                    object.payload = [];
                    if (options.bytes !== Array)
                        object.payload = $util.newBuffer(object.payload);
                }
            }
            if (message.enclosedAt != null && message.hasOwnProperty("enclosedAt"))
                object.enclosedAt = $root.google.protobuf.Timestamp.toObject(message.enclosedAt, options);
            if (message.payload != null && message.hasOwnProperty("payload"))
                object.payload = options.bytes === String ? $util.base64.encode(message.payload, 0, message.payload.length) : options.bytes === Array ? Array.prototype.slice.call(message.payload) : message.payload;
            return object;
        };

        /**
         * Converts this Envelope to JSON.
         * @function toJSON
         * @memberof brefv.Envelope
         * @instance
         * @returns {Object.<string,*>} JSON object
         */
        Envelope.prototype.toJSON = function toJSON() {
            return this.constructor.toObject(this, $protobuf.util.toJSONOptions);
        };

        /**
         * Gets the default type url for Envelope
         * @function getTypeUrl
         * @memberof brefv.Envelope
         * @static
         * @param {string} [typeUrlPrefix] your custom typeUrlPrefix(default "type.googleapis.com")
         * @returns {string} The default type url
         */
        Envelope.getTypeUrl = function getTypeUrl(typeUrlPrefix) {
            if (typeUrlPrefix === undefined) {
                typeUrlPrefix = "type.googleapis.com";
            }
            return typeUrlPrefix + "/brefv.Envelope";
        };

        return Envelope;
    })();

    brefv.scalars = (function() {

        /**
         * Namespace scalars.
         * @memberof brefv
         * @namespace
         */
        var scalars = {};

        scalars.TimestampedBytes = (function() {

            /**
             * Properties of a TimestampedBytes.
             * @memberof brefv.scalars
             * @interface ITimestampedBytes
             * @property {google.protobuf.ITimestamp|null} [timestamp] TimestampedBytes timestamp
             * @property {Uint8Array|null} [value] TimestampedBytes value
             */

            /**
             * Constructs a new TimestampedBytes.
             * @memberof brefv.scalars
             * @classdesc Represents a TimestampedBytes.
             * @implements ITimestampedBytes
             * @constructor
             * @param {brefv.scalars.ITimestampedBytes=} [properties] Properties to set
             */
            function TimestampedBytes(properties) {
                if (properties)
                    for (var keys = Object.keys(properties), i = 0; i < keys.length; ++i)
                        if (properties[keys[i]] != null)
                            this[keys[i]] = properties[keys[i]];
            }

            /**
             * TimestampedBytes timestamp.
             * @member {google.protobuf.ITimestamp|null|undefined} timestamp
             * @memberof brefv.scalars.TimestampedBytes
             * @instance
             */
            TimestampedBytes.prototype.timestamp = null;

            /**
             * TimestampedBytes value.
             * @member {Uint8Array} value
             * @memberof brefv.scalars.TimestampedBytes
             * @instance
             */
            TimestampedBytes.prototype.value = $util.newBuffer([]);

            /**
             * Creates a new TimestampedBytes instance using the specified properties.
             * @function create
             * @memberof brefv.scalars.TimestampedBytes
             * @static
             * @param {brefv.scalars.ITimestampedBytes=} [properties] Properties to set
             * @returns {brefv.scalars.TimestampedBytes} TimestampedBytes instance
             */
            TimestampedBytes.create = function create(properties) {
                return new TimestampedBytes(properties);
            };

            /**
             * Encodes the specified TimestampedBytes message. Does not implicitly {@link brefv.scalars.TimestampedBytes.verify|verify} messages.
             * @function encode
             * @memberof brefv.scalars.TimestampedBytes
             * @static
             * @param {brefv.scalars.ITimestampedBytes} message TimestampedBytes message or plain object to encode
             * @param {$protobuf.Writer} [writer] Writer to encode to
             * @returns {$protobuf.Writer} Writer
             */
            TimestampedBytes.encode = function encode(message, writer) {
                if (!writer)
                    writer = $Writer.create();
                if (message.timestamp != null && Object.hasOwnProperty.call(message, "timestamp"))
                    $root.google.protobuf.Timestamp.encode(message.timestamp, writer.uint32(/* id 1, wireType 2 =*/10).fork()).ldelim();
                if (message.value != null && Object.hasOwnProperty.call(message, "value"))
                    writer.uint32(/* id 2, wireType 2 =*/18).bytes(message.value);
                return writer;
            };

            /**
             * Encodes the specified TimestampedBytes message, length delimited. Does not implicitly {@link brefv.scalars.TimestampedBytes.verify|verify} messages.
             * @function encodeDelimited
             * @memberof brefv.scalars.TimestampedBytes
             * @static
             * @param {brefv.scalars.ITimestampedBytes} message TimestampedBytes message or plain object to encode
             * @param {$protobuf.Writer} [writer] Writer to encode to
             * @returns {$protobuf.Writer} Writer
             */
            TimestampedBytes.encodeDelimited = function encodeDelimited(message, writer) {
                return this.encode(message, writer).ldelim();
            };

            /**
             * Decodes a TimestampedBytes message from the specified reader or buffer.
             * @function decode
             * @memberof brefv.scalars.TimestampedBytes
             * @static
             * @param {$protobuf.Reader|Uint8Array} reader Reader or buffer to decode from
             * @param {number} [length] Message length if known beforehand
             * @returns {brefv.scalars.TimestampedBytes} TimestampedBytes
             * @throws {Error} If the payload is not a reader or valid buffer
             * @throws {$protobuf.util.ProtocolError} If required fields are missing
             */
            TimestampedBytes.decode = function decode(reader, length) {
                if (!(reader instanceof $Reader))
                    reader = $Reader.create(reader);
                var end = length === undefined ? reader.len : reader.pos + length, message = new $root.brefv.scalars.TimestampedBytes();
                while (reader.pos < end) {
                    var tag = reader.uint32();
                    switch (tag >>> 3) {
                    case 1: {
                            message.timestamp = $root.google.protobuf.Timestamp.decode(reader, reader.uint32());
                            break;
                        }
                    case 2: {
                            message.value = reader.bytes();
                            break;
                        }
                    default:
                        reader.skipType(tag & 7);
                        break;
                    }
                }
                return message;
            };

            /**
             * Decodes a TimestampedBytes message from the specified reader or buffer, length delimited.
             * @function decodeDelimited
             * @memberof brefv.scalars.TimestampedBytes
             * @static
             * @param {$protobuf.Reader|Uint8Array} reader Reader or buffer to decode from
             * @returns {brefv.scalars.TimestampedBytes} TimestampedBytes
             * @throws {Error} If the payload is not a reader or valid buffer
             * @throws {$protobuf.util.ProtocolError} If required fields are missing
             */
            TimestampedBytes.decodeDelimited = function decodeDelimited(reader) {
                if (!(reader instanceof $Reader))
                    reader = new $Reader(reader);
                return this.decode(reader, reader.uint32());
            };

            /**
             * Verifies a TimestampedBytes message.
             * @function verify
             * @memberof brefv.scalars.TimestampedBytes
             * @static
             * @param {Object.<string,*>} message Plain object to verify
             * @returns {string|null} `null` if valid, otherwise the reason why it is not
             */
            TimestampedBytes.verify = function verify(message) {
                if (typeof message !== "object" || message === null)
                    return "object expected";
                if (message.timestamp != null && message.hasOwnProperty("timestamp")) {
                    var error = $root.google.protobuf.Timestamp.verify(message.timestamp);
                    if (error)
                        return "timestamp." + error;
                }
                if (message.value != null && message.hasOwnProperty("value"))
                    if (!(message.value && typeof message.value.length === "number" || $util.isString(message.value)))
                        return "value: buffer expected";
                return null;
            };

            /**
             * Creates a TimestampedBytes message from a plain object. Also converts values to their respective internal types.
             * @function fromObject
             * @memberof brefv.scalars.TimestampedBytes
             * @static
             * @param {Object.<string,*>} object Plain object
             * @returns {brefv.scalars.TimestampedBytes} TimestampedBytes
             */
            TimestampedBytes.fromObject = function fromObject(object) {
                if (object instanceof $root.brefv.scalars.TimestampedBytes)
                    return object;
                var message = new $root.brefv.scalars.TimestampedBytes();
                if (object.timestamp != null) {
                    if (typeof object.timestamp !== "object")
                        throw TypeError(".brefv.scalars.TimestampedBytes.timestamp: object expected");
                    message.timestamp = $root.google.protobuf.Timestamp.fromObject(object.timestamp);
                }
                if (object.value != null)
                    if (typeof object.value === "string")
                        $util.base64.decode(object.value, message.value = $util.newBuffer($util.base64.length(object.value)), 0);
                    else if (object.value.length >= 0)
                        message.value = object.value;
                return message;
            };

            /**
             * Creates a plain object from a TimestampedBytes message. Also converts values to other types if specified.
             * @function toObject
             * @memberof brefv.scalars.TimestampedBytes
             * @static
             * @param {brefv.scalars.TimestampedBytes} message TimestampedBytes
             * @param {$protobuf.IConversionOptions} [options] Conversion options
             * @returns {Object.<string,*>} Plain object
             */
            TimestampedBytes.toObject = function toObject(message, options) {
                if (!options)
                    options = {};
                var object = {};
                if (options.defaults) {
                    object.timestamp = null;
                    if (options.bytes === String)
                        object.value = "";
                    else {
                        object.value = [];
                        if (options.bytes !== Array)
                            object.value = $util.newBuffer(object.value);
                    }
                }
                if (message.timestamp != null && message.hasOwnProperty("timestamp"))
                    object.timestamp = $root.google.protobuf.Timestamp.toObject(message.timestamp, options);
                if (message.value != null && message.hasOwnProperty("value"))
                    object.value = options.bytes === String ? $util.base64.encode(message.value, 0, message.value.length) : options.bytes === Array ? Array.prototype.slice.call(message.value) : message.value;
                return object;
            };

            /**
             * Converts this TimestampedBytes to JSON.
             * @function toJSON
             * @memberof brefv.scalars.TimestampedBytes
             * @instance
             * @returns {Object.<string,*>} JSON object
             */
            TimestampedBytes.prototype.toJSON = function toJSON() {
                return this.constructor.toObject(this, $protobuf.util.toJSONOptions);
            };

            /**
             * Gets the default type url for TimestampedBytes
             * @function getTypeUrl
             * @memberof brefv.scalars.TimestampedBytes
             * @static
             * @param {string} [typeUrlPrefix] your custom typeUrlPrefix(default "type.googleapis.com")
             * @returns {string} The default type url
             */
            TimestampedBytes.getTypeUrl = function getTypeUrl(typeUrlPrefix) {
                if (typeUrlPrefix === undefined) {
                    typeUrlPrefix = "type.googleapis.com";
                }
                return typeUrlPrefix + "/brefv.scalars.TimestampedBytes";
            };

            return TimestampedBytes;
        })();

        scalars.TimestampedFloat = (function() {

            /**
             * Properties of a TimestampedFloat.
             * @memberof brefv.scalars
             * @interface ITimestampedFloat
             * @property {google.protobuf.ITimestamp|null} [timestamp] TimestampedFloat timestamp
             * @property {number|null} [value] TimestampedFloat value
             */

            /**
             * Constructs a new TimestampedFloat.
             * @memberof brefv.scalars
             * @classdesc Represents a TimestampedFloat.
             * @implements ITimestampedFloat
             * @constructor
             * @param {brefv.scalars.ITimestampedFloat=} [properties] Properties to set
             */
            function TimestampedFloat(properties) {
                if (properties)
                    for (var keys = Object.keys(properties), i = 0; i < keys.length; ++i)
                        if (properties[keys[i]] != null)
                            this[keys[i]] = properties[keys[i]];
            }

            /**
             * TimestampedFloat timestamp.
             * @member {google.protobuf.ITimestamp|null|undefined} timestamp
             * @memberof brefv.scalars.TimestampedFloat
             * @instance
             */
            TimestampedFloat.prototype.timestamp = null;

            /**
             * TimestampedFloat value.
             * @member {number} value
             * @memberof brefv.scalars.TimestampedFloat
             * @instance
             */
            TimestampedFloat.prototype.value = 0;

            /**
             * Creates a new TimestampedFloat instance using the specified properties.
             * @function create
             * @memberof brefv.scalars.TimestampedFloat
             * @static
             * @param {brefv.scalars.ITimestampedFloat=} [properties] Properties to set
             * @returns {brefv.scalars.TimestampedFloat} TimestampedFloat instance
             */
            TimestampedFloat.create = function create(properties) {
                return new TimestampedFloat(properties);
            };

            /**
             * Encodes the specified TimestampedFloat message. Does not implicitly {@link brefv.scalars.TimestampedFloat.verify|verify} messages.
             * @function encode
             * @memberof brefv.scalars.TimestampedFloat
             * @static
             * @param {brefv.scalars.ITimestampedFloat} message TimestampedFloat message or plain object to encode
             * @param {$protobuf.Writer} [writer] Writer to encode to
             * @returns {$protobuf.Writer} Writer
             */
            TimestampedFloat.encode = function encode(message, writer) {
                if (!writer)
                    writer = $Writer.create();
                if (message.timestamp != null && Object.hasOwnProperty.call(message, "timestamp"))
                    $root.google.protobuf.Timestamp.encode(message.timestamp, writer.uint32(/* id 1, wireType 2 =*/10).fork()).ldelim();
                if (message.value != null && Object.hasOwnProperty.call(message, "value"))
                    writer.uint32(/* id 2, wireType 5 =*/21).float(message.value);
                return writer;
            };

            /**
             * Encodes the specified TimestampedFloat message, length delimited. Does not implicitly {@link brefv.scalars.TimestampedFloat.verify|verify} messages.
             * @function encodeDelimited
             * @memberof brefv.scalars.TimestampedFloat
             * @static
             * @param {brefv.scalars.ITimestampedFloat} message TimestampedFloat message or plain object to encode
             * @param {$protobuf.Writer} [writer] Writer to encode to
             * @returns {$protobuf.Writer} Writer
             */
            TimestampedFloat.encodeDelimited = function encodeDelimited(message, writer) {
                return this.encode(message, writer).ldelim();
            };

            /**
             * Decodes a TimestampedFloat message from the specified reader or buffer.
             * @function decode
             * @memberof brefv.scalars.TimestampedFloat
             * @static
             * @param {$protobuf.Reader|Uint8Array} reader Reader or buffer to decode from
             * @param {number} [length] Message length if known beforehand
             * @returns {brefv.scalars.TimestampedFloat} TimestampedFloat
             * @throws {Error} If the payload is not a reader or valid buffer
             * @throws {$protobuf.util.ProtocolError} If required fields are missing
             */
            TimestampedFloat.decode = function decode(reader, length) {
                if (!(reader instanceof $Reader))
                    reader = $Reader.create(reader);
                var end = length === undefined ? reader.len : reader.pos + length, message = new $root.brefv.scalars.TimestampedFloat();
                while (reader.pos < end) {
                    var tag = reader.uint32();
                    switch (tag >>> 3) {
                    case 1: {
                            message.timestamp = $root.google.protobuf.Timestamp.decode(reader, reader.uint32());
                            break;
                        }
                    case 2: {
                            message.value = reader.float();
                            break;
                        }
                    default:
                        reader.skipType(tag & 7);
                        break;
                    }
                }
                return message;
            };

            /**
             * Decodes a TimestampedFloat message from the specified reader or buffer, length delimited.
             * @function decodeDelimited
             * @memberof brefv.scalars.TimestampedFloat
             * @static
             * @param {$protobuf.Reader|Uint8Array} reader Reader or buffer to decode from
             * @returns {brefv.scalars.TimestampedFloat} TimestampedFloat
             * @throws {Error} If the payload is not a reader or valid buffer
             * @throws {$protobuf.util.ProtocolError} If required fields are missing
             */
            TimestampedFloat.decodeDelimited = function decodeDelimited(reader) {
                if (!(reader instanceof $Reader))
                    reader = new $Reader(reader);
                return this.decode(reader, reader.uint32());
            };

            /**
             * Verifies a TimestampedFloat message.
             * @function verify
             * @memberof brefv.scalars.TimestampedFloat
             * @static
             * @param {Object.<string,*>} message Plain object to verify
             * @returns {string|null} `null` if valid, otherwise the reason why it is not
             */
            TimestampedFloat.verify = function verify(message) {
                if (typeof message !== "object" || message === null)
                    return "object expected";
                if (message.timestamp != null && message.hasOwnProperty("timestamp")) {
                    var error = $root.google.protobuf.Timestamp.verify(message.timestamp);
                    if (error)
                        return "timestamp." + error;
                }
                if (message.value != null && message.hasOwnProperty("value"))
                    if (typeof message.value !== "number")
                        return "value: number expected";
                return null;
            };

            /**
             * Creates a TimestampedFloat message from a plain object. Also converts values to their respective internal types.
             * @function fromObject
             * @memberof brefv.scalars.TimestampedFloat
             * @static
             * @param {Object.<string,*>} object Plain object
             * @returns {brefv.scalars.TimestampedFloat} TimestampedFloat
             */
            TimestampedFloat.fromObject = function fromObject(object) {
                if (object instanceof $root.brefv.scalars.TimestampedFloat)
                    return object;
                var message = new $root.brefv.scalars.TimestampedFloat();
                if (object.timestamp != null) {
                    if (typeof object.timestamp !== "object")
                        throw TypeError(".brefv.scalars.TimestampedFloat.timestamp: object expected");
                    message.timestamp = $root.google.protobuf.Timestamp.fromObject(object.timestamp);
                }
                if (object.value != null)
                    message.value = Number(object.value);
                return message;
            };

            /**
             * Creates a plain object from a TimestampedFloat message. Also converts values to other types if specified.
             * @function toObject
             * @memberof brefv.scalars.TimestampedFloat
             * @static
             * @param {brefv.scalars.TimestampedFloat} message TimestampedFloat
             * @param {$protobuf.IConversionOptions} [options] Conversion options
             * @returns {Object.<string,*>} Plain object
             */
            TimestampedFloat.toObject = function toObject(message, options) {
                if (!options)
                    options = {};
                var object = {};
                if (options.defaults) {
                    object.timestamp = null;
                    object.value = 0;
                }
                if (message.timestamp != null && message.hasOwnProperty("timestamp"))
                    object.timestamp = $root.google.protobuf.Timestamp.toObject(message.timestamp, options);
                if (message.value != null && message.hasOwnProperty("value"))
                    object.value = options.json && !isFinite(message.value) ? String(message.value) : message.value;
                return object;
            };

            /**
             * Converts this TimestampedFloat to JSON.
             * @function toJSON
             * @memberof brefv.scalars.TimestampedFloat
             * @instance
             * @returns {Object.<string,*>} JSON object
             */
            TimestampedFloat.prototype.toJSON = function toJSON() {
                return this.constructor.toObject(this, $protobuf.util.toJSONOptions);
            };

            /**
             * Gets the default type url for TimestampedFloat
             * @function getTypeUrl
             * @memberof brefv.scalars.TimestampedFloat
             * @static
             * @param {string} [typeUrlPrefix] your custom typeUrlPrefix(default "type.googleapis.com")
             * @returns {string} The default type url
             */
            TimestampedFloat.getTypeUrl = function getTypeUrl(typeUrlPrefix) {
                if (typeUrlPrefix === undefined) {
                    typeUrlPrefix = "type.googleapis.com";
                }
                return typeUrlPrefix + "/brefv.scalars.TimestampedFloat";
            };

            return TimestampedFloat;
        })();

        scalars.TimestampedDouble = (function() {

            /**
             * Properties of a TimestampedDouble.
             * @memberof brefv.scalars
             * @interface ITimestampedDouble
             * @property {google.protobuf.ITimestamp|null} [timestamp] TimestampedDouble timestamp
             * @property {number|null} [value] TimestampedDouble value
             */

            /**
             * Constructs a new TimestampedDouble.
             * @memberof brefv.scalars
             * @classdesc Represents a TimestampedDouble.
             * @implements ITimestampedDouble
             * @constructor
             * @param {brefv.scalars.ITimestampedDouble=} [properties] Properties to set
             */
            function TimestampedDouble(properties) {
                if (properties)
                    for (var keys = Object.keys(properties), i = 0; i < keys.length; ++i)
                        if (properties[keys[i]] != null)
                            this[keys[i]] = properties[keys[i]];
            }

            /**
             * TimestampedDouble timestamp.
             * @member {google.protobuf.ITimestamp|null|undefined} timestamp
             * @memberof brefv.scalars.TimestampedDouble
             * @instance
             */
            TimestampedDouble.prototype.timestamp = null;

            /**
             * TimestampedDouble value.
             * @member {number} value
             * @memberof brefv.scalars.TimestampedDouble
             * @instance
             */
            TimestampedDouble.prototype.value = 0;

            /**
             * Creates a new TimestampedDouble instance using the specified properties.
             * @function create
             * @memberof brefv.scalars.TimestampedDouble
             * @static
             * @param {brefv.scalars.ITimestampedDouble=} [properties] Properties to set
             * @returns {brefv.scalars.TimestampedDouble} TimestampedDouble instance
             */
            TimestampedDouble.create = function create(properties) {
                return new TimestampedDouble(properties);
            };

            /**
             * Encodes the specified TimestampedDouble message. Does not implicitly {@link brefv.scalars.TimestampedDouble.verify|verify} messages.
             * @function encode
             * @memberof brefv.scalars.TimestampedDouble
             * @static
             * @param {brefv.scalars.ITimestampedDouble} message TimestampedDouble message or plain object to encode
             * @param {$protobuf.Writer} [writer] Writer to encode to
             * @returns {$protobuf.Writer} Writer
             */
            TimestampedDouble.encode = function encode(message, writer) {
                if (!writer)
                    writer = $Writer.create();
                if (message.timestamp != null && Object.hasOwnProperty.call(message, "timestamp"))
                    $root.google.protobuf.Timestamp.encode(message.timestamp, writer.uint32(/* id 1, wireType 2 =*/10).fork()).ldelim();
                if (message.value != null && Object.hasOwnProperty.call(message, "value"))
                    writer.uint32(/* id 2, wireType 1 =*/17).double(message.value);
                return writer;
            };

            /**
             * Encodes the specified TimestampedDouble message, length delimited. Does not implicitly {@link brefv.scalars.TimestampedDouble.verify|verify} messages.
             * @function encodeDelimited
             * @memberof brefv.scalars.TimestampedDouble
             * @static
             * @param {brefv.scalars.ITimestampedDouble} message TimestampedDouble message or plain object to encode
             * @param {$protobuf.Writer} [writer] Writer to encode to
             * @returns {$protobuf.Writer} Writer
             */
            TimestampedDouble.encodeDelimited = function encodeDelimited(message, writer) {
                return this.encode(message, writer).ldelim();
            };

            /**
             * Decodes a TimestampedDouble message from the specified reader or buffer.
             * @function decode
             * @memberof brefv.scalars.TimestampedDouble
             * @static
             * @param {$protobuf.Reader|Uint8Array} reader Reader or buffer to decode from
             * @param {number} [length] Message length if known beforehand
             * @returns {brefv.scalars.TimestampedDouble} TimestampedDouble
             * @throws {Error} If the payload is not a reader or valid buffer
             * @throws {$protobuf.util.ProtocolError} If required fields are missing
             */
            TimestampedDouble.decode = function decode(reader, length) {
                if (!(reader instanceof $Reader))
                    reader = $Reader.create(reader);
                var end = length === undefined ? reader.len : reader.pos + length, message = new $root.brefv.scalars.TimestampedDouble();
                while (reader.pos < end) {
                    var tag = reader.uint32();
                    switch (tag >>> 3) {
                    case 1: {
                            message.timestamp = $root.google.protobuf.Timestamp.decode(reader, reader.uint32());
                            break;
                        }
                    case 2: {
                            message.value = reader.double();
                            break;
                        }
                    default:
                        reader.skipType(tag & 7);
                        break;
                    }
                }
                return message;
            };

            /**
             * Decodes a TimestampedDouble message from the specified reader or buffer, length delimited.
             * @function decodeDelimited
             * @memberof brefv.scalars.TimestampedDouble
             * @static
             * @param {$protobuf.Reader|Uint8Array} reader Reader or buffer to decode from
             * @returns {brefv.scalars.TimestampedDouble} TimestampedDouble
             * @throws {Error} If the payload is not a reader or valid buffer
             * @throws {$protobuf.util.ProtocolError} If required fields are missing
             */
            TimestampedDouble.decodeDelimited = function decodeDelimited(reader) {
                if (!(reader instanceof $Reader))
                    reader = new $Reader(reader);
                return this.decode(reader, reader.uint32());
            };

            /**
             * Verifies a TimestampedDouble message.
             * @function verify
             * @memberof brefv.scalars.TimestampedDouble
             * @static
             * @param {Object.<string,*>} message Plain object to verify
             * @returns {string|null} `null` if valid, otherwise the reason why it is not
             */
            TimestampedDouble.verify = function verify(message) {
                if (typeof message !== "object" || message === null)
                    return "object expected";
                if (message.timestamp != null && message.hasOwnProperty("timestamp")) {
                    var error = $root.google.protobuf.Timestamp.verify(message.timestamp);
                    if (error)
                        return "timestamp." + error;
                }
                if (message.value != null && message.hasOwnProperty("value"))
                    if (typeof message.value !== "number")
                        return "value: number expected";
                return null;
            };

            /**
             * Creates a TimestampedDouble message from a plain object. Also converts values to their respective internal types.
             * @function fromObject
             * @memberof brefv.scalars.TimestampedDouble
             * @static
             * @param {Object.<string,*>} object Plain object
             * @returns {brefv.scalars.TimestampedDouble} TimestampedDouble
             */
            TimestampedDouble.fromObject = function fromObject(object) {
                if (object instanceof $root.brefv.scalars.TimestampedDouble)
                    return object;
                var message = new $root.brefv.scalars.TimestampedDouble();
                if (object.timestamp != null) {
                    if (typeof object.timestamp !== "object")
                        throw TypeError(".brefv.scalars.TimestampedDouble.timestamp: object expected");
                    message.timestamp = $root.google.protobuf.Timestamp.fromObject(object.timestamp);
                }
                if (object.value != null)
                    message.value = Number(object.value);
                return message;
            };

            /**
             * Creates a plain object from a TimestampedDouble message. Also converts values to other types if specified.
             * @function toObject
             * @memberof brefv.scalars.TimestampedDouble
             * @static
             * @param {brefv.scalars.TimestampedDouble} message TimestampedDouble
             * @param {$protobuf.IConversionOptions} [options] Conversion options
             * @returns {Object.<string,*>} Plain object
             */
            TimestampedDouble.toObject = function toObject(message, options) {
                if (!options)
                    options = {};
                var object = {};
                if (options.defaults) {
                    object.timestamp = null;
                    object.value = 0;
                }
                if (message.timestamp != null && message.hasOwnProperty("timestamp"))
                    object.timestamp = $root.google.protobuf.Timestamp.toObject(message.timestamp, options);
                if (message.value != null && message.hasOwnProperty("value"))
                    object.value = options.json && !isFinite(message.value) ? String(message.value) : message.value;
                return object;
            };

            /**
             * Converts this TimestampedDouble to JSON.
             * @function toJSON
             * @memberof brefv.scalars.TimestampedDouble
             * @instance
             * @returns {Object.<string,*>} JSON object
             */
            TimestampedDouble.prototype.toJSON = function toJSON() {
                return this.constructor.toObject(this, $protobuf.util.toJSONOptions);
            };

            /**
             * Gets the default type url for TimestampedDouble
             * @function getTypeUrl
             * @memberof brefv.scalars.TimestampedDouble
             * @static
             * @param {string} [typeUrlPrefix] your custom typeUrlPrefix(default "type.googleapis.com")
             * @returns {string} The default type url
             */
            TimestampedDouble.getTypeUrl = function getTypeUrl(typeUrlPrefix) {
                if (typeUrlPrefix === undefined) {
                    typeUrlPrefix = "type.googleapis.com";
                }
                return typeUrlPrefix + "/brefv.scalars.TimestampedDouble";
            };

            return TimestampedDouble;
        })();

        return scalars;
    })();

    return brefv;
})();

$root.google = (function() {

    /**
     * Namespace google.
     * @exports google
     * @namespace
     */
    var google = {};

    google.protobuf = (function() {

        /**
         * Namespace protobuf.
         * @memberof google
         * @namespace
         */
        var protobuf = {};

        protobuf.Timestamp = (function() {

            /**
             * Properties of a Timestamp.
             * @memberof google.protobuf
             * @interface ITimestamp
             * @property {number|Long|null} [seconds] Timestamp seconds
             * @property {number|null} [nanos] Timestamp nanos
             */

            /**
             * Constructs a new Timestamp.
             * @memberof google.protobuf
             * @classdesc Represents a Timestamp.
             * @implements ITimestamp
             * @constructor
             * @param {google.protobuf.ITimestamp=} [properties] Properties to set
             */
            function Timestamp(properties) {
                if (properties)
                    for (var keys = Object.keys(properties), i = 0; i < keys.length; ++i)
                        if (properties[keys[i]] != null)
                            this[keys[i]] = properties[keys[i]];
            }

            /**
             * Timestamp seconds.
             * @member {number|Long} seconds
             * @memberof google.protobuf.Timestamp
             * @instance
             */
            Timestamp.prototype.seconds = $util.Long ? $util.Long.fromBits(0,0,false) : 0;

            /**
             * Timestamp nanos.
             * @member {number} nanos
             * @memberof google.protobuf.Timestamp
             * @instance
             */
            Timestamp.prototype.nanos = 0;

            /**
             * Creates a new Timestamp instance using the specified properties.
             * @function create
             * @memberof google.protobuf.Timestamp
             * @static
             * @param {google.protobuf.ITimestamp=} [properties] Properties to set
             * @returns {google.protobuf.Timestamp} Timestamp instance
             */
            Timestamp.create = function create(properties) {
                return new Timestamp(properties);
            };

            /**
             * Encodes the specified Timestamp message. Does not implicitly {@link google.protobuf.Timestamp.verify|verify} messages.
             * @function encode
             * @memberof google.protobuf.Timestamp
             * @static
             * @param {google.protobuf.ITimestamp} message Timestamp message or plain object to encode
             * @param {$protobuf.Writer} [writer] Writer to encode to
             * @returns {$protobuf.Writer} Writer
             */
            Timestamp.encode = function encode(message, writer) {
                if (!writer)
                    writer = $Writer.create();
                if (message.seconds != null && Object.hasOwnProperty.call(message, "seconds"))
                    writer.uint32(/* id 1, wireType 0 =*/8).int64(message.seconds);
                if (message.nanos != null && Object.hasOwnProperty.call(message, "nanos"))
                    writer.uint32(/* id 2, wireType 0 =*/16).int32(message.nanos);
                return writer;
            };

            /**
             * Encodes the specified Timestamp message, length delimited. Does not implicitly {@link google.protobuf.Timestamp.verify|verify} messages.
             * @function encodeDelimited
             * @memberof google.protobuf.Timestamp
             * @static
             * @param {google.protobuf.ITimestamp} message Timestamp message or plain object to encode
             * @param {$protobuf.Writer} [writer] Writer to encode to
             * @returns {$protobuf.Writer} Writer
             */
            Timestamp.encodeDelimited = function encodeDelimited(message, writer) {
                return this.encode(message, writer).ldelim();
            };

            /**
             * Decodes a Timestamp message from the specified reader or buffer.
             * @function decode
             * @memberof google.protobuf.Timestamp
             * @static
             * @param {$protobuf.Reader|Uint8Array} reader Reader or buffer to decode from
             * @param {number} [length] Message length if known beforehand
             * @returns {google.protobuf.Timestamp} Timestamp
             * @throws {Error} If the payload is not a reader or valid buffer
             * @throws {$protobuf.util.ProtocolError} If required fields are missing
             */
            Timestamp.decode = function decode(reader, length) {
                if (!(reader instanceof $Reader))
                    reader = $Reader.create(reader);
                var end = length === undefined ? reader.len : reader.pos + length, message = new $root.google.protobuf.Timestamp();
                while (reader.pos < end) {
                    var tag = reader.uint32();
                    switch (tag >>> 3) {
                    case 1: {
                            message.seconds = reader.int64();
                            break;
                        }
                    case 2: {
                            message.nanos = reader.int32();
                            break;
                        }
                    default:
                        reader.skipType(tag & 7);
                        break;
                    }
                }
                return message;
            };

            /**
             * Decodes a Timestamp message from the specified reader or buffer, length delimited.
             * @function decodeDelimited
             * @memberof google.protobuf.Timestamp
             * @static
             * @param {$protobuf.Reader|Uint8Array} reader Reader or buffer to decode from
             * @returns {google.protobuf.Timestamp} Timestamp
             * @throws {Error} If the payload is not a reader or valid buffer
             * @throws {$protobuf.util.ProtocolError} If required fields are missing
             */
            Timestamp.decodeDelimited = function decodeDelimited(reader) {
                if (!(reader instanceof $Reader))
                    reader = new $Reader(reader);
                return this.decode(reader, reader.uint32());
            };

            /**
             * Verifies a Timestamp message.
             * @function verify
             * @memberof google.protobuf.Timestamp
             * @static
             * @param {Object.<string,*>} message Plain object to verify
             * @returns {string|null} `null` if valid, otherwise the reason why it is not
             */
            Timestamp.verify = function verify(message) {
                if (typeof message !== "object" || message === null)
                    return "object expected";
                if (message.seconds != null && message.hasOwnProperty("seconds"))
                    if (!$util.isInteger(message.seconds) && !(message.seconds && $util.isInteger(message.seconds.low) && $util.isInteger(message.seconds.high)))
                        return "seconds: integer|Long expected";
                if (message.nanos != null && message.hasOwnProperty("nanos"))
                    if (!$util.isInteger(message.nanos))
                        return "nanos: integer expected";
                return null;
            };

            /**
             * Creates a Timestamp message from a plain object. Also converts values to their respective internal types.
             * @function fromObject
             * @memberof google.protobuf.Timestamp
             * @static
             * @param {Object.<string,*>} object Plain object
             * @returns {google.protobuf.Timestamp} Timestamp
             */
            Timestamp.fromObject = function fromObject(object) {
                if (object instanceof $root.google.protobuf.Timestamp)
                    return object;
                var message = new $root.google.protobuf.Timestamp();
                if (object.seconds != null)
                    if ($util.Long)
                        (message.seconds = $util.Long.fromValue(object.seconds)).unsigned = false;
                    else if (typeof object.seconds === "string")
                        message.seconds = parseInt(object.seconds, 10);
                    else if (typeof object.seconds === "number")
                        message.seconds = object.seconds;
                    else if (typeof object.seconds === "object")
                        message.seconds = new $util.LongBits(object.seconds.low >>> 0, object.seconds.high >>> 0).toNumber();
                if (object.nanos != null)
                    message.nanos = object.nanos | 0;
                return message;
            };

            /**
             * Creates a plain object from a Timestamp message. Also converts values to other types if specified.
             * @function toObject
             * @memberof google.protobuf.Timestamp
             * @static
             * @param {google.protobuf.Timestamp} message Timestamp
             * @param {$protobuf.IConversionOptions} [options] Conversion options
             * @returns {Object.<string,*>} Plain object
             */
            Timestamp.toObject = function toObject(message, options) {
                if (!options)
                    options = {};
                var object = {};
                if (options.defaults) {
                    if ($util.Long) {
                        var long = new $util.Long(0, 0, false);
                        object.seconds = options.longs === String ? long.toString() : options.longs === Number ? long.toNumber() : long;
                    } else
                        object.seconds = options.longs === String ? "0" : 0;
                    object.nanos = 0;
                }
                if (message.seconds != null && message.hasOwnProperty("seconds"))
                    if (typeof message.seconds === "number")
                        object.seconds = options.longs === String ? String(message.seconds) : message.seconds;
                    else
                        object.seconds = options.longs === String ? $util.Long.prototype.toString.call(message.seconds) : options.longs === Number ? new $util.LongBits(message.seconds.low >>> 0, message.seconds.high >>> 0).toNumber() : message.seconds;
                if (message.nanos != null && message.hasOwnProperty("nanos"))
                    object.nanos = message.nanos;
                return object;
            };

            /**
             * Converts this Timestamp to JSON.
             * @function toJSON
             * @memberof google.protobuf.Timestamp
             * @instance
             * @returns {Object.<string,*>} JSON object
             */
            Timestamp.prototype.toJSON = function toJSON() {
                return this.constructor.toObject(this, $protobuf.util.toJSONOptions);
            };

            /**
             * Gets the default type url for Timestamp
             * @function getTypeUrl
             * @memberof google.protobuf.Timestamp
             * @static
             * @param {string} [typeUrlPrefix] your custom typeUrlPrefix(default "type.googleapis.com")
             * @returns {string} The default type url
             */
            Timestamp.getTypeUrl = function getTypeUrl(typeUrlPrefix) {
                if (typeUrlPrefix === undefined) {
                    typeUrlPrefix = "type.googleapis.com";
                }
                return typeUrlPrefix + "/google.protobuf.Timestamp";
            };

            return Timestamp;
        })();

        return protobuf;
    })();

    return google;
})();

module.exports = $root;
