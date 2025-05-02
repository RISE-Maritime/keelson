import os
import yaml
import glob
import logging
import warnings
import argparse
from pathlib import Path

from mdutils import MdUtils


def main(args: argparse.Namespace):

    # Initialize the markdown file
    md_file = MdUtils(file_name=str(args.output_path / 'interfaces.md'),
                      title='Generic interfaces')

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
                        # md_file.new_inline_image(
                        #     proto_path.name, f"./interfaces/{proto_path.name}.dot.svg")
                        f'<a href="../interfaces/{proto_path.name}.dot.svg" class="glightbox"><img src="../interfaces/{proto_path.name}.dot.svg" alt="{proto_path.name}" /></a>'
                    )

                    os.system(
                        f"protodot -src {proto_path} -generated {args.output_path / "interfaces"} -output {proto_path.name}")

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
