import subprocess
from pathlib import Path

from setuptools import setup, find_namespace_packages

THIS_DIR: Path = Path(__file__).parent
BREFV_ROOT: Path = THIS_DIR.parent

## Generate code and copy tags.yaml into the package
subprocess.check_output(
    [
        "bash",
        f"{BREFV_ROOT}/generate_code.sh"
    ],
    cwd=BREFV_ROOT
)


# Utility function to read the README file.
# Used for the long_description.  It's nice, because now 1) we have a top level
# README file and 2) it's easier to type in the README file than to put a raw
# string in below ...
def read(fname):
    return (THIS_DIR / fname).read_text()


setup(
    name="keelson-sdk",
    version="0.2.0-pre.1",
    license="Apache License 2.0",
    description="A python Software Development Kit for keelson",
    long_description=read("README.md"),
    long_description_content_type="text/markdown",
    url="https://github.com/MO-RISE/keelson/sdks/python",
    author="Fredrik Olsson",
    author_email="fredrik.x.olsson@ri.se",
    maintainer="Fredrik Olsson",
    maintainer_email="fredrik.x.olsson@ri.se",
    packages=find_namespace_packages(exclude=["tests", "dist", "build"]),
    python_requires=">=3.7",
    install_requires=["eclipse-zenoh==0.10.1-rc", "protobuf", "pyyaml", "parse"],
    include_package_data=True,
    package_data={
        "keelson": ["tags.yaml"],
        "keelson.payloads": ["protobuf_file_descriptor_set.bin"],
    },
)
