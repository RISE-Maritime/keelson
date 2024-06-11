import subprocess
from pathlib import Path

from setuptools import setup, find_namespace_packages

THIS_DIR: Path = Path(__file__).parent

## Generate code and copy tags.yaml into the package
subprocess.check_output(["bash", f"{THIS_DIR}/generate_python.sh"], cwd=THIS_DIR)


# Utility function to read the README file.
# Used for the long_description.  It's nice, because now 1) we have a top level
# README file and 2) it's easier to type in the README file than to put a raw
# string in below ...
def read(fname):
    return (THIS_DIR / fname).read_text()


setup(
    name="keelson",
    version="0.3.7-pre.3",
    license="Apache License 2.0",
    description="A python Software Development Kit for keelson",
    long_description=read("README.md"),
    long_description_content_type="text/markdown",
    url="https://github.com/RISE-Maritime/keelson/sdks/python",
    author="Fredrik Olsson",
    author_email="fredrik.x.olsson@ri.se",
    maintainer="Fredrik Olsson",
    maintainer_email="fredrik.x.olsson@ri.se",
    packages=find_namespace_packages(exclude=["tests", "dist", "build"]),
    python_requires=">=3.7",
    install_requires=[
        "eclipse-zenoh>=0.11.0rc3",
        "protobuf",
        "pyyaml",
        "parse",
        # "zenoh-cli>=0.5.0",
    ],
    include_package_data=True,
    package_data={
        "keelson": ["subjects.yaml"],
        "keelson.payloads": ["protobuf_file_descriptor_set.bin"],
    },
    entry_points={
        "zenoh_cli.codecs.encoders": [
            "keelson-enclose-from-text = keelson.codec:enclose_from_text",
            "keelson-enclose-from-base64 = keelson.codec:enclose_from_base64",
            "keelson-enclose-from-json = keelson.codec:enclose_from_json",
        ],
        "zenoh_cli.codecs.decoders": [
            "keelson-uncover-to-text = keelson.codec:uncover_to_text",
            "keelson-uncover-to-base64 = keelson.codec:uncover_to_base64",
            "keelson-uncover-to-json = keelson.codec:uncover_to_json",
        ],
    },
)
