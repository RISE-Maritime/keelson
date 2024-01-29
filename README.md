# keelson

**NOTE**: keelson is in the early phases of development and will undergo significant changes before reaching v1.0. Be aware!

`keelson` is a flexible, fast and resource-friendly communication backbone enabling edge-to-edge, machine-to-machine communication. It leverages [zenoh](https://github.com/eclipse-zenoh/zenoh) for message based communication (PUB/SUB and REQ/REP) and adds an opinionated key-space design and message format on top. If you are new to zenoh, read here: https://zenoh.io/docs/overview/what-is-zenoh/


**TODO**: Image/sketch

This repository is a mono-repository. It contains:

* The key-space design document ([Key-space design document](key-space-design.md))
* The well-known message schemas supported by keelson ([messages/](./messages/README.md))
* Software Development Kits (SDKs) for several languages ([sdks](./sdks/README.md))
* A CLI for easy interoperation with/interrogation of a keelson infrastructure ([CLI](./cli/README.md))
* Interfaces towards a multitude of sensors, messaging protocols and file formats ([interfaces](./interfaces/README.md))
* Guidelines for setting up a zenoh infrastructure suitable for keelson ([infrastructure](./infrastructure/README.md))

Releases from this repository consists of two artifacts:

* The CLI is published to PyPi
* The SDKs are published to the respective language specific package repositories, see [sdks](./sdks/README.md) for details.
* A docker image containing all the [interfaces](./interfaces/README.md) is published to Githubs container registry

## Version compatibility

| keelson version | Zenoh version | Backwards compatible |
|-----------------|---------------|----------------------|
| 0.2.0           | 0.10.1-rc     | Yes                  |
| 0.1.0           | 0.10.0-rc     | -                    |

## How to use

TODO

## For developers

There is a devcontainer setup for the repository which is suitable for the whole monorepo. Use it!

### To make a new release

Make sure to do the following:
* Update version numbers in the respective SDKs
* Update the version number in the CLI
* Update the version table just above if neccessary
* Make a new release on Github with name according to version number

