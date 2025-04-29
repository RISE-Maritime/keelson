# Keelson

> **NOTE**: Keelson is in the early phases of development and will undergo significant changes before reaching v1.0. Be aware!

Keelson is a start towards an maritime best practices API specification designed for building distributed maritime applications on top of the [Zenoh](https://github.com/eclipse-zenoh/zenoh) communication protocol. It is provided as free and open-source software under the Apache 2.0 License.

## What is a Keelson?

A keelson is a longitudinal structure running above and fastened to the keel of a ship to strengthen its framework. In the context of this project, Keelson serves as the backbone for structuring and managing flexible maritime data backbone for applications and microservices.

## What Keelson Offers

- **Key Expression Design**: Establish a standardized approach for structuring and managing key spaces in maritime systems.
- **Well-Known Message Formats**: Define message structures to streamline communication.
- **Low Network Overhead and Efficient Data Management**: Optimize the use of bandwidth and data handling.
- **Recommended Interface Formats and Functions**: Provide guidelines and tools to create consistent and maintainable interfaces.
- **CLI - Command-Line Tools**: Facilitate development and management with helpful CLI tools.
- **CONNECTOR´s to Hardware and Software**: Seamlessly integrate with various sensors, middleware, and file formats.
- **PROCESSOR´s**: Support a wide range of data processing capabilities, from perception to maritime data analysis.

## Repository structure

This repository is a mono-repo. It contains the following:

- A description of [the keelson protocol](./docs/the-keelson-protocol.md)
- The well-known message and generic interface definitions procedures schemas [Keelson messages](./messages/README_messages.md)
- Connector, implementations of interfaces towards software or hardware. This will in future be moved to respective standalone repositories: ([connectors/](./connectors/README.md))
- Software Development Kits (SDKs) for several languages ([sdks](#keelson-software-development-kits-sdks))

Releases from this repository consists of two artifacts:

- The SDKs are published to the respective language specific package repositories, see [sdks](./sdks/README.md) for details.
- A docker image containing all the [connectors](./connectors/README.md) is published to GitHub's container registry

## Keelson component overview

![sketch](/docs/keelson_overview.drawio.svg)

## Keelson Protocol a design recommendation 

In short to assists microservice architecture compatibility, keelson has opinions and recommendation about:

- The format of the key used when publishing data to zenoh
- The format of the data published to zenoh
- The format of the key used when declaring a queryable (i.e. RPC endpoint) in zenoh
- The format of the requests and responses exchanged via a queryable (i.e. RPC endpoint) in zenoh

**More detailed information about Keelson Protocol found in [README Protocol](./docs/README-protocol.md)**

## Payload & Message defections

Keelson provides a set of recommended message definitions both for publish and subscribe data along with procedures calls / rest API / queryable. Defections are made with [Protocol Buffers "protbuf"](https://protobuf.dev/) for serializing structured data. It is not limiting you to apply same structure to XML, JSON or any other. Preferred way Keelson recommend protobuf.


**Quick access to [Message definitions "Payloads"](./messages/payloads/)**

### Subject in PUBSUB & RPC

In the `pubsub`(publish and subscribe) & `rpc` (remote procedure call) key expression contains "subject" it defines, the payload message format/protocol definition used for that message. It works by having a lookup in [subjects.yml](./messages/subjects.yaml) file that defines the specific subject to a protobuffer message definition.

Quick access to [Subjects.yml](./messages/subjects.yaml)

## Microservice Architecture (Keelson Platforms)

Keelson serves as RISE Maritime’s backbone for structuring a wide range of microservices, enabling the development of robust platforms for diverse maritime applications. Whether it’s a logging server, unmanned surface vehicle control system, remote control center, or any other maritime-related system, Keelson’s modular architecture allows for flexible integration and scalability.

![sketch](/docs/keelson_platform.drawio.svg)

The Keelson microservices and connectors are hosted on the [RISE Maritime GitHub page](https://github.com/RISE-Maritime), where you can explore various components, tools, and example applications built using the Keelson framework.

Read more about how and why Keelson leverages Docker for building its microservice architecture [HERE](/docs/keelson-docker.md).

## How to use

`Keelson`is but a small set of recommendations on top of Zenoh first, ensure you are familiar with Zenoh, and then align your application with the key-space design and message formats recommended by `Keelson`. You can either use one of the available SDKs or ensure compliance through manual implementation. 

### Keelson Software Development Kits (SDKs)

- [Python SDK](/sdks/python/README.md)
  - [Releases PIP](https://pypi.org/project/keelson/#history)
  - [zenoh-cli](https://github.com/MO-RISE/zenoh-cli) codec plugin for keelson data. Bundled with the python SDK.
- [Javascript SDK](./sdks/js/README.md)
  - [Releases NPM](https://www.npmjs.com/package/keelson-js?activeTab=versions)

These libraries typically contain helping functionality for working with topics, tags and payloads.

## Keelson Compatible CONNECTORS

[Link to: Github Keelson Connectors](https://github.com/RISE-Maritime?q=keelson-connector&type=all&language=&sort=)

### TODO: Move internal connectors to standalone Repositories

- [Klog](./connectors/klog/README.md)
- [MCAP](./connectors/mcap/README.md)
- [Mediamtx](./connectors/mediamtx/README.md)
- [Mockups](./connectors/mockups/README.md)
- [Opendlv](./connectors/opendlv/README.md)
- [RTSP](./connectors/rtsp/README.md)

## Keelson Compatible PROCESSORS

[Link to: Github Keelson PROCESSORS](https://github.com/RISE-Maritime?q=keelson-processor&type=all&language=&sort=)

## For developers

There is a Dev Container setup for the repository which is suitable for the whole monorepo. Use it!

### How to Make a New Release

Make sure to do the following:

- Update version numbers in the respective SDKs
- Make a new release on Github with name according to version number

## Contribute to Keelson

- Clone repo and make a new branch with name describing the feature or change
- Make a pull request and set someone from RISE Maritime developers as reviewer
  - Tips: Make changes ready for pre-release version your contribution will be processed fast  

For convenience, extension / microservices should add a [Github topic](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/classifying-your-repository-with-topics) `keelson-<extension_type>` to its repository to be visible in [#keelson-message](https://github.com/topics/keelson-message), [#keelson-processor](https://github.com/topics/keelson-processor) and [#keelson-connector](https://github.com/topics/keelson-connector) respectively.

