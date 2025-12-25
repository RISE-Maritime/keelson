use std::env;
use std::path::PathBuf;

fn main() -> Result<(), Box<dyn std::error::Error>> {
    // Tell Cargo to rerun this build script if any proto files change
    println!("cargo:rerun-if-changed=../../messages");
    println!("cargo:rerun-if-changed=../../interfaces");

    let out_dir = PathBuf::from(env::var("OUT_DIR")?);

    // Compile Envelope proto separately (uses messages as include path)
    prost_build::Config::new()
        .compile_protos(&["../../messages/Envelope.proto"], &["../../messages"])?;

    // Compile payload protos with file descriptor set for runtime reflection
    prost_build::Config::new()
        .file_descriptor_set_path(out_dir.join("payloads_descriptor.bin"))
        .compile_protos(
            &[
                // Keelson payloads
                "../../messages/payloads/Alarm.proto",
                "../../messages/payloads/Audio.proto",
                "../../messages/payloads/Decomposed3DVector.proto",
                "../../messages/payloads/FlagCode.proto",
                "../../messages/payloads/Geojson.proto",
                "../../messages/payloads/LocationFixQuality.proto",
                "../../messages/payloads/NetworkStatus.proto",
                "../../messages/payloads/Primitives.proto",
                "../../messages/payloads/RadarReading.proto",
                "../../messages/payloads/ROCStatus.proto",
                "../../messages/payloads/SensorStatus.proto",
                "../../messages/payloads/SimulationStatus.proto",
                "../../messages/payloads/TargetType.proto",
                "../../messages/payloads/VesselNavStatus.proto",
                "../../messages/payloads/VesselType.proto",
                // Foxglove protos
                "../../messages/payloads/foxglove/ArrowPrimitive.proto",
                "../../messages/payloads/foxglove/CameraCalibration.proto",
                "../../messages/payloads/foxglove/CircleAnnotation.proto",
                "../../messages/payloads/foxglove/Color.proto",
                "../../messages/payloads/foxglove/CompressedImage.proto",
                "../../messages/payloads/foxglove/CompressedVideo.proto",
                "../../messages/payloads/foxglove/CubePrimitive.proto",
                "../../messages/payloads/foxglove/CylinderPrimitive.proto",
                "../../messages/payloads/foxglove/FrameTransform.proto",
                "../../messages/payloads/foxglove/FrameTransforms.proto",
                "../../messages/payloads/foxglove/GeoJSON.proto",
                "../../messages/payloads/foxglove/Grid.proto",
                "../../messages/payloads/foxglove/ImageAnnotations.proto",
                "../../messages/payloads/foxglove/KeyValuePair.proto",
                "../../messages/payloads/foxglove/LaserScan.proto",
                "../../messages/payloads/foxglove/LinePrimitive.proto",
                "../../messages/payloads/foxglove/LocationFix.proto",
                "../../messages/payloads/foxglove/Log.proto",
                "../../messages/payloads/foxglove/ModelPrimitive.proto",
                "../../messages/payloads/foxglove/PackedElementField.proto",
                "../../messages/payloads/foxglove/Point2.proto",
                "../../messages/payloads/foxglove/Point3.proto",
                "../../messages/payloads/foxglove/PointCloud.proto",
                "../../messages/payloads/foxglove/PointsAnnotation.proto",
                "../../messages/payloads/foxglove/Pose.proto",
                "../../messages/payloads/foxglove/PoseInFrame.proto",
                "../../messages/payloads/foxglove/PosesInFrame.proto",
                "../../messages/payloads/foxglove/Quaternion.proto",
                "../../messages/payloads/foxglove/RawImage.proto",
                "../../messages/payloads/foxglove/SceneEntity.proto",
                "../../messages/payloads/foxglove/SceneEntityDeletion.proto",
                "../../messages/payloads/foxglove/SceneUpdate.proto",
                "../../messages/payloads/foxglove/SpherePrimitive.proto",
                "../../messages/payloads/foxglove/TextAnnotation.proto",
                "../../messages/payloads/foxglove/TextPrimitive.proto",
                "../../messages/payloads/foxglove/TriangleListPrimitive.proto",
                "../../messages/payloads/foxglove/Vector2.proto",
                "../../messages/payloads/foxglove/Vector3.proto",
            ],
            &["../../messages/payloads"],
        )?;

    // Compile interface protos with file descriptor set
    prost_build::Config::new()
        .file_descriptor_set_path(out_dir.join("interfaces_descriptor.bin"))
        .compile_protos(
            &[
                "../../interfaces/Configurable.proto",
                "../../interfaces/ErrorResponse.proto",
                "../../interfaces/NetworkPingPong.proto",
                "../../interfaces/Subscriber.proto",
                "../../interfaces/WHEPProxy.proto",
            ],
            &["../../interfaces"],
        )?;

    Ok(())
}
