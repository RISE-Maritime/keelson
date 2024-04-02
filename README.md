# keelson

> **NOTE**: keelson is in the early phases of development and will undergo significant changes before reaching v1.0. Be aware!

keelson is an API specification tailored for digital maritime systems for building distributed systems on top of the [zenoh](https://github.com/eclipse-zenoh/zenoh) communication protocol. It is provided as free and open-source software under the Apache 2.0 License.

The keelson protocol is described [HERE](./the-keelson-protocol.md).


TODO: A nice graphic here would be nice...


**Repository structure**

This repository is a mono-repo. It contains the following (in order of ):

* A description of [the keelson protocol](./the-keelson-protocol.md)
* The well-known message schemas supported by keelson: ([messages/](./messages/README.md))
* Generic RPC interface definitions for some use cases: ([interfaces/](./interfaces/README.md))
* Connector implementations towards a multitude of sensors, middlewares and file formats: ([connectors/](./connectors/README.md))
* Software Development Kits (SDKs) for several languages ([sdks](./sdks/README.md))
* A [zenoh-cli](https://github.com/MO-RISE/zenoh-cli) codec plugin for keelson data. Bundled with the python SDK.


Releases from this repository consists of two artifacts:

* The SDKs are published to the respective language specific package repositories, see [sdks](./sdks/README.md) for details.
* A docker image containing all the [connectors](./connectors/README.md) is published to Githubs container registry


## How to use

`keelson`is but a small set of rules on top of zenoh. Make sure to first be aquainted with zenoh and then ensure your application adheres to the key-space design and message format advocated and supported by `keelson`, either through the useage of one of the available SDKs or just by compliance.

See details of the SDKs for usage examples in the respective languages.

## For developers

There is a devcontainer setup for the repository which is suitable for the whole monorepo. Use it!

### To make a new release

Make sure to do the following:
* Update version numbers in the respective SDKs
* Make a new release on Github with name according to version number

### Extensions

**Work-In-Progress**

Extensions to `keelson` can be of three different types:
* additional well-known subjects/payloads/messages, i.e. a `message` extension
* additional generic RPC interfaces, i.e. a `interface` extension
* additional connector implementations, i.e. a `connector` extension

For convenience, extensions should:
* be named as: `keelson-<extension_type>-<extension_name>`, for example `keelson-connector-mavlink`
* add a [Github topic](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/classifying-your-repository-with-topics) `keelson-<extension_type>` to its repository to be visible in [#keelson-message](https://github.com/topics/keelson-message), [#keelson-interface](https://github.com/topics/keelson-interface) and [#keelson-connector](https://github.com/topics/keelson-connector) respectively.


