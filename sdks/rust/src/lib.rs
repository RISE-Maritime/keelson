//! Keelson SDK (Rust) - Core API for maritime applications
//! 
//! This SDK provides a Rust interface to the Keelson protocol for building
//! distributed maritime applications on top of the Zenoh communication protocol.

use std::collections::HashMap;
use std::fs;
use std::path::Path;
use std::sync::OnceLock;
use std::time::{SystemTime, UNIX_EPOCH};

use prost::Message;
use prost_types::Timestamp;

// Re-export core types
pub use crate::core::{Envelope, KeyEnvelopePair};

// Generated Protobuf modules
pub mod core;
pub mod payloads;
pub mod interfaces;

// Codec functionality
pub mod codec;

// --- Key construction and parsing ---
const KEELSON_PUB_SUB_KEY_FORMAT: &str = "{base_path}/@v0/{entity_id}/pubsub/{subject}/{source_id}";
const KEELSON_REQ_REP_KEY_FORMAT: &str = "{base_path}/@v0/{entity_id}/@rpc/{procedure}/{source_id}";

/// Construct a key expression for a publish subscribe interaction (Observable).
pub fn construct_pubsub_key(
    base_path: &str,
    entity_id: &str,
    subject: &str,
    source_id: &str,
) -> String {
    if !is_subject_well_known(subject) {
        eprintln!("Warning: Subject '{}' is NOT well-known!", subject);
    }
    
    KEELSON_PUB_SUB_KEY_FORMAT
        .replace("{base_path}", base_path)
        .replace("{entity_id}", entity_id)
        .replace("{subject}", subject)
        .replace("{source_id}", source_id)
}

/// Construct a key expression for a request reply interaction (Queryable/RPC).
pub fn construct_rpc_key(
    base_path: &str,
    entity_id: &str,
    procedure: &str,
    source_id: &str,
) -> String {
    KEELSON_REQ_REP_KEY_FORMAT
        .replace("{base_path}", base_path)
        .replace("{entity_id}", entity_id)
        .replace("{procedure}", procedure)
        .replace("{source_id}", source_id)
}

/// Parse a key expression for a publish subscribe interaction.
/// Returns (base_path, entity_id, subject, source_id)
pub fn parse_pubsub_key(key: &str) -> Option<(String, String, String, String)> {
    let parts: Vec<&str> = key.split('/').collect();
    if parts.len() < 6 {
        return None;
    }
    // Skip @v0 at index 1 and "pubsub" at index 3
    Some((
        parts[0].to_string(),
        parts[2].to_string(),
        parts[4].to_string(),
        parts[5..].join("/"),
    ))
}

/// Parse a key expression for a request reply interaction.
/// Returns (base_path, entity_id, procedure, source_id)
pub fn parse_rpc_key(key: &str) -> Option<(String, String, String, String)> {
    let parts: Vec<&str> = key.split('/').collect();
    if parts.len() < 6 {
        return None;
    }
    // Skip @v0 at index 1 and "@rpc" at index 3  
    Some((
        parts[0].to_string(),
        parts[2].to_string(),
        parts[4].to_string(),
        parts[5..].join("/"),
    ))
}

/// Get the subject from a pubsub key expression.
pub fn get_subject_from_pubsub_key(key: &str) -> Option<String> {
    parse_pubsub_key(key).map(|(_, _, subject, _)| subject)
}

// --- Envelope helpers ---

/// Enclose a payload in an envelope.
pub fn enclose(payload: Vec<u8>, enclosed_at: Option<Timestamp>) -> Vec<u8> {
    let envelope = Envelope {
        enclosed_at: enclosed_at.or_else(|| Some(current_timestamp())),
        payload,
    };
    envelope.encode_to_vec()
}

/// Uncover a Keelson message that is an envelope.
/// Returns (received_at, enclosed_at, payload)
pub fn uncover(encoded_envelope: &[u8]) -> Option<(Timestamp, Option<Timestamp>, Vec<u8>)> {
    match Envelope::decode(encoded_envelope) {
        Ok(env) => {
            let received_at = current_timestamp();
            Some((received_at, env.enclosed_at, env.payload))
        }
        Err(_) => None,
    }
}

/// Get the current timestamp as a protobuf Timestamp.
fn current_timestamp() -> Timestamp {
    let now = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .expect("Time went backwards");
    Timestamp {
        seconds: now.as_secs() as i64,
        nanos: now.subsec_nanos() as i32,
    }
}

// --- Subject helpers ---
static SUBJECTS: OnceLock<HashMap<String, String>> = OnceLock::new();

/// Check if a subject is well-known.
pub fn is_subject_well_known(subject: &str) -> bool {
    SUBJECTS.get_or_init(load_subjects).contains_key(subject)
}

/// Get the schema (protobuf type name) for a subject.
pub fn get_subject_schema(subject: &str) -> Option<&str> {
    SUBJECTS.get_or_init(load_subjects).get(subject).map(|s| s.as_str())
}

fn load_subjects() -> HashMap<String, String> {
    let yaml_path = Path::new(env!("CARGO_MANIFEST_DIR")).join("src/subjects.yaml");
    
    match fs::read_to_string(&yaml_path) {
        Ok(contents) => {
            match serde_yml::from_str::<HashMap<String, String>>(&contents) {
                Ok(subjects) => subjects,
                Err(e) => {
                    eprintln!("Warning: Failed to parse subjects.yaml: {}. Falling back to manual parsing.", e);
                    // Fallback to manual parsing if YAML parsing fails
                    parse_subjects_manually(&contents)
                }
            }
        }
        Err(e) => {
            eprintln!("Warning: Failed to read subjects.yaml from {:?}: {}", yaml_path, e);
            HashMap::new()
        }
    }
}

fn parse_subjects_manually(contents: &str) -> HashMap<String, String> {
    let mut map = HashMap::new();
    for line in contents.lines() {
        let line = line.trim();
        if line.is_empty() || line.starts_with('#') {
            continue;
        }
        if let Some((k, v)) = line.split_once(':') {
            let key = k.trim().to_string();
            let val = v.trim()
                .split_whitespace()
                .next()
                .unwrap_or("")
                .to_string();
            map.insert(key, val);
        }
    }
    map
}

// --- Payload helpers ---

/// Decode a payload from its type name.
pub fn decode_payload_from_type_name<T: Message + Default>(payload: &[u8]) -> Result<T, prost::DecodeError> {
    T::decode(payload)
}

/// Encode a payload to bytes.
pub fn encode_payload_from_type_name<T: Message>(payload: &T) -> Vec<u8> {
    payload.encode_to_vec()
}

/// Enclose a payload of a specific type with an envelope.
pub fn enclose_from_type_name<T: Message>(
    payload_value: &T,
    enclosed_at: Option<Timestamp>,
) -> Vec<u8> {
    let payload = encode_payload_from_type_name(payload_value);
    enclose(payload, enclosed_at)
}
