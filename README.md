# Keelson

> **NOTE**: Keelson is in the early phases of development and will undergo significant changes before reaching v1.0. Be aware!

Keelson is a start towards an maritime best practices API specification designed for building distributed maritime applications on top of the [Zenoh](https://github.com/eclipse-zenoh/zenoh) communication protocol. It is provided as free and open-source software under the Apache 2.0 License.

## What Keelson Offers

- **Key Expression Design**: Establish a standardized approach for structuring and managing key spaces in maritime systems.
- **Well-Known Message Formats**: Define message structures to streamline communication.
- **Low Network Overhead and Efficient Data Management**: Optimize the use of bandwidth and data handling.
- **Recommended Interface Formats and Functions**: Provide guidelines and tools to create consistent and maintainable interfaces.
- **Command-Line Tools**: Facilitate development and management with helpful CLI tools.
- **Hardware and Software Connectors**: Seamlessly integrate with various sensors, middleware, and file formats.
- **Data Processors**: Support a wide range of data processing capabilities, from perception to maritime data analysis.

**The keelson protocol is described in details [HERE](./the-keelson-protocol.md)**

## Keelson overview

![sketch](/Doc/keelson_overview.drawio.svg)

## Microservice Architecture (Keelson Platforms)

Keelson serves as RISE Maritime’s backbone for structuring a wide range of microservices, enabling the development of robust platforms for diverse maritime applications. Whether it’s a logging server, unmanned surface vehicle control system, remote control center, or any other maritime-related system, Keelson’s modular architecture allows for flexible integration and scalability.

![sketch](/Doc/keelson_platform.drawio.svg)

The Keelson microservices and connectors are hosted on the [RISE Maritime GitHub page](https://github.com/RISE-Maritime), where you can explore various components, tools, and example applications built using the Keelson framework.

Read more about how and why Keelson leverages Docker for building its microservice architecture [HERE](/Doc/keelson-docker.md).

## Repository structure

This repository is a mono-repo. It contains the following:

- A description of [the keelson protocol](./Doc/the-keelson-protocol.md)
- The well-known message and Generic RPC interface definitions procedures schemas supported by keelson: ([messages/](./messages/README.md))
- Connector implementations towards a multitude middlewares and file formats: ([connectors/](./connectors/README.md))
- Software Development Kits (SDKs) for several languages ([sdks](#keelson-software-development-kits-sdks))
- A [zenoh-cli](https://github.com/MO-RISE/zenoh-cli) codec plugin for keelson data. Bundled with the python SDK.

Releases from this repository consists of two artifacts:

- The SDKs are published to the respective language specific package repositories, see [sdks](./sdks/README.md) for details.
- A docker image containing all the [connectors](./connectors/README.md) is published to GitHub's container registry

## How to use

`Keelson`is but a small set of recommendations on top of Zenoh.First, ensure you are familiar with Zenoh, and then align your application with the key-space design and message formats recommended by `Keelson`. You can either use one of the available SDKs or ensure compliance through manual implementation. 

### Keelson Software Development Kits (SDKs)

- [Python SDK](/sdks/python/README.md)
  - [Releases PIP](https://pypi.org/project/keelson/#history)
- [Javascript SDK](./sdks/js/README.md)
  - [Releases NPM](https://www.npmjs.com/package/keelson-js?activeTab=versions)

These libraries typically contain helping functionality for working with topics, tags and payloads.

### Keelson CLI-Tools

CLI-Tools are a mix of cli-tools for general management of data and messages 

- MCAP
  - Record 
  - Tagg
  - Replay 


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

