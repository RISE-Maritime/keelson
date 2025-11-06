use keelson::*;
use keelson::payloads::TimestampedFloat;
use prost::Message;
use prost_types::Timestamp;
use std::time::{SystemTime, UNIX_EPOCH};

#[test]
fn test_construct_pubsub_key() {
    let key = construct_pubsub_key("base_path", "entity_id", "subject", "source_id");
    assert_eq!(key, "base_path/@v0/entity_id/pubsub/subject/source_id");
}

#[test]
fn test_construct_rpc_key() {
    let key = construct_rpc_key("base_path", "entity_id", "procedure", "responder_id");
    assert_eq!(key, "base_path/@v0/entity_id/@rpc/procedure/responder_id");
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
    let key = "base_path/@v0/entity_id/@rpc/procedure/responder_id";
    let parsed = parse_rpc_key(key).unwrap();
    assert_eq!(parsed.0, "base_path");
    assert_eq!(parsed.1, "entity_id");
    assert_eq!(parsed.2, "procedure");
    assert_eq!(parsed.3, "responder_id");
}

#[test]
fn test_get_subject_from_pubsub_key() {
    let key = "base_path/@v0/entity_id/pubsub/subject/source_id";
    let subject = get_subject_from_pubsub_key(key).unwrap();
    assert_eq!(subject, "subject");
}

#[test]
fn test_enclose_uncover() {
    let test_data = b"test".to_vec();
    let envelope = enclose(test_data.clone(), None);
    let (received_at, enclosed_at, payload) = uncover(&envelope).unwrap();

    assert_eq!(test_data, payload);
    assert!(received_at.seconds >= enclosed_at.as_ref().unwrap().seconds);
}

#[test]
fn test_enclose_uncover_actual_payload() {
    let mut data = TimestampedFloat::default();
    let now = SystemTime::now().duration_since(UNIX_EPOCH).unwrap();
    data.timestamp = Some(Timestamp {
        seconds: now.as_secs() as i64,
        nanos: now.subsec_nanos() as i32,
    });
    data.value = 3.14;

    let envelope = enclose(data.encode_to_vec(), None);
    let (received_at, enclosed_at, payload) = uncover(&envelope).unwrap();
    let content = TimestampedFloat::decode(&payload[..]).unwrap();

    assert_eq!(data.value, content.value);
    assert_eq!(data.timestamp, content.timestamp);
    assert!(received_at.seconds >= enclosed_at.unwrap().seconds);
}

#[test]
fn test_is_subject_well_known() {
    assert!(is_subject_well_known("lever_position_pct"));
    assert!(!is_subject_well_known("random_mumbo_jumbo"));
}

#[test]
fn test_get_subject_schema() {
    assert_eq!(get_subject_schema("lever_position_pct"), Some("keelson.TimestampedFloat"));
    assert_eq!(get_subject_schema("raw"), Some("keelson.TimestampedBytes"));
    assert_eq!(get_subject_schema("not_a_subject"), None);
}

#[test]
fn test_decode_payload_from_type_name() {
    let mut data = TimestampedFloat::default();
    let now = SystemTime::now().duration_since(UNIX_EPOCH).unwrap();
    data.timestamp = Some(Timestamp {
        seconds: now.as_secs() as i64,
        nanos: now.subsec_nanos() as i32,
    });
    data.value = 3.14;

    let payload = data.encode_to_vec();
    let decoded: TimestampedFloat = decode_payload_from_type_name(&payload).unwrap();

    assert_eq!(data.value, decoded.value);
    assert_eq!(data.timestamp, decoded.timestamp);
}

#[test]
fn test_encode_payload_from_type_name() {
    let mut data = TimestampedFloat::default();
    let now = SystemTime::now().duration_since(UNIX_EPOCH).unwrap();
    data.timestamp = Some(Timestamp {
        seconds: now.as_secs() as i64,
        nanos: now.subsec_nanos() as i32,
    });
    data.value = 3.14;

    let encoded = encode_payload_from_type_name(&data);
    let decoded = TimestampedFloat::decode(&encoded[..]).unwrap();

    assert_eq!(data.value, decoded.value);
    assert_eq!(data.timestamp, decoded.timestamp);
}

#[test]
fn test_enclose_from_type_name() {
    let mut data = TimestampedFloat::default();
    let now = SystemTime::now().duration_since(UNIX_EPOCH).unwrap();
    data.timestamp = Some(Timestamp {
        seconds: now.as_secs() as i64,
        nanos: now.subsec_nanos() as i32,
    });
    data.value = 3.14;

    let envelope = enclose_from_type_name(&data, None);
    let (received_at, enclosed_at, payload) = uncover(&envelope).unwrap();
    let content = TimestampedFloat::decode(&payload[..]).unwrap();

    assert_eq!(data.value, content.value);
    assert_eq!(data.timestamp, content.timestamp);
    assert!(received_at.seconds >= enclosed_at.unwrap().seconds);
}

#[test]
fn test_ensure_all_well_known_subjects() {
    // Test that all subjects are lowercase and have valid schemas
    let test_subjects = ["lever_position_pct", "raw", "heading_true_north_deg"];
    
    for subject in &test_subjects {
        assert_eq!(*subject, subject.to_lowercase());
        assert!(is_subject_well_known(subject), "Subject '{}' should be well-known", subject);
        assert!(get_subject_schema(subject).is_some(), "Subject '{}' should have a schema", subject);
    }
}

// Test codec functionality
#[test]
fn test_codec_text_roundtrip() {
    use keelson::codec::{enclose_from_text, uncover_to_text};
    
    let key = "test/@v0/entity/pubsub/raw/source";
    let original_text = "Hello, Keelson!";
    
    let encoded = enclose_from_text(key, original_text).unwrap();
    let decoded = uncover_to_text(key, &encoded).unwrap();
    
    assert_eq!(original_text, decoded);
}

#[test]
fn test_codec_base64_roundtrip() {
    use keelson::codec::{enclose_from_base64, uncover_to_base64};
    
    let key = "test/@v0/entity/pubsub/raw/source";
    let original_data = "Hello, World!";
    use base64::{Engine as _, engine::general_purpose};
    let base64_data = general_purpose::STANDARD.encode(original_data);
    
    let encoded = enclose_from_base64(key, &base64_data).unwrap();
    let decoded = uncover_to_base64(key, &encoded).unwrap();
    
    assert_eq!(base64_data, decoded);
}
