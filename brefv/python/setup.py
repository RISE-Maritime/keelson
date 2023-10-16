import shutil
import subprocess
from pathlib import Path

from setuptools import setup, find_namespace_packages

THIS_DIR: Path = Path(__file__).parent

## Copy tags.yaml into package

shutil.copy("../tags.yaml", "brefv/tags.yaml")

# Compile envelope definition

ENVELOPE_PATH = THIS_DIR.parent / "envelope.proto"
ENVELOPE_OUTPUT_PATH = THIS_DIR / "brefv"

subprocess.check_output(
    [
        "protoc",
        "--proto_path",
        f"{ENVELOPE_PATH.parent}",
        f"--python_out={ENVELOPE_OUTPUT_PATH}",
        f"{ENVELOPE_PATH}",
    ]
)

# Compile proto definitions
PAYLOAD_PATH = THIS_DIR.parent / "payloads"
PROTO_DEFINITIONS = map(str, PAYLOAD_PATH.glob("**/*.proto"))

PAYLOAD_OUTPUT_PATH = THIS_DIR / "brefv" / "payloads/"
PAYLOAD_OUTPUT_PATH.mkdir(parents=True, exist_ok=True)


subprocess.check_output(
    [
        "protoc",
        "--proto_path",
        f"{PAYLOAD_PATH}",
        f"--python_out={PAYLOAD_OUTPUT_PATH}",
        f"--descriptor_set_out={PAYLOAD_OUTPUT_PATH / 'protobuf_file_descriptor_set.bin'}",
        "--include_imports",
        *PROTO_DEFINITIONS,
    ]
)


# Utility function to read the README file.
# Used for the long_description.  It's nice, because now 1) we have a top level
# README file and 2) it's easier to type in the README file than to put a raw
# string in below ...
def read(fname):
    return (THIS_DIR / fname).read_text()


setup(
    name="brefv",
    version="0.1.0",
    license="Apache License 2.0",
    description="brefv",
    long_description=read("README.md"),
    long_description_content_type="text/markdown",
    url="https://github.com/MO-RISE/keelson/brefv/python",
    author="Fredrik Olsson",
    author_email="fredrik.x.olsson@ri.se",
    maintainer="Fredrik Olsson",
    maintainer_email="fredrik.x.olsson@ri.se",
    packages=find_namespace_packages(exclude=["tests", "dist", "build"]),
    python_requires=">=3.7",
    install_requires=["protobuf", "pyyaml"],
    include_package_data=True,
    package_data={
        "brefv": ["tags.yaml"],
        "brefv.payloads": ["protobuf_file_descriptor_set.bin"],
    },
)
