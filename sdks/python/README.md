# keelson-sdk (python)

A python sdk for keelson.

Not yet available on PyPi.

Install as: `pip install "git+https://github.com/MO-RISE/keelson.git@<TAG>#subdirectory=sdks/python"`
substituting `<TAG>` for whatever you want to install.

## Basic usage
See [test](./tests/)

## keelson codec for `zenoh-cli`
The python sdk also bundles a keelson codec for [`zenoh-cli`](). It make the following encoders and decoders available:

* keelson-enclose-from-text
* keelson-enclose-from-base64
* keelson-enclose-from-json
* keelson-uncover-to-text
* keelson-uncover-to-base64
* keelson-uncover-to-json