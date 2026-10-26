"""Track exit-delay estimates while the central remains authoritative."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import math
from typing import TYPE_CHECKING, Any

from homeassistant.util import dt as dt_util

from .const import (
    ARMING_SOURCE_COMMAND,
    ARMING_SOURCE_UNKNOWN,
    STATUS_GROUP,
    STATUS_OFF,
    STATUS_PRESENCE,
    STATUS_TEMPO_GROUP,
)

if TYPE_CHECKING:
    from pydiagral.models import AlarmConfiguration, SystemStatus


def _indices(values: Any) -> tuple[int, ...]:
    """Keep valid protocol group indices, preserving no transport identifiers."""
    if not isinstance(values, (tuple, list)):
        return ()
    return tuple(sorted({value for value in values if type(value) is int}))


def _duration(
    config: AlarmConfiguration,
    targets: tuple[int, ...],
    *,
    armed_groups: tuple[int, ...] = (),
) -> int | None:
    """Estimate the maximum configured delay among newly targeted groups."""
    groups = getattr(config, "groups", None) or []
    if not isinstance(groups, list):
        return None
    delays = {
        group.index: group.output_delay
        for group in groups
        if type(getattr(group, "index", None)) is int
    }
    selected = (
        [delays.get(index) for index in targets if index not in armed_groups]
        if targets
        else list(delays.values())
    )
    if not selected or any(type(delay) is not int or delay < 0 for delay in selected):
        return None
    if targets:
        return max(selected)
    return selected[0] if len(set(selected)) == 1 else None


@dataclass
class ArmingTracker:
    """Hold a command-derived estimate; expiry never changes the alarm state.

    ``source == command`` means the start was observed before sending an accepted
    HA command. It is an estimate, since the central does not return its own start
    timestamp. Discovery through polling supplies no trustworthy countdown.
    """

    is_arming: bool = False
    started_at: datetime | None = None
    ends_at: datetime | None = None
    duration_seconds: int | None = None
    target_groups: tuple[int, ...] = ()
    previously_armed_groups: tuple[int, ...] = ()
    previous_groups_known: bool = False
    source: str | None = None

    @property
    def timing_known(self) -> bool:
        """Return whether a command supplied both an origin and a duration."""
        return self.started_at is not None and self.ends_at is not None

    def remaining_seconds(self, now: datetime | None = None) -> int | None:
        """Return an estimate, or unknown after an invalid backward clock jump."""
        now = now or dt_util.utcnow()
        if not self.timing_known or now < self.started_at:
            return None
        return max(0, math.ceil((self.ends_at - now).total_seconds()))

    def clear(self) -> None:
        """Forget an ended or cancelled arming attempt."""
        self.is_arming = False
        self.started_at = self.ends_at = None
        self.duration_seconds = None
        self.target_groups = ()
        self.previously_armed_groups = ()
        self.previous_groups_known = False
        self.source = None

    def observe(
        self,
        status: SystemStatus,
        config: AlarmConfiguration,
        *,
        now: datetime | None = None,
        command_started_at: datetime | None = None,
        target_groups: tuple[int, ...] | list[int] | None = None,
        previous_status: SystemStatus | None = None,
    ) -> None:
        """Record the central's state without fabricating an unobserved start."""
        now = now or dt_util.utcnow()
        value = getattr(status, "status", "")
        value = value.lower() if isinstance(value, str) else ""
        if value != STATUS_TEMPO_GROUP:
            if value in (STATUS_OFF, STATUS_GROUP, STATUS_PRESENCE):
                self.clear()
            return
        self.is_arming = True
        if target_groups is not None:
            previous = getattr(previous_status, "status", "")
            previous = previous.lower() if isinstance(previous, str) else ""
            previous_membership = (
                getattr(config, "presence_group", None)
                if previous == STATUS_PRESENCE
                else getattr(previous_status, "activated_groups", None)
                if previous == STATUS_GROUP
                else ()
                if previous == STATUS_OFF
                else None
            )
            self.previous_groups_known = isinstance(
                previous_membership, (tuple, list)
            ) and all(type(index) is int for index in previous_membership)
            self.previously_armed_groups = (
                _indices(previous_membership) if self.previous_groups_known else ()
            )
        if self.started_at is not None and now < self.started_at:
            self.started_at = self.ends_at = None
            self.source = ARMING_SOURCE_UNKNOWN
        if command_started_at is not None:
            self.target_groups = _indices(target_groups)
            self.duration_seconds = _duration(
                config, self.target_groups, armed_groups=self.previously_armed_groups
            )
            if command_started_at.tzinfo is not None and command_started_at <= now:
                self.started_at = dt_util.as_utc(command_started_at)
                self.source = ARMING_SOURCE_COMMAND
                self.ends_at = (
                    self.started_at + timedelta(seconds=self.duration_seconds)
                    if self.duration_seconds is not None
                    else None
                )
            else:
                self.started_at = self.ends_at = None
                self.source = ARMING_SOURCE_UNKNOWN
        elif target_groups is not None:
            self.target_groups = _indices(target_groups)
            self.duration_seconds = _duration(
                config, self.target_groups, armed_groups=self.previously_armed_groups
            )
            self.started_at = self.ends_at = None
            self.source = ARMING_SOURCE_UNKNOWN
        elif self.source is None:
            self.duration_seconds = _duration(config, ())
            self.source = ARMING_SOURCE_UNKNOWN

    def attributes(self) -> dict[str, Any]:
        """Return the public panel attributes without transport or user data."""
        return {
            "arming_started_at": self.started_at.isoformat()
            if self.started_at
            else None,
            "arming_ends_at": self.ends_at.isoformat() if self.ends_at else None,
            "arming_duration": self.duration_seconds,
            "arming_timing_known": self.timing_known,
            "arming_source": self.source,
            "arming_target_groups": list(self.target_groups),
        }


def group_state(
    index: int,
    status: SystemStatus,
    config: AlarmConfiguration,
    arming: ArmingTracker,
) -> str:
    """Describe one group, leaving an unknown arming target unknown."""
    value = getattr(status, "status", "")
    value = value.lower() if isinstance(value, str) else ""
    if value == STATUS_OFF:
        return "disarmed"
    if value == STATUS_PRESENCE:
        presence = getattr(config, "presence_group", None)
        if not isinstance(presence, (tuple, list)) or any(
            type(item) is not int for item in presence
        ):
            return "unknown"
        return "armed" if index in _indices(presence) else "disarmed"
    reported_active = getattr(status, "activated_groups", None)
    active = _indices(reported_active)
    if value == STATUS_GROUP:
        if not isinstance(reported_active, (tuple, list)) or any(
            type(item) is not int for item in reported_active
        ):
            return "unknown"
        return "armed" if index in active else "disarmed"
    if value == STATUS_TEMPO_GROUP and arming.target_groups:
        if index in arming.previously_armed_groups:
            return "armed"
        if index in arming.target_groups:
            return "arming"
        if index in active:
            return "armed"
        if arming.previous_groups_known:
            return "disarmed"
    return "unknown"
