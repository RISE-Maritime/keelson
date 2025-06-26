use keelson::*;
use prost_types::Timestamp;

#[test]
fn test_construct_pubsub_key() {
    let key = construct_pubsub_key("base_path", "entity_id", "subject", "source_id");
    assert_eq!(key, "base_path/@v0/entity_id/pubsub/subject/source_id");
}

#[test]
fn test_construct_rpc_key() {
    let key = construct_rpc_key("base_path", "entity_id", "procedure", "source_id");
    assert_eq!(key, "base_path/@v0/entity_id/@rpc/procedure/source_id");
}

#[test]
fn test_parse_pubsub_key() {
    let key = "base_path/@v0/entity_id/pubsub/subject/source_id/sub_id";
    let parsed = parse_pubsub_key(key).unwrap();
    assert_eq!(parsed.0, "base_path");
    assert_eq!(parsed.1, "entity_id");
    assert_eq!(parsed.2, "subject");
    assert_eq!(parsed.3, "source_id/sub_id");
}

#[test]
fn test_parse_rpc_key() {
    let key = "base_path/@v0/entity_id/@rpc/procedure/source_id";
    let parsed = parse_rpc_key(key).unwrap();
    assert_eq!(parsed.0, "base_path");
    assert_eq!(parsed.1, "entity_id");
    assert_eq!(parsed.2, "procedure");
    assert_eq!(parsed.3, "source_id");
}

#[test]
fn test_is_subject_well_known() {
    assert!(is_subject_well_known("raw"));
    assert!(!is_subject_well_known("not_a_subject"));
}

#[test]
fn test_get_subject_schema() {
    assert_eq!(get_subject_schema("raw"), Some("keelson.TimestampedBytes"));
    assert_eq!(get_subject_schema("not_a_subject"), None);
}

#[test]
fn test_enclose_and_uncover() {
    let payload = b"hello world".to_vec();
    let ts = Timestamp {
        seconds: 123,
        nanos: 456,
    };
    let envelope = enclose(payload.clone(), Some(ts.clone()));
    let (received_at, enclosed_at, out_payload) = uncover(&envelope).unwrap();
    assert!(received_at.seconds > 0);
    assert_eq!(enclosed_at, Some(ts));
    assert_eq!(out_payload, payload);
}
