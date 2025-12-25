//! Error types for the Keelson SDK

use thiserror::Error;

/// Result type alias for Keelson operations
pub type Result<T> = std::result::Result<T, Error>;

/// Error types that can occur in the Keelson SDK
#[derive(Error, Debug)]
pub enum Error {
    /// Error parsing a key expression
    #[error("Failed to parse key: {0}")]
    KeyParseError(String),

    /// Error with protobuf decoding
    #[error("Protobuf decode error: {0}")]
    ProtobufDecodeError(#[from] prost::DecodeError),

    /// Error with protobuf encoding
    #[error("Protobuf encode error: {0}")]
    ProtobufEncodeError(#[from] prost::EncodeError),

    /// Error with subject handling
    #[error("Subject error: {0}")]
    SubjectError(String),

    /// Error loading subjects YAML
    #[error("YAML error: {0}")]
    YamlError(#[from] serde_yaml::Error),

    /// Error with base64 encoding/decoding
    #[error("Base64 error: {0}")]
    Base64Error(#[from] base64::DecodeError),

    /// Error with JSON encoding/decoding
    #[error("JSON error: {0}")]
    JsonError(#[from] serde_json::Error),

    /// General I/O error
    #[error("IO error: {0}")]
    IoError(#[from] std::io::Error),

    /// Invalid timestamp
    #[error("Invalid timestamp")]
    InvalidTimestamp,

    /// Subject not well-known
    #[error("Subject '{0}' is not well-known")]
    UnknownSubject(String),

    /// Invalid codec operation
    #[error("Invalid codec operation: {0}")]
    CodecError(String),

    /// Error with protobuf reflection
    #[error("Reflection error: {0}")]
    ReflectionError(String),
}
