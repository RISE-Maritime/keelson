"""Working out which container this process is."""

import pytest

from container_control import selfid

from container_control_fakes import snapshot

FULL_ID = "0123456789abcdef" * 4
MOUNTINFO = (
    "1234 1200 0:59 /docker/containers/"
    + FULL_ID
    + "/hostname /etc/hostname rw,relatime\n"
    "1235 1200 0:59 / /sys/fs/cgroup ro\n"
)
CGROUP_V2_NAMESPACED = "0::/\n"
CGROUP_V1 = "12:pids:/docker/" + FULL_ID + "\n"


@pytest.fixture
def files(tmp_path):
    def write(mountinfo: str, cgroup: str):
        m, c = tmp_path / "mountinfo", tmp_path / "cgroup"
        m.write_text(mountinfo)
        c.write_text(cgroup)
        return str(m), str(c)

    return write


class TestIdFromProc:
    def test_mountinfo_yields_the_full_id(self, files):
        assert selfid.id_from_proc(*files(MOUNTINFO, CGROUP_V2_NAMESPACED)) == FULL_ID

    def test_cgroup_v1_is_the_fallback(self, files):
        assert selfid.id_from_proc(*files("", CGROUP_V1)) == FULL_ID

    def test_namespaced_cgroup_v2_alone_yields_nothing(self, files):
        assert selfid.id_from_proc(*files("", CGROUP_V2_NAMESPACED)) == ""

    def test_missing_files_yield_nothing(self):
        assert selfid.id_from_proc("/nonexistent/a", "/nonexistent/b") == ""


class TestResolve:
    def test_explicit_name_wins_over_proc(self, files):
        identity, how = selfid.resolve(
            "my-container", mountinfo_path=files(MOUNTINFO, "")[0]
        )
        assert "my-container" in identity
        assert how == "--self-container-name"

    def test_explicit_name_is_enriched_with_the_looked_up_id(self):
        snap = snapshot("my-container", FULL_ID)
        identity, _ = selfid.resolve("my-container", lookup=lambda _: snap)
        assert {"my-container", FULL_ID, FULL_ID[:12]} <= identity

    def test_explicit_name_survives_a_failed_lookup(self):
        # The operator said so; the container may simply not exist yet.
        def boom(_):
            raise RuntimeError("no such container")

        identity, _ = selfid.resolve("my-container", lookup=boom)
        assert identity == frozenset({"my-container"})

    def test_proc_is_used_when_no_name_was_given(self, files):
        m, c = files(MOUNTINFO, CGROUP_V2_NAMESPACED)
        identity, how = selfid.resolve(None, mountinfo_path=m, cgroup_path=c)
        assert FULL_ID in identity and FULL_ID[:12] in identity
        assert how == "/proc self-inspection"

    def test_exactly_one_label_match_resolves(self, files):
        m, c = files("", CGROUP_V2_NAMESPACED)
        snap = snapshot("labelled", FULL_ID)
        identity, how = selfid.resolve(
            None, list_by_label=lambda _: [snap], mountinfo_path=m, cgroup_path=c
        )
        assert {"labelled", FULL_ID} <= identity
        assert selfid.SELF_LABEL in how

    def test_two_label_matches_refuse_to_guess(self, files):
        m, c = files("", CGROUP_V2_NAMESPACED)
        snaps = [snapshot("a", "a" * 64), snapshot("b", "b" * 64)]
        identity, how = selfid.resolve(
            None, list_by_label=lambda _: snaps, mountinfo_path=m, cgroup_path=c
        )
        assert identity == frozenset() and how == ""

    def test_nothing_resolves_to_nothing(self, files):
        m, c = files("", CGROUP_V2_NAMESPACED)
        identity, how = selfid.resolve(None, mountinfo_path=m, cgroup_path=c)
        assert identity == frozenset() and how == ""

    def test_a_failing_label_query_is_not_fatal(self, files):
        m, c = files("", CGROUP_V2_NAMESPACED)

        def boom(_):
            raise RuntimeError("daemon down")

        identity, _ = selfid.resolve(
            None, list_by_label=boom, mountinfo_path=m, cgroup_path=c
        )
        assert identity == frozenset()
