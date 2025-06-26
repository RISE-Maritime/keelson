//! Keelson SDK (Rust) - Minimal core API

use std::collections::HashMap;
use std::fs;
use std::path::Path;
use std::sync::OnceLock;

// --- Key construction and parsing ---
const KEELSON_PUB_SUB_KEY_FORMAT: &str = "{base_path}/@v0/{entity_id}/pubsub/{subject}/{source_id}";
const KEELSON_REQ_REP_KEY_FORMAT: &str = "{base_path}/@v0/{entity_id}/@rpc/{procedure}/{source_id}";

pub fn construct_pubsub_key(
    base_path: &str,
    entity_id: &str,
    subject: &str,
    source_id: &str,
) -> String {
    KEELSON_PUB_SUB_KEY_FORMAT
        .replace("{base_path}", base_path)
        .replace("{entity_id}", entity_id)
        .replace("{subject}", subject)
        .replace("{source_id}", source_id)
}

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

pub fn parse_pubsub_key(key: &str) -> Option<(String, String, String, String)> {
    let parts: Vec<&str> = key.split('/').collect();
    if parts.len() < 6 {
        return None;
    }
    Some((
        parts[0].to_string(),
        parts[2].to_string(),
        parts[4].to_string(),
        parts[5..].join("/"),
    ))
}

pub fn parse_rpc_key(key: &str) -> Option<(String, String, String, String)> {
    let parts: Vec<&str> = key.split('/').collect();
    if parts.len() < 6 {
        return None;
    }
    Some((
        parts[0].to_string(),
        parts[2].to_string(),
        parts[4].to_string(),
        parts[5..].join("/"),
    ))
}

pub fn get_subject_from_pubsub_key(key: &str) -> Option<String> {
    parse_pubsub_key(key).map(|(_, _, subject, _)| subject)
}

// --- Envelope helpers ---
use crate::core::Envelope;
use prost_types::Timestamp;

pub fn enclose(payload: Vec<u8>, enclosed_at: Option<Timestamp>) -> Vec<u8> {
    Envelope {
        enclosed_at,
        payload,
    }
    .encode_to_vec()
}

pub fn uncover(encoded_envelope: &[u8]) -> Option<(Timestamp, Option<Timestamp>, Vec<u8>)> {
    if let Ok(env) = Envelope::decode(encoded_envelope) {
        let received_at = prost_types::Timestamp::from(std::time::SystemTime::now());
        Some((received_at, env.enclosed_at, env.payload))
    } else {
        None
    }
}

// --- Subject helpers ---
static SUBJECTS: OnceLock<HashMap<&'static str, &'static str>> = OnceLock::new();

pub fn is_subject_well_known(subject: &str) -> bool {
    SUBJECTS.get_or_init(load_subjects).contains_key(subject)
}

pub fn get_subject_schema(subject: &str) -> Option<&'static str> {
    SUBJECTS.get_or_init(load_subjects).get(subject).copied()
}

fn load_subjects() -> HashMap<&'static str, &'static str> {
    let mut map = HashMap::new();
    let yaml_path = Path::new(env!("CARGO_MANIFEST_DIR")).join("src/subjects.yaml");
    if let Ok(contents) = fs::read_to_string(yaml_path) {
        for line in contents.lines() {
            let line = line.trim();
            if line.is_empty() || line.starts_with('#') {
                continue;
            }
            if let Some((k, v)) = line.split_once(':') {
                let key: &'static str = Box::leak(k.trim().to_owned().into_boxed_str());
                let val: &'static str = Box::leak(
                    v.trim()
                        .split_whitespace()
                        .next()
                        .unwrap_or("")
                        .to_owned()
                        .into_boxed_str(),
                );
                map.insert(key, val);
            }
        }
    }
    map
}

// --- Payload helpers (stub) ---
use prost::Message;

pub fn get_protobuf_class_from_type_name(type_name: &str) -> Option<&'static str> {
    // This is a stub. In a full implementation, you would match type_name to a Rust type.
    // For now, just return the type name if it matches a known subject schema.
    get_subject_schema(type_name)
}

pub fn decode_payload_from_type_name<T: Message + Default>(payload: &[u8]) -> Option<T> {
    T::decode(payload).ok()
}

pub fn encode_payload_from_type_name<T: Message>(payload: &T) -> Option<Vec<u8>> {
    let mut buf = Vec::new();
    if payload.encode(&mut buf).is_ok() {
        Some(buf)
    } else {
        None
    }
}

pub fn enclose_from_type_name<T: Message>(
    payload_value: &T,
    enclosed_at: Option<Timestamp>,
) -> Option<Vec<u8>> {
    encode_payload_from_type_name(payload_value).map(|payload| enclose(payload, enclosed_at))
}

// --- Generated Protobuf modules ---

pub mod core;
pub mod payloads {
    pub mod foxglove;
    pub mod keelson;
}
pub mod interfaces {
    include!(concat!(env!("CARGO_MANIFEST_DIR"), "/src/interfaces/_.rs"));
}
