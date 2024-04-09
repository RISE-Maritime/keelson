# keelson-sdk (python)

A python SDK for [keelson](https://github.com/MO-RISE/keelson).

## Basic usage
See the [tests](https://github.com/MO-RISE/keelson/blob/main/sdks/python/tests/test_sdk.py)

## keelson codec for `zenoh-cli`
The python sdk also bundles a keelson codec for [`zenoh-cli`](https://github.com/MO-RISE/zenoh-cli). It make the following encoders and decoders available:

* keelson-enclose-from-text
* keelson-enclose-from-base64
* keelson-enclose-from-json
* keelson-uncover-to-text
* keelson-uncover-to-base64
* keelson-uncover-to-json
