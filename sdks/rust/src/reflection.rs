//! Dynamic protobuf message reflection and decoding
//!
//! This module provides runtime reflection capabilities for protobuf messages,
//! allowing decoding of messages by type name without compile-time knowledge of the type.
//!
//! # Examples
//!
//! ```rust,no_run
//! use keelson::decode_payload_from_type_name;
//!
//! let payload = b"\x08\x01\x12\x05hello"; // Encoded protobuf message
//! let dynamic_msg = decode_payload_from_type_name(payload, "keelson.TimestampedFloat")?;
//! # Ok::<(), keelson::Error>(())
//! ```

use crate::{Error, Result};
use lazy_static::lazy_static;
use prost_reflect::{DescriptorPool, DynamicMessage, MessageDescriptor};
use std::sync::RwLock;

// Include the compiled file descriptor sets at compile time
const PAYLOADS_DESCRIPTOR: &[u8] =
    include_bytes!(concat!(env!("OUT_DIR"), "/payloads_descriptor.bin"));
const INTERFACES_DESCRIPTOR: &[u8] =
    include_bytes!(concat!(env!("OUT_DIR"), "/interfaces_descriptor.bin"));

lazy_static! {
    /// Global descriptor pool containing all known message types
    static ref DESCRIPTOR_POOL: RwLock<DescriptorPool> = {
        let pool = DescriptorPool::decode(PAYLOADS_DESCRIPTOR)
            .expect("Failed to decode payloads descriptor set");

        // Merge interfaces descriptor set
        let interfaces_pool = DescriptorPool::decode(INTERFACES_DESCRIPTOR)
            .expect("Failed to decode interfaces descriptor set");

        // Merge both pools by creating a new pool with all file descriptors
        let mut combined = DescriptorPool::new();
        for file in pool.files() {
            combined.add_file_descriptor_proto(file.file_descriptor_proto().clone())
                .expect("Failed to add file descriptor");
        }
        for file in interfaces_pool.files() {
            combined.add_file_descriptor_proto(file.file_descriptor_proto().clone())
                .expect("Failed to add file descriptor");
        }

        RwLock::new(combined)
    };
}

/// Get a message descriptor for a given protobuf type name.
///
/// # Arguments
///
/// * `type_name` - The fully qualified protobuf type name (e.g., "keelson.TimestampedFloat")
///
/// # Returns
///
/// A `MessageDescriptor` that can be used to inspect or decode messages of this type.
///
/// # Errors
///
/// Returns an error if the type name is not found in any of the loaded descriptor sets.
///
/// # Examples
///
/// ```rust,no_run
/// use keelson::get_message_descriptor_from_type_name;
///
/// let descriptor = get_message_descriptor_from_type_name("keelson.TimestampedFloat")?;
/// println!("Message type: {}", descriptor.full_name());
/// # Ok::<(), keelson::Error>(())
/// ```
pub fn get_message_descriptor_from_type_name(type_name: &str) -> Result<MessageDescriptor> {
    let pool = DESCRIPTOR_POOL
        .read()
        .map_err(|e| Error::ReflectionError(format!("Failed to acquire read lock: {}", e)))?;

    pool.get_message_by_name(type_name)
        .ok_or_else(|| Error::ReflectionError(format!("Unknown message type: {}", type_name)))
}

/// Decode a protobuf payload into a dynamic message using the type name.
///
/// This function allows decoding protobuf messages at runtime without compile-time
/// knowledge of the message type. The returned `DynamicMessage` can be inspected
/// and manipulated using the prost-reflect API.
///
/// # Arguments
///
/// * `payload` - The encoded protobuf message bytes
/// * `type_name` - The fully qualified protobuf type name (e.g., "keelson.TimestampedFloat")
///
/// # Returns
///
/// A `DynamicMessage` containing the decoded message data.
///
/// # Errors
///
/// Returns an error if:
/// - The type name is not found in the descriptor pool
/// - The payload cannot be decoded as the specified type
///
/// # Examples
///
/// ```rust,no_run
/// use keelson::decode_payload_from_type_name;
///
/// let payload = b"\x08\x01\x12\x05hello"; // Encoded protobuf message
/// let dynamic_msg = decode_payload_from_type_name(payload, "keelson.TimestampedFloat")?;
///
/// // Access fields dynamically
/// // let value = dynamic_msg.get_field_by_name("value");
/// # Ok::<(), keelson::Error>(())
/// ```
pub fn decode_payload_from_type_name(payload: &[u8], type_name: &str) -> Result<DynamicMessage> {
    let descriptor = get_message_descriptor_from_type_name(type_name)?;

    DynamicMessage::decode(descriptor, payload).map_err(Error::ProtobufDecodeError)
}

/// Get the file descriptor set that contains the specified message type.
///
/// This function returns either "payloads" or "interfaces" depending on which
/// descriptor set contains the specified type name.
///
/// # Arguments
///
/// * `type_name` - The fully qualified protobuf type name (e.g., "keelson.TimestampedFloat")
///
/// # Returns
///
/// A string indicating which descriptor set contains the type ("payloads" or "interfaces").
///
/// # Errors
///
/// Returns an error if the type name is not found in any descriptor set.
///
/// # Examples
///
/// ```rust,no_run
/// use keelson::get_file_descriptor_set_from_type_name;
///
/// let descriptor_set = get_file_descriptor_set_from_type_name("keelson.TimestampedFloat")?;
/// assert_eq!(descriptor_set, "payloads");
/// # Ok::<(), keelson::Error>(())
/// ```
pub fn get_file_descriptor_set_from_type_name(type_name: &str) -> Result<&'static str> {
    // First try to find in payloads
    let payloads_pool = DescriptorPool::decode(PAYLOADS_DESCRIPTOR).map_err(|e| {
        Error::ReflectionError(format!("Failed to decode payloads descriptor: {}", e))
    })?;

    if payloads_pool.get_message_by_name(type_name).is_some() {
        return Ok("payloads");
    }

    // Then try interfaces
    let interfaces_pool = DescriptorPool::decode(INTERFACES_DESCRIPTOR).map_err(|e| {
        Error::ReflectionError(format!("Failed to decode interfaces descriptor: {}", e))
    })?;

    if interfaces_pool.get_message_by_name(type_name).is_some() {
        return Ok("interfaces");
    }

    Err(Error::ReflectionError(format!(
        "Type '{}' not found in any descriptor set",
        type_name
    )))
}

#[cfg(test)]
mod tests {
    use super::*;
    use prost_reflect::ReflectMessage;

    #[test]
    fn test_get_message_descriptor_keelson_type() {
        let descriptor = get_message_descriptor_from_type_name("keelson.TimestampedFloat");
        assert!(descriptor.is_ok());
        let desc = descriptor.unwrap();
        assert_eq!(desc.full_name(), "keelson.TimestampedFloat");
    }

    #[test]
    fn test_get_message_descriptor_foxglove_type() {
        let descriptor = get_message_descriptor_from_type_name("foxglove.Color");
        assert!(descriptor.is_ok());
        let desc = descriptor.unwrap();
        assert_eq!(desc.full_name(), "foxglove.Color");
    }

    #[test]
    fn test_get_message_descriptor_interface_type() {
        let descriptor = get_message_descriptor_from_type_name("ConfigurableSuccessResponse");
        assert!(descriptor.is_ok());
        let desc = descriptor.unwrap();
        assert_eq!(desc.full_name(), "ConfigurableSuccessResponse");
    }

    #[test]
    fn test_get_message_descriptor_unknown_type() {
        let descriptor = get_message_descriptor_from_type_name("unknown.Type");
        assert!(descriptor.is_err());
        match descriptor {
            Err(Error::ReflectionError(msg)) => {
                assert!(msg.contains("Unknown message type"));
            }
            _ => panic!("Expected ReflectionError"),
        }
    }

    #[test]
    fn test_decode_payload_from_type_name() {
        // Create a simple TimestampedFloat message
        use crate::proto::keelson::TimestampedFloat;
        use prost::Message;
        use prost_types::Timestamp;

        let msg = TimestampedFloat {
            timestamp: Some(Timestamp {
                seconds: 1234567890,
                nanos: 123456789,
            }),
            value: 42.5,
        };

        let encoded = msg.encode_to_vec();

        // Decode dynamically
        let dynamic_msg = decode_payload_from_type_name(&encoded, "keelson.TimestampedFloat");
        assert!(dynamic_msg.is_ok());

        let decoded = dynamic_msg.unwrap();
        assert_eq!(decoded.descriptor().full_name(), "keelson.TimestampedFloat");
    }

    #[test]
    fn test_get_file_descriptor_set_keelson_type() {
        let result = get_file_descriptor_set_from_type_name("keelson.TimestampedFloat");
        assert!(result.is_ok());
        assert_eq!(result.unwrap(), "payloads");
    }

    #[test]
    fn test_get_file_descriptor_set_foxglove_type() {
        let result = get_file_descriptor_set_from_type_name("foxglove.Color");
        assert!(result.is_ok());
        assert_eq!(result.unwrap(), "payloads");
    }

    #[test]
    fn test_get_file_descriptor_set_interface_type() {
        let result = get_file_descriptor_set_from_type_name("ConfigurableSuccessResponse");
        assert!(result.is_ok());
        assert_eq!(result.unwrap(), "interfaces");
    }

    #[test]
    fn test_get_file_descriptor_set_unknown_type() {
        let result = get_file_descriptor_set_from_type_name("unknown.Type");
        assert!(result.is_err());
    }
}
