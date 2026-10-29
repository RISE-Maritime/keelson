"""The SDK's declared protobuf range must cover the bundled generated code.

Each *_pb2.py refuses to import on a protobuf runtime older than the protoc
that generated it. If the generator is bumped without raising the floor in
sdks/python/pyproject.toml, pip happily installs a runtime that cannot import
keelson. CI also runs the SDK tests on the floor and on the newest allowed
release, so both ends of the range are exercised.
"""

import re
import tomllib
from pathlib import Path

import pytest
from packaging.requirements import Requirement
from packaging.version import Version

pytestmark = pytest.mark.unit

SDK = Path(__file__).resolve().parent.parent
STAMP = re.compile(
    r"ValidateProtobufRuntimeVersion\(\s*_runtime_version\.Domain\.PUBLIC,"
    r"\s*(\d+),\s*(\d+),\s*(\d+),"
)


def declared_protobuf() -> Requirement:
    project = tomllib.loads((SDK / "pyproject.toml").read_text())["project"]
    [req] = [
        Requirement(d)
        for d in project["dependencies"]
        if Requirement(d).name == "protobuf"
    ]
    return req


def gencode_stamps() -> dict[Path, Version]:
    stamps = {}
    for path in (SDK / "keelson").rglob("*_pb2.py"):
        match = STAMP.search(path.read_text())
        assert match, f"{path} has no protobuf runtime version check"
        stamps[path] = Version(".".join(match.groups()))
    assert stamps, "no generated code; run generate_python.sh"
    return stamps


def floor(req: Requirement) -> Version:
    [spec] = [s for s in req.specifier if s.operator == ">="]
    return Version(spec.version)


def test_floor_covers_every_generated_file():
    minimum = floor(declared_protobuf())
    too_new = {
        str(p.relative_to(SDK)): str(v)
        for p, v in gencode_stamps().items()
        if v > minimum
    }
    assert not too_new, (
        f"{len(too_new)} generated files need a newer protobuf than the "
        f"declared floor {minimum}; raise it in sdks/python/pyproject.toml. "
        f"Highest: {max(too_new.values(), key=Version)}"
    )


def test_range_is_capped_below_an_untested_major():
    req = declared_protobuf()
    assert any(s.operator == "<" for s in req.specifier), str(req)
