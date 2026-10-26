"""Read-only group entities using the integration's existing status snapshots."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .arming import group_state
from .const import DOMAIN, GROUP_STATES
from .entity import DiagralEntity

if TYPE_CHECKING:
    from pydiagral.models import Group

    from . import DiagralConfigEntry
    from .coordinator import DiagralDataUpdateCoordinator


async def async_setup_group_sensors(
    hass: HomeAssistant,
    entry: DiagralConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Discover configured groups once, including additions after setup."""
    coordinator = entry.runtime_data.coordinator
    known: set[int] = set()

    @callback
    def discover() -> None:
        config = coordinator.data.get("alarm_config")
        groups = getattr(config, "groups", None) or []
        new = sorted(
            {
                group.index
                for group in groups
                if type(group.index) is int and group.index not in known
            }
        )
        if new:
            known.update(new)
            async_add_entities(
                [DiagralGroupSensor(coordinator, entry, index) for index in new]
            )

    discover()
    entry.async_on_unload(coordinator.async_add_listener(discover))


class DiagralGroupSensor(DiagralEntity, SensorEntity):
    """Report one group without implying knowledge of an unreported arming target."""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_translation_key = "group_status"
    _attr_options: ClassVar[list[str]] = list(GROUP_STATES)

    def __init__(
        self,
        coordinator: DiagralDataUpdateCoordinator,
        entry: DiagralConfigEntry,
        index: int,
    ) -> None:
        """Initialize a group entity whose index is stable across label changes."""
        super().__init__(coordinator)
        self._index = index
        serial = coordinator.data["alarm_config"].alarm.central.serial
        self._attr_unique_id = (
            f"{entry.entry_id}_{DOMAIN}_{serial}_group_{index}_status"
        )
        self._attr_translation_placeholders = {"group": self._group.name or str(index)}

    @property
    def _group(self) -> Group | None:
        """Return the group's current configuration, including changed delays."""
        config = self.coordinator.data.get("alarm_config")
        return next(
            (
                group
                for group in getattr(config, "groups", None) or []
                if group.index == self._index
            ),
            None,
        )

    @property
    def available(self) -> bool:
        """A removed group or unavailable central cannot report a valid state."""
        return super().available and self._group is not None

    @property
    def native_value(self) -> str:
        """Return what the central and a locally observed command establish."""
        return group_state(
            self._index,
            self.coordinator.data.get("system_status"),
            self.coordinator.data.get("alarm_config"),
            self.coordinator.arming,
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the group label and real configured entry and exit delays."""
        group = self._group
        if group is None:
            return {}
        return {
            "group_index": self._index,
            "group_name": group.name,
            "input_delay": group.input_delay,
            "output_delay": group.output_delay,
        }
