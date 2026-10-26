"""Expose readable faults, configured delays and communication diagnostics."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
)
from homeassistant.const import EntityCategory, UnitOfTime
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.util import dt as dt_util

from .connection import ClockDiagnostics, communication_state
from .const import (
    COMMUNICATION_STALE_SECONDS,
    COMMUNICATION_STATES,
    DOMAIN,
    FUTURE_TIMESTAMP_TOLERANCE,
    LOCAL_DIAGNOSTIC_INTERVAL,
)
from .entity import DiagralEntity
from .equipment import iter_equipment
from .faults import build_faults, fault_summary

if TYPE_CHECKING:
    from . import DiagralConfigEntry
    from .coordinator import DiagralDataUpdateCoordinator


@dataclass(frozen=True, kw_only=True)
class DiagralOverviewDescription(SensorEntityDescription):
    """Describe an additional local view over the shared coordinator."""

    available_during_outage: bool = False


OVERVIEW_SENSORS = (
    DiagralOverviewDescription(
        key="anomaly_summary",
        translation_key="anomaly_summary",
        icon="mdi:alert-circle-outline",
    ),
    DiagralOverviewDescription(
        key="fault_count", translation_key="fault_count", icon="mdi:alert"
    ),
    DiagralOverviewDescription(
        key="affected_device_count",
        translation_key="affected_device_count",
        icon="mdi:devices",
    ),
    DiagralOverviewDescription(
        key="arming_ends_at",
        translation_key="arming_ends_at",
        device_class=SensorDeviceClass.TIMESTAMP,
    ),
    DiagralOverviewDescription(
        key="exit_delay",
        translation_key="exit_delay",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.SECONDS,
    ),
    DiagralOverviewDescription(
        key="entry_delay",
        translation_key="entry_delay",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.SECONDS,
    ),
    DiagralOverviewDescription(
        key="last_success",
        translation_key="last_success",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        available_during_outage=True,
    ),
    DiagralOverviewDescription(
        key="communication_state",
        translation_key="communication_state",
        device_class=SensorDeviceClass.ENUM,
        options=list(COMMUNICATION_STATES),
        entity_category=EntityCategory.DIAGNOSTIC,
        available_during_outage=True,
    ),
    DiagralOverviewDescription(
        key="equipment_count",
        translation_key="equipment_count",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
)


async def async_setup_overview_sensors(
    hass: HomeAssistant,
    entry: DiagralConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Add views without starting any additional Diagral polling loop."""
    async_add_entities(
        DiagralOverviewSensor(entry.runtime_data.coordinator, description, entry)
        for description in OVERVIEW_SENSORS
    )


class DiagralOverviewSensor(DiagralEntity, SensorEntity):
    """A computed entity backed by the existing alarm snapshot."""

    entity_description: DiagralOverviewDescription

    def __init__(
        self,
        coordinator: DiagralDataUpdateCoordinator,
        description: DiagralOverviewDescription,
        entry: DiagralConfigEntry,
    ) -> None:
        """Use the central's existing identity and a stable additive suffix."""
        super().__init__(coordinator)
        self.entity_description = description
        serial = coordinator.data["alarm_config"].alarm.central.serial
        self._attr_unique_id = f"{entry.entry_id}_{DOMAIN}_{serial}_{description.key}"

    @property
    def available(self) -> bool:
        """Connection diagnostics remain readable while alarm data are unavailable."""
        return (
            self.entity_description.available_during_outage
            or self.coordinator.last_update_success
        )

    def _language(self) -> str:
        """Use HA's configured language for the free-text fault summary."""
        language = getattr(
            getattr(getattr(self, "hass", None), "config", None), "language", None
        )
        return language if isinstance(language, str) else "en"

    def _faults(self) -> list[dict[str, Any]]:
        """Present every fault without publishing the raw cloud response."""
        data = self.coordinator.data or {}
        return build_faults(
            data.get("anomalies"),
            data.get("alarm_config"),
            devices=data.get("devices_infos"),
            language=self._language(),
        )

    def _groups(self) -> list[dict[str, Any]]:
        """Return configured group delays, including unknown values."""
        config = (self.coordinator.data or {}).get("alarm_config")
        return [
            {
                "index": group.index,
                "name": group.name,
                "input_delay": group.input_delay,
                "output_delay": group.output_delay,
            }
            for group in getattr(config, "groups", None) or []
        ]

    @property
    def native_value(self) -> Any:
        """Read only the snapshot or recorded timing, never the network."""
        key = self.entity_description.key
        if key in ("anomaly_summary", "fault_count", "affected_device_count"):
            faults = self._faults()
            if key == "anomaly_summary":
                return fault_summary(faults, self._language())
            return (
                sum(len(item["faults"]) for item in faults)
                if key == "fault_count"
                else len(faults)
            )
        if key == "arming_ends_at":
            return self.coordinator.arming.ends_at
        if key in ("exit_delay", "entry_delay"):
            targets = self.coordinator.arming.target_groups
            groups = [
                group
                for group in self._groups()
                if not targets or group["index"] in targets
            ]
            field = "output_delay" if key == "exit_delay" else "input_delay"
            values = [group[field] for group in groups]
            return (
                max(values)
                if values and all(type(value) is int and value >= 0 for value in values)
                else None
            )
        if key == "last_success":
            return self.coordinator.last_success
        if key == "communication_state":
            return communication_state(self.coordinator)
        if key == "equipment_count":
            return len(
                iter_equipment((self.coordinator.data or {}).get("alarm_config"))
            )
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Keep details structured and indicate their last confirmed observation."""
        key = self.entity_description.key
        last_success = self.coordinator.last_success
        last_success = last_success if isinstance(last_success, datetime) else None
        attributes = {
            "last_success": last_success.isoformat() if last_success else None,
            "data_fresh": bool(
                last_success
                and self.coordinator.last_update_success
                and -FUTURE_TIMESTAMP_TOLERANCE
                <= (dt_util.utcnow() - last_success).total_seconds()
                <= COMMUNICATION_STALE_SECONDS
            ),
        }
        if key in ("anomaly_summary", "fault_count", "affected_device_count"):
            faults = self._faults()
            attributes.update(
                faults=faults,
                fault_count=sum(len(item["faults"]) for item in faults),
                affected_device_count=len(faults),
                summary=fault_summary(faults, self._language()),
                updated_at=attributes["last_success"],
            )
        elif key in ("exit_delay", "entry_delay"):
            attributes.update(
                groups=self._groups(),
                target_groups=list(self.coordinator.arming.target_groups),
                calculation="maximum_of_selected_groups",
            )
        elif key == "arming_ends_at":
            attributes.update(self.coordinator.arming.attributes())
        elif key == "communication_state":
            clock = getattr(self.coordinator, "clock_diagnostics", None)
            attributes.update(
                last_error=self.coordinator.last_error,
                http_status=getattr(self.coordinator, "last_error_status", None),
                webhook_status=self.coordinator.webhook_status,
                clock=clock.as_dict() if isinstance(clock, ClockDiagnostics) else None,
            )
        return attributes

    async def async_added_to_hass(self) -> None:
        """Refresh age diagnostics locally without sending periodic HTTP reads."""
        await super().async_added_to_hass()
        if self.entity_description.key == "communication_state":
            self.async_on_remove(
                async_track_time_interval(
                    self.hass,
                    self._async_age_update,
                    timedelta(seconds=LOCAL_DIAGNOSTIC_INTERVAL),
                )
            )

    @callback
    def _async_age_update(self, now: datetime) -> None:
        """Publish stale-data transitions even when polling has stopped."""
        self.async_write_ha_state()
