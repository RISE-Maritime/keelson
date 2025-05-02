# Keelson

> **NOTE**: Keelson is in the early phases of development and will undergo significant changes before reaching v1.0. Be aware!

Keelson is a start towards an maritime best practices API specification designed for building distributed maritime applications on top of the [Zenoh](https://github.com/eclipse-zenoh/zenoh) communication protocol. It is provided as free and open-source software under the Apache 2.0 License.

See the docs for details about usage -> [docs](https://RISE-Maritime.github.io/keelson)

## Repository structure

This repository is a mono-repo. It contains the following:

- [The well-known subjects and messages](./messages/)
- [Generic interface definitions](./interfaces/) 
- [A few, core connector implementations](./connectors/)
- [Software Development Kits (SDKs) for several languages](./sdks/)

Releases from this repository consists of two artifacts:

- The SDKs are published to the respective language specific package repositories.
- A docker image containing all the connectors is published to GitHub's container registry


## For developers

There is a Dev Container setup for the repository which is suitable for the whole monorepo. Use it!

### Documentation
Is built using [`mkdocs-material`](https://squidfunk.github.io/mkdocs-material/). For local development:
* Generate docs for well-known subjects and types: `./generate_docs.sh`
* Serve the docs locally: `mkdocs serve`

### How to Make a New Release

Make sure to do the following:

- Update version numbers in the respective SDKs
- Make a new release on Github with name according to version number

### Contribute to Keelson

- Clone repo and make a new branch with name describing the feature or change
- Make a pull request and set someone from RISE Maritime developers as reviewer
  - Tips: Make changes ready for pre-release version your contribution will be processed fast  

For convenience, extension / microservices should add a [Github topic](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/classifying-your-repository-with-topics) `keelson-<extension_type>` to its repository to be visible in [#keelson-message](https://github.com/topics/keelson-message), [#keelson-processor](https://github.com/topics/keelson-processor) and [#keelson-connector](https://github.com/topics/keelson-connector) respectively.

