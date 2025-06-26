use std::fs;
use std::path::Path;

fn main() {
    // Envelope.proto -> src
    let envelope_out = Path::new("src");
    fs::create_dir_all(envelope_out).unwrap();
    prost_build::Config::new()
        .out_dir(envelope_out)
        .compile_protos(&["../../messages/Envelope.proto"], &["../../messages"])
        .unwrap();

    // Payloads -> src/payloads
    let payloads_out = Path::new("src/payloads");
    fs::create_dir_all(payloads_out).unwrap();
    prost_build::Config::new()
        .out_dir(payloads_out)
        .compile_protos(
            &glob::glob("../../messages/payloads/**/*.proto")
                .unwrap()
                .filter_map(Result::ok)
                .map(|p| p.to_str().unwrap().to_owned())
                .collect::<Vec<_>>(),
            &["../../messages/payloads"],
        )
        .unwrap();

    // Interfaces -> src/interfaces
    let interfaces_out = Path::new("src/interfaces");
    fs::create_dir_all(interfaces_out).unwrap();
    prost_build::Config::new()
        .out_dir(interfaces_out)
        .compile_protos(
            &glob::glob("../../interfaces/*.proto")
                .unwrap()
                .filter_map(Result::ok)
                .map(|p| p.to_str().unwrap().to_owned())
                .collect::<Vec<_>>(),
            &["../../interfaces"],
        )
        .unwrap();
}
