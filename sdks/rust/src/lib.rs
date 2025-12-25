//! # Keelson SDK for Rust
//!
//! A Rust Software Development Kit for [Keelson](https://github.com/RISE-Maritime/keelson).
//!
//! This SDK provides utilities for working with Keelson's protocol built on top of Zenoh,
//! including key construction/parsing, envelope wrapping/unwrapping, and protobuf message handling.
//!
//! ## Basic Usage
//!
//! ```rust,no_run
//! use keelson::{construct_pubsub_key, construct_rpc_key, enclose, uncover};
//!
//! // Construct a pub/sub key
//! let key = construct_pubsub_key("base", "entity", "subject", "source", None);
//!
//! // Enclose a payload
//! let payload = b"hello world";
//! let envelope = enclose(payload, None)?;
//!
//! // Uncover an envelope
//! let (received_at, enclosed_at, payload) = uncover(&envelope)?;
//! # Ok::<(), Box<dyn std::error::Error>>(())
//! ```

pub mod envelope;
pub mod error;
pub mod keys;
pub mod proto;
pub mod reflection;
pub mod subjects;

pub use envelope::{enclose, uncover};
pub use error::{Error, Result};
pub use keys::{
    construct_pubsub_key, construct_rpc_key, get_subject_from_pubsub_key, parse_pubsub_key,
    parse_rpc_key,
};
pub use reflection::{
    decode_payload_from_type_name, get_file_descriptor_set_from_type_name,
    get_message_descriptor_from_type_name,
};
pub use subjects::{get_subject_schema, is_subject_well_known, load_subjects};

// Re-export commonly used types
pub use prost_reflect::DynamicMessage;
pub use proto::core::Envelope;
