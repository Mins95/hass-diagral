"""Observe existing HTTP reads to explain communication and clock failures."""

from __future__ import annotations

from datetime import datetime
from email.utils import parsedate_to_datetime
import time
from typing import Any
from urllib.parse import urlparse

from aiohttp import TraceConfig
from pydiagral import api as api_module

from homeassistant.util import dt as dt_util

from .const import (
    CLOCK_SKEW_THRESHOLD,
    COMMUNICATION_STALE_SECONDS,
    FUTURE_TIMESTAMP_TOLERANCE,
    HTTP_SERVER_ERROR,
)


class ClockDiagnostics:
    """Estimate server minus system time from authenticated HTTPS responses."""

    def __init__(self) -> None:
        """Start without assuming either a good or an invalid system clock."""
        self.offset_seconds: int | None = None
        self.checked_at: datetime | None = None

    @property
    def skewed(self) -> bool:
        """Return whether an observed mismatch can invalidate signed requests."""
        return (
            self.offset_seconds is not None
            and abs(self.offset_seconds) > CLOCK_SKEW_THRESHOLD
        )

    def observe(self, date: str | None, started: float, elapsed: float) -> None:
        """Interpret only timezone-aware server times, allowing for round trip."""
        if not date:
            return
        try:
            moment = parsedate_to_datetime(date)
        except ValueError, TypeError, OverflowError:
            return
        if moment.tzinfo is None:
            return
        self.offset_seconds = round(moment.timestamp() - (started + elapsed / 2))
        self.checked_at = moment

    def as_dict(self) -> dict[str, Any]:
        """Expose timing only, never headers, URLs or credentials."""
        return {
            "offset_seconds": self.offset_seconds,
            "skewed": self.skewed if self.offset_seconds is not None else None,
            "checked_at": self.checked_at.isoformat() if self.checked_at else None,
        }

    def trace_config(self) -> TraceConfig:
        """Attach passive observation to the session used by pydiagral."""
        trace = TraceConfig()
        expected_host = urlparse(api_module.BASE_URL).hostname

        async def started(session, context, params) -> None:
            context.started_wall = time.time()
            context.started_monotonic = time.monotonic()

        async def finished(session, context, params) -> None:
            if params.url.host != expected_host:
                return
            self.observe(
                params.response.headers.get("Date"),
                context.started_wall,
                time.monotonic() - context.started_monotonic,
            )

        trace.on_request_start.append(started)
        trace.on_request_end.append(finished)
        return trace


def communication_state(coordinator: Any, *, now: datetime | None = None) -> str:
    """Distinguish a service outage, refused credentials and stale readings."""
    clock = getattr(coordinator, "clock_diagnostics", None)
    if isinstance(clock, ClockDiagnostics) and clock.skewed:
        return "clock_skew"
    error = getattr(coordinator, "last_error", None)
    status = getattr(coordinator, "last_error_status", None)
    if error == "PinCodeError":
        return "pin_refused"
    if error == "AuthenticationError" or status in (401, 403):
        return "authentication_failed"
    if getattr(coordinator, "cloud_reachable", None) is False:
        return "cloud_unavailable"
    if error == "TimeoutError":
        return "timeout"
    if error == "ClientError":
        return "network_error"
    if error in ("ValidationError", "ModelValidationError", "ValueError", "TypeError"):
        return "invalid_response"
    if error == "ServerError" or (
        isinstance(status, int) and status >= HTTP_SERVER_ERROR
    ):
        return "cloud_unavailable"
    if error:
        return "api_error"
    moment = getattr(coordinator, "last_success", None)
    if not isinstance(moment, datetime) or not getattr(
        coordinator, "last_update_success", False
    ):
        return "unknown"
    age = ((now or dt_util.utcnow()) - moment).total_seconds()
    if age < -FUTURE_TIMESTAMP_TOLERANCE:
        return "unknown"
    if age > COMMUNICATION_STALE_SECONDS:
        return "stale"
    return "connected"
