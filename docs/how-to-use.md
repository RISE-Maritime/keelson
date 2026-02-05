# How to use

`keelson` currently provides Software Development Kits (SDKs) in two languages: `Python` and `Javascript/Typescript`. Using an SDK is not a requirement to adhere to the protocol defined by keelson but it typically helps.

The `Python` SDK is available from [PyPI](https://pypi.org/project/keelson).

The `Javascript/Typescript` SDK is available from [NPM](https://www.npmjs.com/package/keelson-js).

Further, the [`zenoh-cli`](https://pypi.org/project/zenoh-cli) (also written in `Python`) can be of great help to probe an existing `Zenoh` infrastructure. `keelson` provides a plugin to `zenoh-cli`(part of the `Python` SDK) to enhance `zenoh-cli` with an understanding of its well-known subjects and protobuf types.

## Examples

=== "Python"

    **Publishing data to Zenoh**

    ``` python
    import zenoh
    import keelson
    from keelson.scaffolding import create_zenoh_config

    # Open a Zenoh session
    conf = create_zenoh_config(mode="peer")
    session = zenoh.open(conf)

    # Construct a key expression for a well-known subject
    key = keelson.construct_pubsub_key(
        base_path="my_realm",
        entity_id="my_vessel",
        subject="location_fix",
        source_id="gps/0",
    )

    # Declare a publisher
    pub = session.declare_publisher(key)

    # Build a protobuf payload (e.g. a LocationFix)
    from keelson.payloads.foxglove.LocationFix_pb2 import LocationFix

    payload = LocationFix()
    payload.latitude = 57.706
    payload.longitude = 11.937

    # Enclose the serialized payload in a keelson envelope and publish
    envelope = keelson.enclose(payload.SerializeToString())
    pub.put(envelope)

    session.close()
    ```

    **Subscribing to data from Zenoh**

    ``` python
    import zenoh
    import keelson
    from keelson.scaffolding import create_zenoh_config

    conf = create_zenoh_config(mode="peer")
    session = zenoh.open(conf)

    def on_sample(sample):
        key = str(sample.key_expr)
        received_at, enclosed_at, payload = keelson.uncover(
            sample.payload.to_bytes()
        )

        # Get the subject from the key to know how to decode
        subject = keelson.get_subject_from_pubsub_key(key)
        schema = keelson.get_subject_schema(subject)
        message = keelson.decode_protobuf_payload_from_type_name(payload, schema)
        print(f"Received on {key}: {message}")

    # Subscribe using a wildcard key expression
    sub = session.declare_subscriber(
        "my_realm/v0/my_vessel/pubsub/**",
        on_sample,
    )

    # Keep running until interrupted
    import time
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass

    session.close()
    ```

=== "Javascript"

    The Javascript/Typescript SDK is available on [NPM](https://www.npmjs.com/package/keelson-js). See the [JS SDK README](https://github.com/RISE-Maritime/keelson/tree/main/sdks/js) for usage examples and API documentation.
