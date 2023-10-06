use std::io::Result;

// use glob::glob;

fn main() -> Result<()> {
    prost_build::compile_protos(&["../envelope.proto"], &["../"])?;

    // let protos: Vec<_> = glob("../payloads/**/*.proto")
    //     .expect("Could not understand glob pattern!")
    //     .collect();

    // let protos = protos.unwrap();

    // for path in protos {
    //     println!("{}", path?.display());
    // }

    // let mut builder = prost_build::Config::new();
    // builder.file_descriptor_set_path(&["payloads_proto_fds.bin"]);
    // builder.compile_protos(protos, &["../"])?;

    Ok(())
}
