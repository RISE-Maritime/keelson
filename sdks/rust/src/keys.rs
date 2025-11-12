//! Key expression construction and parsing
//!
//! This module provides utilities for constructing and parsing Keelson key expressions
//! used in Zenoh pub/sub and RPC interactions.

use crate::error::{Error, Result};
use crate::subjects::is_subject_well_known;
use std::collections::HashMap;

/// Format string for pub/sub keys
const KEELSON_PUB_SUB_KEY_FORMAT: &str =
    "{base_path}/@v0/{entity_id}/pubsub/{subject}/{source_id}";

/// Format string for RPC (request/reply) keys
const KEELSON_REQ_REP_KEY_FORMAT: &str =
    "{base_path}/@v0/{entity_id}/@rpc/{procedure}/{responder_id}";

/// Construct a key expression for a publish-subscribe interaction (Observable).
///
/// # Arguments
///
/// * `base_path` - The base path/realm for the entity
/// * `entity_id` - The entity identifier
/// * `subject` - The subject of the interaction
/// * `source_id` - The source identifier of the entity
/// * `target_id` - Optional target entity identifier
///
/// # Returns
///
/// The constructed key expression as a String.
///
/// # Example
///
/// ```rust
/// use keelson::construct_pubsub_key;
///
/// let key = construct_pubsub_key("base_path", "entity_id", "subject", "source_id", None);
/// assert_eq!(key, "base_path/@v0/entity_id/pubsub/subject/source_id");
/// ```
pub fn construct_pubsub_key(
    base_path: &str,
    entity_id: &str,
    subject: &str,
    source_id: &str,
    target_id: Option<&str>,
) -> String {
    if !is_subject_well_known(subject) {
        log::warn!("Subject: {} is NOT well-known!", subject);
    }

    let key = KEELSON_PUB_SUB_KEY_FORMAT
        .replace("{base_path}", base_path)
        .replace("{entity_id}", entity_id)
        .replace("{subject}", subject)
        .replace("{source_id}", source_id);

    match target_id {
        Some(tid) => format!("{}/@target/{}", key, tid),
        None => key,
    }
}

/// Construct a key expression for a request-reply interaction (Queryable/RPC).
///
/// # Arguments
///
/// * `base_path` - The base path/realm for the entity
/// * `entity_id` - The entity identifier
/// * `procedure` - The procedure being called to identify the specific service
/// * `responder_id` - The responder identifier of the entity being targeted
///
/// # Returns
///
/// The constructed key expression as a String.
///
/// # Example
///
/// ```rust
/// use keelson::construct_rpc_key;
///
/// let key = construct_rpc_key("base_path", "entity_id", "procedure", "responder_id");
/// assert_eq!(key, "base_path/@v0/entity_id/@rpc/procedure/responder_id");
/// ```
pub fn construct_rpc_key(
    base_path: &str,
    entity_id: &str,
    procedure: &str,
    responder_id: &str,
) -> String {
    KEELSON_REQ_REP_KEY_FORMAT
        .replace("{base_path}", base_path)
        .replace("{entity_id}", entity_id)
        .replace("{procedure}", procedure)
        .replace("{responder_id}", responder_id)
}

/// Parse a pub/sub key expression into its components.
///
/// # Arguments
///
/// * `key` - The key expression to parse
///
/// # Returns
///
/// A HashMap containing the parsed components: base_path, entity_id, subject, source_id
///
/// # Errors
///
/// Returns `Error::KeyParseError` if the key doesn't match the expected format.
///
/// # Example
///
/// ```rust
/// use keelson::parse_pubsub_key;
///
/// let parsed = parse_pubsub_key("base_path/@v0/entity_id/pubsub/subject/source_id").unwrap();
/// assert_eq!(parsed.get("base_path").unwrap(), "base_path");
/// assert_eq!(parsed.get("subject").unwrap(), "subject");
/// ```
pub fn parse_pubsub_key(key: &str) -> Result<HashMap<String, String>> {
    let parts: Vec<&str> = key.split('/').collect();

    if parts.len() < 6 {
        return Err(Error::KeyParseError(format!(
            "Key '{}' does not have enough parts (expected at least 6)",
            key
        )));
    }

    if parts[1] != "@v0" || parts[3] != "pubsub" {
        return Err(Error::KeyParseError(format!(
            "Key '{}' does not match expected format '{}'",
            key, KEELSON_PUB_SUB_KEY_FORMAT
        )));
    }

    let mut result = HashMap::new();
    result.insert("base_path".to_string(), parts[0].to_string());
    result.insert("entity_id".to_string(), parts[2].to_string());
    result.insert("subject".to_string(), parts[4].to_string());

    // Handle multi-part source_id (everything after position 5)
    result.insert("source_id".to_string(), parts[5..].join("/"));

    Ok(result)
}

/// Parse an RPC key expression into its components.
///
/// # Arguments
///
/// * `key` - The key expression to parse
///
/// # Returns
///
/// A HashMap containing the parsed components: base_path, entity_id, procedure, responder_id
///
/// # Errors
///
/// Returns `Error::KeyParseError` if the key doesn't match the expected format.
///
/// # Example
///
/// ```rust
/// use keelson::parse_rpc_key;
///
/// let parsed = parse_rpc_key("base_path/@v0/entity_id/@rpc/procedure/responder_id").unwrap();
/// assert_eq!(parsed.get("procedure").unwrap(), "procedure");
/// ```
pub fn parse_rpc_key(key: &str) -> Result<HashMap<String, String>> {
    let parts: Vec<&str> = key.split('/').collect();

    if parts.len() < 6 {
        return Err(Error::KeyParseError(format!(
            "Key '{}' does not have enough parts (expected at least 6)",
            key
        )));
    }

    if parts[1] != "@v0" || parts[3] != "@rpc" {
        return Err(Error::KeyParseError(format!(
            "Key '{}' does not match expected format '{}'",
            key, KEELSON_REQ_REP_KEY_FORMAT
        )));
    }

    let mut result = HashMap::new();
    result.insert("base_path".to_string(), parts[0].to_string());
    result.insert("entity_id".to_string(), parts[2].to_string());
    result.insert("procedure".to_string(), parts[4].to_string());
    result.insert("responder_id".to_string(), parts[5..].join("/"));

    Ok(result)
}

/// Get the subject from a pub/sub key expression.
///
/// # Arguments
///
/// * `key` - The pub/sub key expression
///
/// # Returns
///
/// The subject as a String.
///
/// # Errors
///
/// Returns `Error::KeyParseError` if the key cannot be parsed.
///
/// # Example
///
/// ```rust
/// use keelson::get_subject_from_pubsub_key;
///
/// let subject = get_subject_from_pubsub_key("base_path/@v0/entity_id/pubsub/subject/source_id").unwrap();
/// assert_eq!(subject, "subject");
/// ```
pub fn get_subject_from_pubsub_key(key: &str) -> Result<String> {
    let parsed = parse_pubsub_key(key)?;
    Ok(parsed
        .get("subject")
        .ok_or_else(|| Error::KeyParseError("Missing subject in parsed key".to_string()))?
        .clone())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_construct_pubsub_key() {
        let key = construct_pubsub_key("base_path", "entity_id", "subject", "source_id", None);
        assert_eq!(key, "base_path/@v0/entity_id/pubsub/subject/source_id");
    }

    #[test]
    fn test_construct_pubsub_key_with_target() {
        let key = construct_pubsub_key(
            "base_path",
            "entity_id",
            "subject",
            "source_id",
            Some("target"),
        );
        assert_eq!(
            key,
            "base_path/@v0/entity_id/pubsub/subject/source_id/@target/target"
        );
    }

    #[test]
    fn test_construct_rpc_key() {
        let key = construct_rpc_key("base_path", "entity_id", "procedure", "responder_id");
        assert_eq!(key, "base_path/@v0/entity_id/@rpc/procedure/responder_id");
    }

    #[test]
    fn test_parse_pubsub_key() {
        let parsed = parse_pubsub_key("base_path/@v0/entity_id/pubsub/subject/source_id").unwrap();
        assert_eq!(parsed.get("base_path").unwrap(), "base_path");
        assert_eq!(parsed.get("entity_id").unwrap(), "entity_id");
        assert_eq!(parsed.get("subject").unwrap(), "subject");
        assert_eq!(parsed.get("source_id").unwrap(), "source_id");
    }

    #[test]
    fn test_parse_pubsub_key_with_multi_part_source() {
        let parsed =
            parse_pubsub_key("base_path/@v0/entity_id/pubsub/subject/source_id/sub_id").unwrap();
        assert_eq!(parsed.get("source_id").unwrap(), "source_id/sub_id");
    }

    #[test]
    fn test_parse_rpc_key() {
        let parsed = parse_rpc_key("base_path/@v0/entity_id/@rpc/procedure/responder_id").unwrap();
        assert_eq!(parsed.get("base_path").unwrap(), "base_path");
        assert_eq!(parsed.get("entity_id").unwrap(), "entity_id");
        assert_eq!(parsed.get("procedure").unwrap(), "procedure");
        assert_eq!(parsed.get("responder_id").unwrap(), "responder_id");
    }

    #[test]
    fn test_get_subject_from_pubsub_key() {
        let subject =
            get_subject_from_pubsub_key("base_path/@v0/entity_id/pubsub/subject/source_id")
                .unwrap();
        assert_eq!(subject, "subject");
    }

    #[test]
    fn test_parse_invalid_pubsub_key() {
        let result = parse_pubsub_key("invalid/key");
        assert!(result.is_err());
    }

    #[test]
    fn test_parse_invalid_rpc_key() {
        let result = parse_rpc_key("invalid/key");
        assert!(result.is_err());
    }
}
