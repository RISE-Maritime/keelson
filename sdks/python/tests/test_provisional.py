"""messages/provisional.yaml agrees with the registries (specification §9)."""

import re
import sys
from pathlib import Path

import keelson
import pytest
from keelson.interfaces import get_procedures

REPO = Path(__file__).resolve().parents[3]
PROVISIONAL = REPO / "messages" / "provisional.yaml"
sys.path.insert(0, str(REPO / "scripts"))

pytestmark = pytest.mark.skipif(
    not PROVISIONAL.is_file(), reason="only exists in a repository checkout"
)


@pytest.fixture
def provisional():
    from provisional_lib import load

    return load(PROVISIONAL)


def test_every_entry_says_since_when_and_why(provisional):
    for kind, entries in provisional.items():
        for name, entry in entries.items():
            where = f"{kind}: {name}"
            assert set(entry) == {"since", "issue"}, where
            assert re.fullmatch(r"\d+\.\d+\.\d+", str(entry["since"])), where
            assert type(entry["issue"]) is int and entry["issue"] > 0, where


def test_every_subject_and_interface_is_registered(provisional):
    unknown = [
        s for s in provisional["subjects"] if not keelson.is_subject_well_known(s)
    ] + [i for i in provisional["interfaces"] if not keelson.is_interface_well_known(i)]
    assert not unknown, f"not in subjects.yaml or interfaces.yaml: {unknown}"


def test_every_procedure_exists_on_an_interface_that_is_not_provisional(provisional):
    for name in provisional["procedures"]:
        interface, version, procedure = name.split("/")
        key = f"{interface}/{version}"
        assert keelson.is_interface_well_known(key), name
        assert procedure in get_procedures(interface, version), name
        assert key not in provisional["interfaces"], f"{key} covers {name}"
