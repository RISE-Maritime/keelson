use std::env;
use std::fs;
use std::path::Path;

fn main() -> Result<(), Box<dyn std::error::Error>> {
    println!("cargo:rerun-if-changed=../../messages");
    println!("cargo:rerun-if-changed=../../interfaces");

    let out_dir = env::var("OUT_DIR")?;

    // Start with just core files to get a working build
    generate_core_only(&out_dir)?;
    
    // Generate module files
    generate_modules(&out_dir)?;

    Ok(())
}

fn generate_core_only(out_dir: &str) -> Result<(), Box<dyn std::error::Error>> {
    println!("cargo:warning=Generating maritime protobuf files");
    println!("cargo:warning=OUT_DIR = {}", out_dir);

    // Core maritime message types (independent ones first)
    let mut maritime_files = vec![
        "../../messages/Envelope.proto".to_string(),
        "../../messages/payloads/Primitives.proto".to_string(),
        "../../messages/payloads/Audio.proto".to_string(),
        "../../messages/payloads/FlagCode.proto".to_string(),
        "../../messages/payloads/LocationFixQuality.proto".to_string(),
        "../../messages/payloads/NetworkStatus.proto".to_string(),
        "../../messages/payloads/ROCStatus.proto".to_string(),
        "../../messages/payloads/SensorStatus.proto".to_string(),
        "../../messages/payloads/SimulationStatus.proto".to_string(),
        "../../messages/payloads/TargetType.proto".to_string(),
        "../../messages/payloads/VesselType.proto".to_string(),
        "../../messages/payloads/VesselNavStatus.proto".to_string(),
        "../../messages/payloads/Geojson.proto".to_string(),
    ];

    // Add essential foxglove dependencies
    maritime_files.extend(vec![
        "../../messages/payloads/foxglove/Vector3.proto".to_string(),
        "../../messages/payloads/foxglove/Quaternion.proto".to_string(),
        "../../messages/payloads/foxglove/Point3.proto".to_string(),
    ]);

    // Now add maritime types that depend on foxglove
    maritime_files.extend(vec![
        "../../messages/payloads/ImuReading.proto".to_string(),
        "../../messages/payloads/RadarReading.proto".to_string(),
    ]);

    println!("cargo:warning=Compiling {} maritime files", core_maritime_files.len());

    match prost_build::Config::new()
        .out_dir(out_dir)
        .compile_protos(&core_maritime_files, &["../../messages"]) {
        Ok(()) => {
            println!("cargo:warning=Maritime protobuf compilation successful");
            // List generated files
            for entry in std::fs::read_dir(out_dir)? {
                let entry = entry?;
                println!("cargo:warning=Generated: {}", entry.file_name().to_string_lossy());
            }
        },
        Err(e) => {
            println!("cargo:warning=Maritime protobuf compilation failed: {}", e);
            return Err(Box::new(e));
        }
    }

    Ok(())
}

fn generate_modules(_out_dir: &str) -> Result<(), Box<dyn std::error::Error>> {
    // Generate src/core.rs
    let core_content = r#"//! Core Keelson types
//! 
//! This module contains the fundamental types used by the Keelson protocol,
//! including Envelope and KeyEnvelopePair.

// Generated protobuf files - the actual filenames are determined by the package names
include!(concat!(env!("OUT_DIR"), "/core.rs"));
"#;
    fs::write("src/core.rs", core_content)?;

    // Generate src/payloads.rs with basic payload types
    let payloads_content = r#"//! Keelson payload types
//! 
//! This module contains the generated protobuf message types used for 
//! maritime data exchange in the Keelson protocol.

// Generated from Primitives.proto - contains TimestampedFloat, TimestampedBytes, etc.
include!(concat!(env!("OUT_DIR"), "/keelson.rs"));

/// Foxglove-specific message types (placeholder for now)
pub mod foxglove {
    // Will be populated when foxglove generation is working
}
"#;
    fs::write("src/payloads.rs", payloads_content)?;

    // Generate src/interfaces.rs - empty for now
    let interfaces_content = r#"//! Keelson interface definitions
//! 
//! This module contains the generated protobuf service interfaces used for
//! RPC communication in the Keelson protocol.

// Interfaces will be added once protobuf generation is working properly
"#;
    fs::write("src/interfaces.rs", interfaces_content)?;

    Ok(())
}

fn discover_proto_files(pattern: &str) -> Result<Vec<String>, Box<dyn std::error::Error>> {
    let mut files = Vec::new();
    for entry in glob::glob(pattern)? {
        files.push(entry?.to_str().unwrap().to_string());
    }
    Ok(files)
}
