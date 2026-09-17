"""Target-scoped liveliness (#253): a contact producer must be discoverable as one."""

import argparse
import time

import pytest
import zenoh

from conftest import ais2keelson
from keelson.scaffolding import create_zenoh_config, subject_liveliness_keys

pytestmark = pytest.mark.unit


def _args(**kw):
    base = dict(publish_raw=False, publish_json=False, publish_fields=False)
    base.update(kw)
    return argparse.Namespace(**base)


def test_field_subjects_are_targeted_and_raw_is_not():
    plain, targeted = ais2keelson.liveliness_subjects(
        _args(publish_raw=True, publish_json=True, publish_fields=True)
    )
    assert plain == ["raw", "raw_json"]
    assert targeted == list(ais2keelson.AIS_FIELD_SUBJECTS)
    assert not set(plain) & set(targeted)


def test_nothing_targeted_without_fields():
    plain, targeted = ais2keelson.liveliness_subjects(_args(publish_raw=True))
    assert plain == ["raw"]
    assert targeted == []


@pytest.mark.e2e
def test_running_connector_advertises_target_tokens(
    connector_process_factory, zenoh_endpoints
):
    """The token a consumer queries with `.../pubsub/*/**/@target` must exist."""
    proc = connector_process_factory(
        "ais",
        "ais2keelson",
        [
            "--realm",
            "test-realm",
            "--entity-id",
            "test-vessel",
            "--source-id",
            "ais-rx",
            "--publish-raw",
            "--publish-fields",
            "--mode",
            "peer",
            "--connect",
            zenoh_endpoints["connect"],
        ],
        stdin_pipe=True,
    )
    session = zenoh.open(
        create_zenoh_config(
            mode="peer", connect=None, listen=[zenoh_endpoints["listen"]]
        )
    )
    proc.start()
    try:
        plain, target = subject_liveliness_keys(
            "test-realm", "test-vessel", "location_fix", "ais-rx", targeted=True
        )
        raw_plain = subject_liveliness_keys(
            "test-realm", "test-vessel", "raw", "ais-rx"
        )[0]

        def alive(key):
            return [
                r
                for r in session.liveliness().get(key, timeout=1.0)
                if r.ok is not None
            ]

        deadline = time.monotonic() + 20
        while time.monotonic() < deadline and not alive(target):
            time.sleep(0.2)
        assert alive(target), f"no target-scoped token at {target}"
        assert alive(plain), "plain token must still be declared beside the target one"
        assert alive(raw_plain)
        assert not alive(f"{raw_plain}/@target"), "raw is not published about targets"
    finally:
        session.close()
        proc.stop()


def test_digitraffic_surface_is_entirely_targeted():
    import importlib.util
    import pathlib
    from importlib.machinery import SourceFileLoader

    path = (
        pathlib.Path(__file__).resolve().parent.parent
        / "bin"
        / "digitraffic2keelson.py"
    )
    loader = SourceFileLoader("digitraffic2keelson", str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)

    subjects = mod.liveliness_subjects(_args(publish_raw=True, publish_fields=True))
    assert subjects == ["raw_json", *mod.DIGITRAFFIC_FIELD_SUBJECTS]
