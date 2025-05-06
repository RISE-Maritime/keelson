import os
import yaml
import glob
import logging
import warnings
import argparse
from pathlib import Path

from mdutils import MdUtils
from protoc import PROTOC_INCLUDE_DIR


def proto_name_to_file_name(proto_name: str) -> str:
    return f"{proto_name.replace(".", "_")}"


def main(args: argparse.Namespace):

    # Read subject.yaml
    with args.subject_yaml_path.open() as fh:
        subjects = yaml.safe_load(fh)

    # Initialize the markdown file
    md_file = MdUtils(file_name=str(args.output_path / 'subjects-and-types.md'),
                      title='Well-known subjects and protobuf types')

    markdown_table = ["Subject", "Fully qualified protobuf type name"]

    # The set of seen protos
    well_known_protos = set()

    # Iterate over all subjects, proto names
    for subject, proto_name in subjects.items():

        # Add to a set since we can have many subjects with the same proto name
        well_known_protos.add(proto_name)

        # Construct link to svg-file according to nameing convention
        proto_file_name = proto_name_to_file_name(proto_name)

        # Add entry to markdown table
        markdown_table.append(f"``{subject}``")
        markdown_table.append(
            f'<a href="../payloads/{proto_file_name}.dot.svg" class="glightbox">{proto_name}</a>'
        )

    # Finish markdown file
    md_file.new_table(2, len(markdown_table)//2,
                      text=markdown_table, text_align="left")
    md_file.create_md_file()

    # Recursively iterate over all proto files in the given base folder
    for proto_path in glob.glob("**/*.proto", root_dir=args.proto_root_path, recursive=True):
        proto_path = args.proto_root_path / proto_path

        with open(proto_path) as fh:

            package_name = ""

            # Find all message defined in the file
            for line in fh.readlines():

                # We find the package name
                if line.startswith("package "):
                    package_name = line.split(" ")[-1].rstrip().strip(";")
                    print(f"Found package name: {package_name}")

                # and then for each messag definitions
                elif line.startswith("message "):
                    message_name = line.split(" ")[1].strip("{")
                    fully_qualified_name = f"{package_name}.{message_name}"
                    print(f"Found message: {fully_qualified_name}")

                    # if in set -> Generate svg-file using os.system call to protodot using same naming convention as above
                    if fully_qualified_name in well_known_protos:
                        proto_file_name = proto_name_to_file_name(
                            fully_qualified_name)
                        os.system(
                            f"protodot -src {proto_path} -select .{message_name} -generated {args.output_path / "payloads"} -output {proto_file_name} -inc {PROTOC_INCLUDE_DIR}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        prog="DocGenerator-SubjectsTypes",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--log-level", type=int, default=logging.WARNING)
    parser.add_argument("subject_yaml_path", type=Path,
                        help="Path to a subjects.yaml file")
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
