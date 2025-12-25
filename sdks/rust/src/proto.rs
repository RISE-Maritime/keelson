//! Protocol buffer message definitions
//!
//! This module contains all the generated protobuf message types used by Keelson.

/// Core message types (Envelope, etc.)
pub mod core {
    include!(concat!(env!("OUT_DIR"), "/core.rs"));
}

/// Keelson payload message types
pub mod keelson {
    include!(concat!(env!("OUT_DIR"), "/keelson.rs"));
}

/// Foxglove message types
pub mod foxglove {
    include!(concat!(env!("OUT_DIR"), "/foxglove.rs"));
}

/// Interface definitions (RPC, etc.) - no package name in proto files
#[path = ""]
pub mod interfaces {
    include!(concat!(env!("OUT_DIR"), "/_.rs"));
}
