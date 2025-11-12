//! Envelope wrapping and unwrapping
//!
//! This module provides utilities for wrapping payloads in Keelson Envelopes
//! and unwrapping them.

use crate::error::{Error, Result};
use crate::proto::core::Envelope;
use prost::Message;
use prost_types::Timestamp;
use std::time::{SystemTime, UNIX_EPOCH};

/// Enclose a payload in a Keelson Envelope.
///
/// # Arguments
///
/// * `payload` - The payload bytes to enclose
/// * `enclosed_at` - Optional timestamp (nanoseconds since epoch). If None, uses current time.
///
/// # Returns
///
/// The serialized envelope as a Vec<u8>.
///
/// # Errors
///
/// Returns an error if timestamp conversion fails or encoding fails.
///
/// # Example
///
/// ```rust
/// use keelson::enclose;
///
/// let payload = b"hello world";
/// let envelope = enclose(payload, None).unwrap();
/// ```
pub fn enclose(payload: &[u8], enclosed_at: Option<i64>) -> Result<Vec<u8>> {
    let timestamp = match enclosed_at {
        Some(nanos) => nanos_to_timestamp(nanos)?,
        None => current_timestamp()?,
    };

    let envelope = Envelope {
        enclosed_at: Some(timestamp),
        payload: payload.to_vec(),
    };

    let mut buf = Vec::new();
    envelope.encode(&mut buf)?;
    Ok(buf)
}

/// Uncover a Keelson Envelope to extract the payload.
///
/// # Arguments
///
/// * `message` - The serialized envelope bytes
///
/// # Returns
///
/// A tuple of (received_at, enclosed_at, payload) where times are in nanoseconds since epoch.
///
/// # Errors
///
/// Returns an error if the envelope cannot be decoded or timestamp is invalid.
///
/// # Example
///
/// ```rust
/// use keelson::{enclose, uncover};
///
/// let payload = b"hello world";
/// let envelope = enclose(payload, None).unwrap();
/// let (received_at, enclosed_at, extracted_payload) = uncover(&envelope).unwrap();
/// assert_eq!(payload, extracted_payload.as_slice());
/// ```
pub fn uncover(message: &[u8]) -> Result<(i64, i64, Vec<u8>)> {
    let envelope = Envelope::decode(message)?;

    let received_at = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_err(|_| Error::InvalidTimestamp)?
        .as_nanos() as i64;

    let enclosed_at = envelope
        .enclosed_at
        .ok_or(Error::InvalidTimestamp)
        .and_then(|ts| timestamp_to_nanos(&ts))?;

    Ok((received_at, enclosed_at, envelope.payload))
}

/// Convert nanoseconds since epoch to a protobuf Timestamp.
fn nanos_to_timestamp(nanos: i64) -> Result<Timestamp> {
    let secs = nanos / 1_000_000_000;
    let nanos = (nanos % 1_000_000_000) as i32;

    Ok(Timestamp {
        seconds: secs,
        nanos,
    })
}

/// Convert a protobuf Timestamp to nanoseconds since epoch.
fn timestamp_to_nanos(timestamp: &Timestamp) -> Result<i64> {
    Ok(timestamp.seconds * 1_000_000_000 + timestamp.nanos as i64)
}

/// Get the current timestamp as a protobuf Timestamp.
fn current_timestamp() -> Result<Timestamp> {
    let now = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_err(|_| Error::InvalidTimestamp)?;

    Ok(Timestamp {
        seconds: now.as_secs() as i64,
        nanos: now.subsec_nanos() as i32,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_enclose_uncover() {
        let test_payload = b"test";
        let envelope = enclose(test_payload, None).unwrap();
        let (received_at, enclosed_at, payload) = uncover(&envelope).unwrap();

        assert_eq!(test_payload, payload.as_slice());
        assert!(received_at >= enclosed_at);
    }

    #[test]
    fn test_enclose_with_timestamp() {
        let test_payload = b"test";
        let timestamp = 1234567890_123456789i64;
        let envelope = enclose(test_payload, Some(timestamp)).unwrap();
        let (_, enclosed_at, payload) = uncover(&envelope).unwrap();

        assert_eq!(test_payload, payload.as_slice());
        assert_eq!(enclosed_at, timestamp);
    }

    #[test]
    fn test_nanos_to_timestamp() {
        let nanos = 1234567890_123456789i64;
        let ts = nanos_to_timestamp(nanos).unwrap();
        assert_eq!(ts.seconds, 1234567890);
        assert_eq!(ts.nanos, 123456789);
    }

    #[test]
    fn test_timestamp_to_nanos() {
        let ts = Timestamp {
            seconds: 1234567890,
            nanos: 123456789,
        };
        let nanos = timestamp_to_nanos(&ts).unwrap();
        assert_eq!(nanos, 1234567890_123456789i64);
    }
}
