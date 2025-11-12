//! Subject management and well-known subjects
//!
//! This module provides utilities for working with well-known subjects
//! and their associated protobuf type names.

use crate::error::Result;
use lazy_static::lazy_static;
use std::collections::HashMap;
use std::sync::RwLock;

lazy_static! {
    /// Global registry of well-known subjects and their protobuf type names
    static ref SUBJECTS: RwLock<HashMap<String, String>> = {
        let subjects = load_bundled_subjects().unwrap_or_default();
        RwLock::new(subjects)
    };
}

/// Load the bundled subjects.yaml file
fn load_bundled_subjects() -> Result<HashMap<String, String>> {
    let yaml_content = include_str!("../../../messages/subjects.yaml");
    load_subjects_from_str(yaml_content)
}

/// Load subjects from a YAML string
pub fn load_subjects_from_str(yaml_content: &str) -> Result<HashMap<String, String>> {
    let subjects: HashMap<String, String> = serde_yaml::from_str(yaml_content)?;
    Ok(subjects)
}

/// Load subjects from a custom file path and add to the global registry
///
/// # Arguments
///
/// * `path` - Path to a subjects.yaml file
///
/// # Errors
///
/// Returns an error if the file cannot be read or parsed.
pub fn load_subjects(path: &str) -> Result<()> {
    let content = std::fs::read_to_string(path)?;
    let new_subjects = load_subjects_from_str(&content)?;

    let mut subjects = SUBJECTS.write().unwrap();
    subjects.extend(new_subjects);

    Ok(())
}

/// Check if a subject is well-known
///
/// # Arguments
///
/// * `subject` - The subject name to check
///
/// # Returns
///
/// `true` if the subject is well-known, `false` otherwise.
///
/// # Example
///
/// ```rust
/// use keelson::is_subject_well_known;
///
/// assert!(is_subject_well_known("raw"));
/// assert!(is_subject_well_known("location_fix"));
/// assert!(!is_subject_well_known("random_mumbo_jumbo"));
/// ```
pub fn is_subject_well_known(subject: &str) -> bool {
    SUBJECTS.read().unwrap().contains_key(subject)
}

/// Get the protobuf type name (schema) for a well-known subject
///
/// # Arguments
///
/// * `subject` - The subject name
///
/// # Returns
///
/// The protobuf type name as a String, or None if the subject is not well-known.
///
/// # Example
///
/// ```rust
/// use keelson::get_subject_schema;
///
/// let schema = get_subject_schema("raw").unwrap();
/// assert_eq!(schema, "keelson.TimestampedBytes");
/// ```
pub fn get_subject_schema(subject: &str) -> Option<String> {
    SUBJECTS.read().unwrap().get(subject).cloned()
}

/// Get all well-known subjects
///
/// # Returns
///
/// A HashMap of all subject names and their protobuf type names.
pub fn get_all_subjects() -> HashMap<String, String> {
    SUBJECTS.read().unwrap().clone()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_bundled_subjects_loaded() {
        // Test that bundled subjects are loaded
        assert!(is_subject_well_known("raw"));
        assert!(is_subject_well_known("location_fix"));
        assert!(is_subject_well_known("heading_true_north_deg"));
    }

    #[test]
    fn test_is_subject_well_known() {
        assert!(is_subject_well_known("lever_position_pct"));
        assert!(!is_subject_well_known("random_mumbo_jumbo"));
    }

    #[test]
    fn test_get_subject_schema() {
        let schema = get_subject_schema("lever_position_pct");
        assert_eq!(schema, Some("keelson.TimestampedFloat".to_string()));

        let schema = get_subject_schema("raw");
        assert_eq!(schema, Some("keelson.TimestampedBytes".to_string()));

        let schema = get_subject_schema("nonexistent");
        assert_eq!(schema, None);
    }

    #[test]
    fn test_load_subjects_from_str() {
        let yaml = r#"
test_subject: test.Type
another_subject: another.Type
"#;
        let subjects = load_subjects_from_str(yaml).unwrap();
        assert_eq!(subjects.get("test_subject").unwrap(), "test.Type");
        assert_eq!(subjects.get("another_subject").unwrap(), "another.Type");
    }

    #[test]
    fn test_get_all_subjects() {
        let subjects = get_all_subjects();
        assert!(!subjects.is_empty());
        assert!(subjects.contains_key("raw"));
    }
}
