"""Tests for trustworthy exit-delay estimates without changing alarm state."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from custom_components.diagral.arming import ArmingTracker, group_state
import pytest

NOW = datetime(2026, 10, 26, 12, 0, tzinfo=UTC)


def configuration(delays=(90, 90)):
    """Return two configured groups and their presence mode."""
    return SimpleNamespace(
        groups=[
            SimpleNamespace(index=index, output_delay=delay, input_delay=20)
            for index, delay in enumerate(delays, 1)
        ],
        presence_group=[2],
    )


def status(value, groups=()):
    """Return a status read from the central."""
    return SimpleNamespace(status=value, activated_groups=list(groups))


class TestArmingTracker:
    """Only an accepted and timed command can supply a countdown."""

    def test_command_starts_from_send_time_not_response_time(self):
        """Network latency does not add time to the configured delay."""
        tracker = ArmingTracker()
        tracker.observe(
            status("TEMPO_GROUP"),
            configuration(),
            now=NOW + timedelta(seconds=5),
            command_started_at=NOW,
            target_groups=(1, 2),
        )
        assert tracker.started_at == NOW
        assert tracker.ends_at == NOW + timedelta(seconds=90)
        assert tracker.duration_seconds == 90
        assert tracker.remaining_seconds(NOW + timedelta(seconds=5)) == 85
        assert tracker.target_groups == (1, 2)
        assert tracker.source == "command"
        assert tracker.timing_known is True

    def test_poll_discovers_arming_without_inventing_start(self):
        """Opening HA or loading the integration cannot start a new90s timer."""
        tracker = ArmingTracker()
        tracker.observe(status("TEMPO_GROUP"), configuration(), now=NOW)
        assert tracker.is_arming is True
        assert tracker.duration_seconds == 90
        assert tracker.started_at is None
        assert tracker.ends_at is None
        assert tracker.remaining_seconds(NOW) is None
        assert tracker.source == "unknown"
        assert tracker.timing_known is False

    def test_repeated_observation_keeps_deadline(self):
        """Duplicate polls or notifications do not reset the countdown."""
        tracker = ArmingTracker()
        tracker.observe(
            status("TEMPO_GROUP"),
            configuration(),
            now=NOW,
            command_started_at=NOW,
            target_groups=(1,),
        )
        tracker.observe(
            status("TEMPO_GROUP"), configuration(), now=NOW + timedelta(seconds=30)
        )
        assert tracker.ends_at == NOW + timedelta(seconds=90)
        assert tracker.remaining_seconds(NOW + timedelta(seconds=30)) == 60

    @pytest.mark.parametrize(
        "value",
        ["OFF", "GROUP", "PRESENCE"],
        ids=["cancelled", "armed-away", "armed-home"],
    )
    def test_confirmed_transition_clears_countdown(self, value):
        """Only a central transition ends the arming phase."""
        tracker = ArmingTracker()
        tracker.observe(
            status("TEMPO_GROUP"),
            configuration(),
            now=NOW,
            command_started_at=NOW,
            target_groups=(1,),
        )
        tracker.observe(status(value), configuration(), now=NOW + timedelta(seconds=15))
        assert tracker.is_arming is False
        assert tracker.ends_at is None
        assert tracker.target_groups == ()

    def test_expiry_waits_for_confirmation(self):
        """A timer reaching zero never means that the system is armed."""
        tracker = ArmingTracker()
        tracker.observe(
            status("TEMPO_GROUP"),
            configuration(),
            now=NOW,
            command_started_at=NOW,
            target_groups=(1,),
        )
        tracker.observe(
            status("TEMPO_GROUP"), configuration(), now=NOW + timedelta(seconds=95)
        )
        assert tracker.is_arming is True
        assert tracker.remaining_seconds(NOW + timedelta(seconds=95)) == 0

    def test_changed_clock_invalidates_estimate(self):
        """A clock corrected backwards cannot extend the timer by an hour."""
        tracker = ArmingTracker()
        tracker.observe(
            status("TEMPO_GROUP"),
            configuration(),
            now=NOW,
            command_started_at=NOW,
            target_groups=(1,),
        )
        tracker.observe(
            status("TEMPO_GROUP"), configuration(), now=NOW - timedelta(hours=1)
        )
        assert tracker.is_arming is True
        assert tracker.timing_known is False
        assert tracker.ends_at is None

    @pytest.mark.parametrize(
        "delays",
        [(90, None), (90, -1), (90, True)],
        ids=["missing", "negative", "boolean"],
    )
    def test_missing_delay_never_uses_hardcoded_default(self, delays):
        """An unknown selected group delay produces no invented deadline."""
        tracker = ArmingTracker()
        tracker.observe(
            status("TEMPO_GROUP"),
            configuration(delays),
            now=NOW,
            command_started_at=NOW,
            target_groups=(1, 2),
        )
        assert tracker.ends_at is None
        assert tracker.duration_seconds is None

    def test_different_group_delays_use_selected_group(self):
        """The estimate uses the commanded group rather than a global90s value."""
        tracker = ArmingTracker()
        tracker.observe(
            status("TEMPO_GROUP"),
            configuration((90, 30)),
            now=NOW,
            command_started_at=NOW,
            target_groups=(2,),
        )
        assert tracker.duration_seconds == 30
        assert tracker.ends_at == NOW + timedelta(seconds=30)

    @pytest.mark.parametrize("previous", [status("GROUP", (2,)), status("PRESENCE")])
    def test_already_armed_target_does_not_extend_estimated_delay(self, previous):
        """Only groups not already armed contribute their configured delay."""
        tracker = ArmingTracker()
        tracker.observe(
            status("TEMPO_GROUP"),
            configuration((30, 90)),
            now=NOW,
            command_started_at=NOW,
            target_groups=(1, 2),
            previous_status=previous,
        )
        assert tracker.duration_seconds == 30
        assert tracker.ends_at == NOW + timedelta(seconds=30)

    def test_all_targets_already_armed_leave_estimated_delay_unknown(self):
        """An unexpected tempo response supplies no newly arming group."""
        tracker = ArmingTracker()
        tracker.observe(
            status("TEMPO_GROUP"),
            configuration(),
            now=NOW,
            command_started_at=NOW,
            target_groups=(1, 2),
            previous_status=status("GROUP", (1, 2)),
        )
        assert tracker.duration_seconds is None
        assert tracker.ends_at is None


class TestGroupState:
    """Group states are derived from central data without guessing a target."""

    @pytest.mark.parametrize(
        ("value", "active", "index", "expected"),
        [
            ("OFF", (), 1, "disarmed"),
            ("GROUP", (1,), 1, "armed"),
            ("GROUP", (1,), 2, "disarmed"),
            ("PRESENCE", (), 2, "armed"),
            ("PRESENCE", (), 1, "disarmed"),
            ("TEMPO_GROUP", (), 1, "unknown"),
            ("OTHER", (), 1, "unknown"),
        ],
        ids=[
            "off",
            "active",
            "inactive",
            "presence-active",
            "presence-inactive",
            "unknown-arming-target",
            "unknown-status",
        ],
    )
    def test_central_state(self, value, active, index, expected):
        """Presence uses configured groups; unknown tempo targets stay unknown."""
        assert (
            group_state(index, status(value, active), configuration(), ArmingTracker())
            == expected
        )

    def test_commanded_group_is_arming(self):
        """A group targeted by this HA command can be shown as arming."""
        tracker = ArmingTracker()
        tracker.observe(
            status("TEMPO_GROUP"),
            configuration(),
            now=NOW,
            command_started_at=NOW,
            target_groups=(1,),
            previous_status=status("OFF"),
        )
        assert (
            group_state(1, status("TEMPO_GROUP"), configuration(), tracker) == "arming"
        )
        assert (
            group_state(2, status("TEMPO_GROUP"), configuration(), tracker)
            == "disarmed"
        )

    def test_previously_armed_group_stays_armed(self):
        """An empty tempo list cannot erase a group already confirmed armed."""
        tracker = ArmingTracker()
        tracker.observe(
            status("TEMPO_GROUP"),
            configuration(),
            now=NOW,
            command_started_at=NOW,
            target_groups=(1,),
            previous_status=status("GROUP", (2,)),
        )
        assert (
            group_state(2, status("TEMPO_GROUP"), configuration(), tracker) == "armed"
        )

    def test_previously_armed_target_stays_armed(self):
        """Including an armed group in a command does not make it arming."""
        tracker = ArmingTracker()
        tracker.observe(
            status("TEMPO_GROUP"),
            configuration(),
            now=NOW,
            command_started_at=NOW,
            target_groups=(1, 2),
            previous_status=status("GROUP", (2,)),
        )
        assert (
            group_state(1, status("TEMPO_GROUP"), configuration(), tracker) == "arming"
        )
        assert (
            group_state(2, status("TEMPO_GROUP"), configuration(), tracker) == "armed"
        )

    @pytest.mark.parametrize(
        "previous",
        [
            SimpleNamespace(status="GROUP", activated_groups=None),
            SimpleNamespace(status="GROUP", activated_groups=[2, "invalid"]),
            SimpleNamespace(status="PRESENCE", activated_groups=[]),
        ],
        ids=[
            "missing-active-groups",
            "invalid-active-groups",
            "missing-presence-config",
        ],
    )
    def test_missing_previous_membership_does_not_establish_disarmed(self, previous):
        """Only complete valid prior memberships establish non-target states."""
        config = configuration()
        if previous.status == "PRESENCE":
            config.presence_group = None
        tracker = ArmingTracker()
        tracker.observe(
            status("TEMPO_GROUP"),
            config,
            now=NOW,
            command_started_at=NOW,
            target_groups=(1,),
            previous_status=previous,
        )
        assert tracker.previous_groups_known is False
        assert group_state(2, status("TEMPO_GROUP"), config, tracker) == "unknown"

    @pytest.mark.parametrize(
        "missing",
        ["activated_groups", "presence_group"],
        ids=["missing-active-groups", "missing-presence-config"],
    )
    def test_missing_membership_is_unknown(self, missing):
        """Missing membership cannot establish that a group is disarmed."""
        config = configuration()
        value = status("GROUP", (1,))
        if missing == "activated_groups":
            value.activated_groups = None
        else:
            value.status = "PRESENCE"
            config.presence_group = None
        assert group_state(2, value, config, ArmingTracker()) == "unknown"
