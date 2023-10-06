use clap::Parser;
use std::io::Cursor;
// use std::io::{stdin, Read};
use std::path::PathBuf;
use std::time::Duration;
// use std::process::exit;
use std::{fs, io::BufWriter};

use zenoh::config::Config;
use zenoh::prelude::sync::*;

// use anyhow::Result;
// use log::{debug, error, info, log_enabled, Level};

use mcap::Writer;

use prost::Message;

pub mod brefv {
    include!(concat!(env!("OUT_DIR"), "/brefv.rs"));
}

// use std::time::{Duration, SystemTime, UNIX_EPOCH};

// fn get_current_time_as_nanos() -> u128 {
//     let now = SystemTime::now();
//     let duration_since_unix_epoch = now.duration_since(UNIX_EPOCH).unwrap();
//     return duration_since_unix_epoch.as_nanos();
// }

// fn google_protobuf_timestamp_to_nanos() -> u128 {
//     let duration_since_unix_epoch = Duration::new(5, 42782);
//     return duration_since_unix_epoch.as_nanos();
// }

// fn write_it(path: &str) -> Result<()> {
//     // To set the profile or compression options, see mcap::WriteOptions.
//     let mut out = Writer::new(BufWriter::new(fs::File::create(path)?))?;

//     // Channels and schemas are automatically assigned ID as they're serialized,
//     // and automatically deduplicated with `Arc` when deserialized.
//     let my_channel = Channel {
//         topic: String::from("cool stuff"),
//         schema: None,
//         message_encoding: String::from("application/octet-stream"),
//         metadata: BTreeMap::default(),
//     };

//     let channel_id = out.add_channel(&my_channel)?;

//     out.write_to_known_channel(
//         &MessageHeader {
//             channel_id,
//             sequence: 25,
//             log_time: 6,
//             publish_time: 24,
//         },
//         &[1, 2, 3],
//     )?;
//     out.write_to_known_channel(
//         &MessageHeader {
//             channel_id,
//             sequence: 32,
//             log_time: 23,
//             publish_time: 25,
//         },
//         &[3, 4, 5],
//     )?;

//     out.finish()?;

//     Ok(())
// }

#[derive(Parser)]
#[command(author, version, about, long_about = None)]
struct Args {
    /// Path to the mcap output file
    output: PathBuf,

    // Path to the optional tags.yaml file
    #[arg(short, long, default_value = "/etc/brefv/tags.yaml")]
    tags: PathBuf,

    // Path to the optional protobuf binary file descriptor set
    #[arg(
        short,
        long,
        default_value = "/etc/brefv/payloads_proto_file_descripto_set.bin"
    )]
    proto_file_descriptors: PathBuf,

    // All key expressions that will be subscribed to
    #[clap(short, long, value_parser, required = true)]
    key: Option<Vec<String>>,
}

fn main() {
    // initiate logging
    env_logger::init();

    // Parse cli arguments
    let args = Args::parse();

    // Read tags.yaml and proto file descriptor

    // Create DescriptorPool

    // Open writer to output file
    let mut _out = Writer::new(BufWriter::new(
        fs::File::create(args.output).expect("Can not create file at desired location!"),
    ))
    .expect("Can not open MCAP writer.");

    // Define writer callback
    // Callback should:
    // - Find tag in key and lookup in tags.yaml
    //   - Find message descriptor if existing
    // - Open brefv Envelope
    // - Write to out
    let write_callback = move |s: Sample| {
        log::debug!("Got sample on key: {}", s.key_expr);

        // Open envelope
        let _env = brefv::Envelope::decode(&mut Cursor::new(s.value.payload.contiguous()));
    };

    // Open up the Zenoh session
    log::info!("Opening Zenoh session...");
    let session = zenoh::open(Config::default())
        .res()
        .expect("Could not open a Zenoh session!")
        .into_arc();

    // Subscribe to all keys given as input
    let mut subscribers = Vec::new();
    for key in args.key.unwrap() {
        log::info!("Subscribing to: {}", key);
        subscribers.push(
            session
                .declare_subscriber(key)
                .callback(write_callback)
                .res()
                .unwrap(),
        );
    }

    // ctrlc::set_handler(|| {
    //     for subscriber in subscribers {
    //         subscriber.undeclare();
    //     }
    //     out.finish();
    //     process::exit(0);
    // });

    loop {
        std::thread::sleep(Duration::from_secs(1));
    }
}
