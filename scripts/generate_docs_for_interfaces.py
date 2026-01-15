import os
import glob
import logging
import warnings
import argparse
from pathlib import Path

from mdutils import MdUtils
from protoc import PROTOC_INCLUDE_DIR


def main(args: argparse.Namespace):

    # Initialize the markdown file
    md_file = MdUtils(file_name=str(args.output_path / 'interfaces.md'),
                      title='Generic interfaces')

    md_file.new_paragraph(
        "These are well-known, well-specified interface definitions in use in `keelson`. "
        "Some noteworthy details: \n\n"
        "* The `JSON` message type referenced below refers to the use of actual JSON-encoded structures, instead of protobuf-encoded binary payloads. \n"
        "* Unfortunately, there exists NO code generation tool for creating stubs from these definitions as of now. \n"
        "* Implementing an interface in zenoh makes use of [queryables](https://zenoh.io/docs/manual/abstractions/)."
    )

    md_file.new_paragraph(
        "In all cases, errors in a queryable MUST be handled according to: \n\n"
        "* Using the `reply_err` functionality of a queryable. \n"
        "* Replying with an `ErrorResponse`"
    )

    md_file.new_line(
        '<a href="../interfaces/ErrorResponse.dot.svg" class="glightbox"><img src="../interfaces/ErrorResponse.dot.svg" alt="ErrorResponse" /></a>'
    )

    os.system(
        f"protodot -src {args.proto_root_path / 'ErrorResponse.proto'} -generated {args.output_path / 'interfaces'} -output ErrorResponse -inc {PROTOC_INCLUDE_DIR}")

    # Recursively iterate over all proto files in the given base folder
    for proto_path in glob.glob("**/*.proto", root_dir=args.proto_root_path, recursive=True):
        proto_path: Path = args.proto_root_path / proto_path

        with open(proto_path) as fh:

            # Find all services defined in the file
            for line in fh.readlines():

                # and then for each service definitions
                if line.startswith("service "):
                    service_name = line.split(" ")[1].strip("{")
                    print(f"Found service: {service_name}")

                    md_file.new_header(
                        2, service_name, add_table_of_contents="n")
                    md_file.new_line(
                        f'<a href="../interfaces/{proto_path.name}.dot.svg" class="glightbox"><img src="../interfaces/{proto_path.name}.dot.svg" alt="{proto_path.name}" /></a>'
                    )

                    os.system(
                        f"protodot -src {proto_path} -generated {args.output_path / 'interfaces'} -output {proto_path.name} -inc {PROTOC_INCLUDE_DIR}")

    md_file.create_md_file()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        prog="DocGenerator-Interfaces",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--log-level", type=int, default=logging.DEBUG)
    parser.add_argument(
        "proto_root_path", type=Path, help="Path to the folder that (recursively) contains the proto definitions")

    parser.add_argument("output_path", type=Path,
                        help="Folder to write output to.")

    args = parser.parse_args()

    # Setup logger
    logging.basicConfig(
        format="%(asctime)s %(levelname)s %(name)s %(message)s", level=args.log_level
    )
    logging.captureWarnings(True)
    warnings.filterwarnings("once")

    main(args)
