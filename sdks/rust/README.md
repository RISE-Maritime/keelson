# Rust SDK Documentation

# Rust SDK

This is the Rust SDK that mirrors the functionality of the existing Python SDK. 

## Overview

The Rust SDK provides a set of tools and libraries to facilitate the development of applications using the specified protobuf specifications. It is designed to be easy to use and integrate into your Rust projects.

## Getting Started

### Prerequisites

Ensure you have the following installed:

- Rust (via [rustup](https://rustup.rs/))
- Cargo (comes with Rust)
- Protobuf compiler (`protoc`) with Rust plugin

### Building the SDK

To build the Rust SDK, navigate to the `sdks/rust` directory and run:

```bash
cargo build
```

### Generating Rust Code from Protobuf

To generate Rust code from the protobuf specifications, run the following script:

```bash
bash generate_rust.sh
```

This script will use the `protoc` compiler with the Rust plugin to generate the necessary Rust files.

## Usage

After building the SDK and generating the Rust code, you can include the library in your Rust projects by adding it as a dependency in your `Cargo.toml` file.

## Contributing

Contributions are welcome! Please feel free to submit a pull request or open an issue for any enhancements or bug fixes.

## License

This project is licensed under the MIT License. See the LICENSE file for more details.

# Keelson-SDK (rust)

A Rust SDK for [keelson](https://github.com/MO-RISE/keelson).

## Basic usage

See the tests (to be added) for usage examples.

## Development setup

Step 1: Generate protobuf messages

```bash
chmod +x generate_rust.sh
./generate_rust.sh
```

Step 2: Run tests

```bash
cargo test
```