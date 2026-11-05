import os
import sys
import yaml
import glob
import logging
import warnings
import argparse
from pathlib import Path

from protoc import PROTOC_INCLUDE_DIR

sys.path.insert(0, str(Path(__file__).resolve().parent))
import commented_yaml as cy  # noqa: E402


def proto_name_to_file_name(proto_name: str) -> str:
    return proto_name.replace(".", "_")


def md_escape(text: str) -> str:
    """A table cell may not contain a raw pipe or a newline."""
    return text.replace("|", "\\|").replace("\n", " ").strip()


def main(args: argparse.Namespace):

    # Read subject.yaml
    with args.subject_yaml_path.open() as fh:
        subjects = yaml.safe_load(fh)

    # Read qos.yaml (companion of subjects.yaml). Subjects not listed inherit
    # the default profile; a missing file degrades all to the default.
    qos_path = args.subject_yaml_path.parent / "qos.yaml"
    qos_subjects = {}
    qos_default = "standard"
    if qos_path.exists():
        with qos_path.open() as fh:
            qos_doc = yaml.safe_load(fh) or {}
        qos_subjects = qos_doc.get("subjects") or {}
        qos_default = qos_doc.get("default") or qos_default

    # The same file again, as text this time, so the comments survive. Roughly
    # half of subjects.yaml is comment, and that half is the only place the
    # units, the source_id conventions, the provenance and the deliberate
    # omissions are written down -- the safe_load above drops all of it.
    events = list(cy.walk(args.subject_yaml_path))
    cy.check_against(events, subjects, args.subject_yaml_path)

    # The set of seen protos
    well_known_protos = set()

    out = [
        "# Well-known subjects and protobuf types",
        "",
        "Generated from `messages/subjects.yaml`, in the order that file states"
        " them and including its commentary. A subject not listed under"
        " `subjects:` in `messages/qos.yaml` inherits the default profile --"
        " see [QoS profiles](qos-profiles.md).",
        "",
    ]

    HEADER = ["Subject", "Fully qualified protobuf type name", "QoS profile", "Notes"]
    rows = []

    def flush_rows():
        """Emit the run of subjects collected so far as one table."""
        if not rows:
            return
        out.append("| " + " | ".join(HEADER) + " |")
        out.append("|" + "|".join([" --- "] * len(HEADER)) + "|")
        for row in rows:
            out.append("| " + " | ".join(row) + " |")
        out.append("")
        rows.clear()

    seen_entry = False
    for event in events:
        if isinstance(event, cy.Heading):
            # The first heading is the file's own title (`### Well-known
            # subjects ###`), which this page already carries as its H1.
            if not seen_entry:
                continue
            flush_rows()
            out.append(f"## {event.text}")
            out.append("")
        elif isinstance(event, cy.Prose):
            flush_rows()
            out.extend(cy.render_prose(event.text))
            out.append("")
        elif isinstance(event, cy.Entry):
            seen_entry = True
            proto_name = event.value
            well_known_protos.add(proto_name)
            proto_file_name = proto_name_to_file_name(proto_name)
            rows.append(
                [
                    f"`{event.key}`",
                    f'<a href="../payloads/{proto_file_name}.dot.svg"'
                    f' class="glightbox">{proto_name}</a>',
                    f"`{qos_subjects.get(event.key, qos_default)}`",
                    md_escape(event.inline),
                ]
            )
    flush_rows()

    (args.output_path / "subjects-and-types.md").write_text(
        "\n".join(out).rstrip() + "\n"
    )

    # Recursively iterate over all proto files in the given base folder
    for proto_path in glob.glob(
        "**/*.proto", root_dir=args.proto_root_path, recursive=True
    ):
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
                        proto_file_name = proto_name_to_file_name(fully_qualified_name)
                        os.system(
                            f"protodot -src {proto_path} -select .{message_name} -generated {args.output_path / 'payloads'} -output {proto_file_name} -inc {PROTOC_INCLUDE_DIR}"
                        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        prog="DocGenerator-SubjectsTypes",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--log-level", type=int, default=logging.WARNING)
    parser.add_argument(
        "subject_yaml_path", type=Path, help="Path to a subjects.yaml file"
    )
    parser.add_argument(
        "proto_root_path",
        type=Path,
        help="Path to the folder that (recursively) contains the proto definitions",
    )

    parser.add_argument("output_path", type=Path, help="Folder to write output to.")

    args = parser.parse_args()

    # Setup logger
    logging.basicConfig(
        format="%(asctime)s %(levelname)s %(name)s %(message)s", level=args.log_level
    )
    logging.captureWarnings(True)
    warnings.filterwarnings("once")

    main(args)
