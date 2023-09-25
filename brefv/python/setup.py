import os
import subprocess
from pathlib import Path

from distutils.spawn import find_executable
from setuptools import setup

THIS_DIR: Path = Path(__file__).parent

# Compile proto definitions
PROTO_PATH = THIS_DIR.parent / "messages"
PROTO_DEFINITIONS = map(str, PROTO_PATH.glob("*.proto"))
OUTPUT_PATH = THIS_DIR / "brefv" / "messages"
OUTPUT_PATH.mkdir(parents=True, exist_ok=True)

subprocess.check_output(
    [
        "protoc",
        "--proto_path",
        f"{PROTO_PATH}",
        f"--python_out={OUTPUT_PATH}",
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
    url="https://github.com/MO-RISE/keel/brefv/python",
    author="Fredrik Olsson",
    author_email="fredrik.x.olsson@ri.se",
    maintainer="Fredrik Olsson",
    maintainer_email="fredrik.x.olsson@ri.se",
    packages=["brefv"],
    python_requires=">=3.7",
    install_requires=["protobuf"],
)
