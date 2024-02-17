# keelson

**NOTE**: keelson is in the early phases of development and will undergo significant changes before reaching v1.0. Be aware!

`keelson` is a flexible, fast and resource-friendly communication backbone enabling edge-to-edge, machine-to-machine communication. It leverages [zenoh](https://github.com/eclipse-zenoh/zenoh) for message based communication (PUB/SUB and REQ/REP) and adds an opinionated key-space design and message format on top. If you are new to zenoh, read here: https://zenoh.io/docs/overview/what-is-zenoh/

This repository is a mono-repo. It contains:

* The key-space design document ([Key-space design document](keelson-key-space-design.md))
* The well-known message schemas supported by keelson ([messages/](./messages/README.md))
* Software Development Kits (SDKs) for several languages ([sdks](./sdks/README.md))
* A [zenoh-cli](https://github.com/MO-RISE/zenoh-cli) codec plugin for keelson data. Bundled with the python SDK.
* Interfaces towards a multitude of sensors, middlewares and file formats ([interfaces](./interfaces/README.md))

Releases from this repository consists of two artifacts:

* The SDKs are published to the respective language specific package repositories, see [sdks](./sdks/README.md) for details.
* A docker image containing all the [interfaces](./interfaces/README.md) is published to Githubs container registry

## Version compatibility

| keelson version | Zenoh version | Backwards compatible |
|-----------------|---------------|----------------------|
| 0.2.0           | 0.10.1-rc     | No                   |
| 0.1.0           | 0.10.0-rc     | -                    |

## How to use

### Basic usage

`TODO`

### Infrastructure
A good first overview of the possible infrastructure setups using Zenoh can be found [here](https://zenoh.io/docs/getting-started/deployment/). In general, keelson supports any infrastructure constellation that is supported by Zenoh but has some additional recommendations:

* mTLS should be used for for router-to-router connections, see [here](https://zenoh.io/docs/manual/tls/)
* proper role-based access-control should be used as soon as Zenoh support this.

In order to provide "seamless" connectivity between several geographically distributed edge deployments at least one router must be deployed in the "cloud" with a static address. This router will act as a proxy between the edge deployments.

## For developers

There is a devcontainer setup for the repository which is suitable for the whole monorepo. Use it!

### To make a new release

Make sure to do the following:
* Update version numbers in the respective SDKs
* Update the version number in the CLI
* Update the version table just above if neccessary
* Make a new release on Github with name according to version number

