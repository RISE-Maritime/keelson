# keelson

**NOTE**: Work in progress...

`keelson` is a flexible, fast and resource-friendly communication backbone enabling edge-to-edge, machine-to-machine communication. It leverages [zenoh](https://github.com/eclipse-zenoh/zenoh) for message based communication (PUB/SUB and REQ/REP) and adds an opinionated key-space design and message format on top. 

**TODO**: Image/sketch

If you are new to zenoh, read here: https://zenoh.io/docs/overview/what-is-zenoh/

## Repository structure
The core parts of `keelson` are maintained and developed inside a monorepo (this repo) to ensure consistency and interoperability within versions during early development. At some point in the future, this monorepo may (or may not) be split into separate repositories.

Parts:

* [**Brefv**](./brefv/README.md) defines the key-space design and message formats used by `keelson`.
* [**Infrastructure guidelines**](./infrastructure/README.md) contains bits and pieces to set up a working zenoh network infrastructure suitable for keelson.



Versions:

| keelson version | Zenoh version |
|-----------------|---------------|
| 0.1.0           | 0.10.0-rc     |


## How to release
Make sure to do the following:
* Update version number in [setup.py](./brefv/python/setup.py)
* Update the table just above if neccessary
* Make a new release on Github with name according to version number

