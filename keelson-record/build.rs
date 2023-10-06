use std::io::Result;
fn main() -> Result<()> {
    prost_build::compile_protos(&["../brefv/envelope.proto"], &["../brefv/"])?;

    // let mut builder = prost_build::Config::new();
    // builder.file_descriptor_set_path(&["srv/payloads_proto_fds.bin"]);
    // builder.compile_protos(protos, includes)

    // prost_build::compile_protos(protos, includes)/"])?;
    Ok(())
}
