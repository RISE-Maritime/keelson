"""The read-only default, the allow-list and self-protection."""

from keelson.interfaces.ErrorResponse_pb2 import ErrorResponse

from container_control.guard import ControlGuard

SELF_ID = "s" * 64


def test_default_is_read_only():
    assert ControlGuard().control_enabled is False


class TestReadOnly:
    guard = ControlGuard()

    def test_refuses_every_container(self):
        decision = self.guard.decide("anything", "id", "stop")
        assert not decision.allowed
        assert decision.code == ErrorResponse.Code.PERMISSION_DENIED

    def test_reason_names_the_flag_that_would_change_it(self):
        assert "--allow-control" in self.guard.decide("anything", verb="stop").reason

    def test_nothing_is_controllable(self):
        assert not self.guard.controllable("anything")


class TestAllowList:
    guard = ControlGuard(control_enabled=True, allow_globs=("keelson-*", "mcap"))

    def test_glob_match_is_allowed(self):
        assert self.guard.decide("keelson-router", verb="stop").allowed

    def test_exact_match_is_allowed(self):
        assert self.guard.decide("mcap", verb="stop").allowed

    def test_miss_is_refused_and_the_reason_lists_the_globs(self):
        decision = self.guard.decide("grafana", verb="stop")
        assert not decision.allowed
        assert "keelson-*" in decision.reason and "grafana" in decision.reason

    def test_star_means_everything(self):
        assert (
            ControlGuard(control_enabled=True, allow_globs=("*",))
            .decide("x", verb="stop")
            .allowed
        )


class TestSelfProtection:
    guard = ControlGuard(
        control_enabled=True,
        # Note the glob WOULD match the responder's own name.
        allow_globs=("*",),
        self_identity=frozenset({"keelson-container-control", SELF_ID}),
    )

    def test_self_by_name_is_refused_even_though_the_glob_matches(self):
        decision = self.guard.decide("keelson-container-control", verb="stop")
        assert not decision.allowed
        assert "own container" in decision.reason

    def test_self_by_full_id_is_refused(self):
        assert not self.guard.decide("some-other-name", SELF_ID, "restart").allowed

    def test_self_by_short_id_is_refused(self):
        # Callers see the full id; --self-container-name may have resolved a short one.
        short = ControlGuard(
            control_enabled=True,
            allow_globs=("*",),
            self_identity=frozenset({SELF_ID[:12]}),
        )
        assert not short.decide("other", SELF_ID, "stop").allowed

    def test_the_reason_names_the_verb_that_was_refused(self):
        assert (
            "restart"
            in self.guard.decide("keelson-container-control", verb="restart").reason
        )

    def test_a_different_container_is_still_allowed(self):
        assert self.guard.decide("grafana", "g" * 64, "stop").allowed

    def test_a_short_id_that_is_not_a_prefix_is_not_self(self):
        assert not self.guard.is_self("other", "b" * 64)


def test_controllable_agrees_with_decide_for_every_case():
    guard = ControlGuard(
        control_enabled=True,
        allow_globs=("keelson-*",),
        self_identity=frozenset({"keelson-self"}),
    )
    for name, cid in [("keelson-router", "r"), ("grafana", "g"), ("keelson-self", "s")]:
        assert guard.controllable(name, cid) == guard.decide(name, cid).allowed


class TestRemoveIsGatedSeparately:
    """--allow-control does not grant removal, and never has to.

    The upgrade case is the one that matters: a responder that has run with
    control enabled for months pulls an image whose interface grew `remove`. It
    must still remove nothing.
    """

    control_only = ControlGuard(control_enabled=True, allow_globs=("*",))

    def test_control_alone_removes_nothing(self):
        assert self.control_only.remove_enabled is False
        decision = self.control_only.decide_remove("anything", "id")
        assert not decision.allowed
        assert decision.code == ErrorResponse.Code.PERMISSION_DENIED

    def test_the_reason_says_control_is_not_enough(self):
        reason = self.control_only.decide_remove("anything").reason
        assert "--allow-remove" in reason
        assert "does not enable it" in reason

    def test_nothing_is_removable_though_everything_is_controllable(self):
        assert self.control_only.controllable("anything", "id") is True
        assert self.control_only.removable("anything", "id") is False

    def test_read_only_refuses_remove_and_names_both_flags(self):
        reason = ControlGuard().decide_remove("anything").reason
        assert "--allow-control" in reason and "--allow-remove" in reason

    def test_remove_globs_alone_do_not_enable_it_without_control(self):
        # app.py refuses this combination at startup; the guard must not rely on
        # that, because a guard built anywhere else would then be wide open.
        guard = ControlGuard(remove_globs=("*",))
        assert guard.remove_enabled is False
        assert not guard.decide_remove("anything").allowed


class TestTheTwoAllowListsAreIndependent:
    """ "Restart anything, delete only the scratch containers" has to be sayable."""

    guard = ControlGuard(
        control_enabled=True,
        allow_globs=("*",),
        remove_globs=("scratch-*",),
        self_identity=frozenset({SELF_ID, "me"}),
    )

    def test_removal_is_narrower_than_control(self):
        assert self.guard.controllable("keelson-router") is True
        assert self.guard.removable("keelson-router") is False
        assert self.guard.removable("scratch-1") is True

    def test_the_refusal_names_the_remove_list_not_the_control_one(self):
        reason = self.guard.decide_remove("keelson-router").reason
        assert "remove allow-list" in reason
        assert "scratch-*" in reason

    def test_removal_can_also_be_wider_than_control(self):
        # Not a configuration to recommend, but the lists are independent rather
        # than nested, and a guard that silently intersected them would make the
        # narrower case above a lie too.
        guard = ControlGuard(
            control_enabled=True, allow_globs=("keelson-*",), remove_globs=("*",)
        )
        assert guard.controllable("grafana") is False
        assert guard.removable("grafana") is True

    def test_the_responders_own_container_is_never_removable(self):
        # Even under remove_globs=("*",) -- self-protection is checked first.
        assert self.guard.removable("me") is False
        assert self.guard.removable("scratch-1", SELF_ID) is False

    def test_remove_enabled_is_derived_not_stored(self):
        # There is no boolean to disagree with the list, which is the failure
        # mode --allow-control needed a startup check for.
        assert self.guard.remove_enabled is True
        assert (
            ControlGuard(control_enabled=True, allow_globs=("*",)).remove_enabled
            is False
        )
