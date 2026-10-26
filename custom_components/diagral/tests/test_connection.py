"""Test clock and communication diagnostics without an external request."""

from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from types import SimpleNamespace

from custom_components.diagral.connection import ClockDiagnostics, communication_state
import pytest

NOW = datetime(2026, 10, 26, 12, tzinfo=UTC)


class TestClockDiagnostics:
    """The Date header identifies the one-hour skew seen on the NAS."""

    def test_clock_ahead_by_one_hour_is_detected(self):
        """Date interpretation uses UTC regardless of daylight saving time."""
        clock = ClockDiagnostics()
        clock.observe(format_datetime(NOW, usegmt=True), NOW.timestamp() + 3600, 0)
        assert clock.offset_seconds == -3600
        assert clock.skewed

    def test_round_trip_midpoint_avoids_a_false_skew(self):
        """A normal short request is not classified as an invalid system clock."""
        clock = ClockDiagnostics()
        clock.observe(format_datetime(NOW, usegmt=True), NOW.timestamp() - 2, 4)
        assert clock.offset_seconds == 0
        assert not clock.skewed

    @pytest.mark.parametrize(
        "date",
        [None, "invalid", "26 Oct 2026 12:00:00"],
        ids=["missing", "invalid", "no-timezone"],
    )
    def test_invalid_date_does_not_invent_a_clock_offset(self, date):
        """Malformed or absent server time stays unknown."""
        clock = ClockDiagnostics()
        clock.observe(date, NOW.timestamp(), 0)
        assert clock.offset_seconds is None

    def test_corrected_clock_replaces_old_diagnostic(self):
        """A successful new sample clears a previous one-hour mismatch."""
        clock = ClockDiagnostics()
        clock.observe(format_datetime(NOW, usegmt=True), NOW.timestamp() + 3600, 0)
        clock.observe(format_datetime(NOW, usegmt=True), NOW.timestamp(), 0)
        assert not clock.skewed


class TestCommunicationState:
    """An authentication refusal does not hide the actual clock problem."""

    def test_skew_is_reported_before_authentication(self):
        """An observed invalid timestamp is actionable without replacing keys."""
        clock = ClockDiagnostics()
        clock.observe(format_datetime(NOW, usegmt=True), NOW.timestamp() + 3600, 0)
        coordinator = SimpleNamespace(
            clock_diagnostics=clock,
            last_error="AuthenticationError",
            last_error_status=401,
            last_update_success=False,
            cloud_reachable=True,
            last_success=NOW,
        )
        assert communication_state(coordinator, now=NOW) == "clock_skew"

    @pytest.mark.parametrize(
        ("error", "status", "reachable", "expected"),
        [
            ("AuthenticationError", 401, True, "authentication_failed"),
            ("PinCodeError", 403, True, "pin_refused"),
            ("ServerError", 503, False, "cloud_unavailable"),
            ("TimeoutError", None, True, "timeout"),
            ("ClientError", None, True, "network_error"),
            ("ModelValidationError", None, True, "invalid_response"),
        ],
        ids=["auth", "pin", "cloud", "timeout", "network", "invalid-response"],
    )
    def test_error_categories(self, error, status, reachable, expected):
        """Known error types remain distinguishable in the diagnostic sensor."""
        coordinator = SimpleNamespace(
            last_error=error,
            last_error_status=status,
            last_update_success=False,
            cloud_reachable=reachable,
            last_success=NOW,
        )
        assert communication_state(coordinator, now=NOW) == expected

    def test_old_success_is_marked_stale(self):
        """An old successful snapshot is not described as freshly connected."""
        coordinator = SimpleNamespace(
            last_error=None,
            last_update_success=True,
            cloud_reachable=True,
            last_success=NOW - timedelta(hours=1),
        )
        assert communication_state(coordinator, now=NOW) == "stale"

    def test_future_success_after_a_clock_change_is_not_fresh(self):
        """Moving the system clock backwards invalidates the old observation."""
        coordinator = SimpleNamespace(
            last_error=None,
            last_update_success=True,
            cloud_reachable=True,
            last_success=NOW + timedelta(hours=1),
        )
        assert communication_state(coordinator, now=NOW) == "unknown"
