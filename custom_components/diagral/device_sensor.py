"""Inventory entities for declared equipment, including metadata-only cameras."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .equipment import Equipment, iter_equipment
from .equipment_entity import DiagralEquipmentEntity

if TYPE_CHECKING:
    from . import DiagralConfigEntry
    from .coordinator import DiagralDataUpdateCoordinator

EQUIPMENT_INFORMATION = SensorEntityDescription(
    key="equipment_information",
    translation_key="equipment_information",
    device_class=SensorDeviceClass.ENUM,
    options=["configured"],
    icon="mdi:information-outline",
    entity_category=EntityCategory.DIAGNOSTIC,
)


async def async_setup_device_sensors(
    hass: HomeAssistant,
    entry: DiagralConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create an inventory entity for each device the central declares."""
    coordinator = entry.runtime_data.coordinator
    added: set[str] = set()

    @callback
    def async_discover() -> None:
        entities = []
        for equipment in iter_equipment(coordinator.data.get("alarm_config")):
            if equipment.key not in added:
                added.add(equipment.key)
                entities.append(DiagralDeviceSensor(coordinator, entry, equipment))
        if entities:
            async_add_entities(entities)

    async_discover()
    entry.async_on_unload(coordinator.async_add_listener(async_discover))


class DiagralDeviceSensor(DiagralEquipmentEntity, SensorEntity):
    """Describe declared hardware without inventing a live operating state."""

    entity_description = EQUIPMENT_INFORMATION

    def __init__(
        self,
        coordinator: DiagralDataUpdateCoordinator,
        entry: DiagralConfigEntry,
        equipment: Equipment,
    ) -> None:
        """Initialize the inventory record of one reported device."""
        super().__init__(coordinator, entry, equipment, EQUIPMENT_INFORMATION.key)

    @property
    def native_value(self) -> str | None:
        """Presence in a configuration is not a claim of working video or radio."""
        return "configured" if self.equipment is not None else None
