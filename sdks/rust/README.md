# Keelson SDK (Rust)

A Rust Software Development Kit for [Keelson](https://github.com/RISE-Maritime/keelson).

## Overview

This SDK provides a comprehensive Rust implementation of the Keelson protocol, built on top of the [Zenoh](https://github.com/eclipse-zenoh/zenoh) communication framework. It offers utilities for constructing and parsing key expressions, wrapping and unwrapping message envelopes, and working with well-known maritime data subjects.

## Features

- **Key Expression Management**: Construct and parse Zenoh key expressions for pub/sub and RPC interactions
- **Envelope Handling**: Wrap and unwrap payloads in Keelson envelopes with timestamps
- **Well-Known Subjects**: Built-in registry of maritime data subjects and their protobuf schemas
- **Protocol Buffer Support**: Full protobuf message support for all Keelson message types
- **Type Safety**: Idiomatic Rust with strong typing and comprehensive error handling

## Installation

Add this to your `Cargo.toml`:

```toml
[dependencies]
keelson = "0.4.4"
```

## Basic Usage

### Key Construction

```rust
use keelson::{construct_pubsub_key, construct_rpc_key};

// Construct a pub/sub key
let pubsub_key = construct_pubsub_key(
    "realm/site",
    "vessel_1",
    "location_fix",
    "gps_sensor",
    None
);
// Result: "realm/site/@v0/vessel_1/pubsub/location_fix/gps_sensor"

// Construct an RPC key
let rpc_key = construct_rpc_key(
    "realm/site",
    "vessel_1",
    "get_config",
    "nav_system"
);
// Result: "realm/site/@v0/vessel_1/@rpc/get_config/nav_system"
```

### Key Parsing

```rust
use keelson::{parse_pubsub_key, get_subject_from_pubsub_key};

let key = "realm/site/@v0/vessel_1/pubsub/location_fix/gps_sensor";
let parsed = parse_pubsub_key(key)?;

println!("Entity: {}", parsed.get("entity_id").unwrap());
println!("Subject: {}", parsed.get("subject").unwrap());

// Quick subject extraction
let subject = get_subject_from_pubsub_key(key)?;
```

### Envelope Handling

```rust
use keelson::{enclose, uncover};

// Wrap a payload in an envelope
let payload = b"sensor data";
let envelope = enclose(payload, None)?;

// Unwrap an envelope
let (received_at, enclosed_at, payload) = uncover(&envelope)?;
println!("Received at: {} ns", received_at);
println!("Enclosed at: {} ns", enclosed_at);
```

### Well-Known Subjects

```rust
use keelson::{is_subject_well_known, get_subject_schema};

// Check if a subject is well-known
if is_subject_well_known("location_fix") {
    println!("Subject is well-known!");
}

// Get the protobuf type for a subject
let schema = get_subject_schema("location_fix");
// Returns: Some("foxglove.LocationFix")
```

## Well-Known Subjects

The SDK includes all standard Keelson subjects defined in `subjects.yaml`:

- **Navigation**: `location_fix`, `heading_true_north_deg`, `course_over_ground_deg`
- **Motion**: `roll_deg`, `pitch_deg`, `yaw_deg`, `speed_over_ground_knots`
- **Vessel Info**: `vessel_type`, `mmsi_number`, `imo_number`, `nav_status`
- **Environment**: `air_temperature_celsius`, `water_temperature_celsius`, `true_wind_speed_mps`
- **Sensors**: `image_compressed`, `laser_scan`, `point_cloud`, `radar_spoke`
- **Status**: `sensor_status`, `network_status`, `simulation_status`
- And many more...

See the [full subjects list](https://github.com/RISE-Maritime/keelson/blob/main/messages/subjects.yaml).

## Protocol Buffer Messages

All Keelson protobuf message types are available through the `proto` module:

```rust
use keelson::proto::keelson::TimestampedFloat;
use keelson::proto::foxglove::LocationFix;
use prost::Message;

// Create a timestamped float
let mut data = TimestampedFloat::default();
data.value = 42.0;

// Serialize to bytes
let bytes = data.encode_to_vec();
```

## Error Handling

The SDK uses the `Result<T, Error>` pattern for all fallible operations:

```rust
use keelson::{Error, Result};

fn process_key(key: &str) -> Result<String> {
    let subject = keelson::get_subject_from_pubsub_key(key)?;

    if !keelson::is_subject_well_known(&subject) {
        return Err(Error::UnknownSubject(subject));
    }

    Ok(subject)
}
```

## Building from Source

```bash
cd sdks/rust
cargo build --release
```

## Running Tests

```bash
cargo test
```

## Documentation

Generate and view the full API documentation:

```bash
cargo doc --open
```

## Dependencies

- **zenoh** (^1.2.1) - Zenoh pub/sub and RPC framework
- **prost** (^0.13) - Protocol Buffers implementation
- **serde** (^1.0) - Serialization framework
- **serde_yaml** (^0.9) - YAML parsing for subjects

## License

Apache License 2.0

## Contributing

See the main [Keelson repository](https://github.com/RISE-Maritime/keelson) for contribution guidelines.

## Version Compatibility

This Rust SDK maintains feature parity with the Python SDK version 0.4.4.
