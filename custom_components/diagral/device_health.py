"""Per-equipment fault and inhibition binary sensors, without device actions."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import EQUIPMENT_DEFAULT_FAULT_KEYS, EQUIPMENT_FAULT_KEYS
from .equipment import Equipment, iter_equipment
from .equipment_entity import DiagralEquipmentEntity
from .faults import api_faults_for

if TYPE_CHECKING:
    from . import DiagralConfigEntry
    from .coordinator import DiagralDataUpdateCoordinator


@dataclass(frozen=True, kw_only=True)
class DiagralDeviceBinarySensorDescription(BinarySensorEntityDescription):
    """Describe a reported equipment state and whether the field exists."""

    is_on_fn: Callable[[Equipment], bool | None]
    exists_fn: Callable[[Equipment], bool]
    fault_key: str | None = None
    summarizes_faults: bool = False


EQUIPMENT_PROBLEM = DiagralDeviceBinarySensorDescription(
    key="equipment_problem",
    translation_key="equipment_problem",
    device_class=BinarySensorDeviceClass.PROBLEM,
    is_on_fn=lambda equipment: equipment.problem,
    exists_fn=lambda equipment: equipment.problem is not None,
    summarizes_faults=True,
)
EQUIPMENT_INHIBITED = DiagralDeviceBinarySensorDescription(
    key="equipment_inhibited",
    translation_key="equipment_inhibited",
    icon="mdi:shield-off-outline",
    entity_category=EntityCategory.DIAGNOSTIC,
    is_on_fn=lambda equipment: equipment.inhibited,
    exists_fn=lambda equipment: equipment.inhibited is not None,
)
EQUIPMENT_FAULTS = tuple(
    DiagralDeviceBinarySensorDescription(
        key=f"equipment_{name}",
        translation_key=f"equipment_{name}",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_registry_enabled_default=name in EQUIPMENT_DEFAULT_FAULT_KEYS,
        is_on_fn=lambda equipment, name=name: equipment.fault_flags.get(name),
        exists_fn=lambda equipment, name=name: (
            equipment.fault_flags.get(name) is not None
        ),
        fault_key=name,
    )
    for name in EQUIPMENT_FAULT_KEYS
)


async def async_setup_device_binary_sensors(
    hass: HomeAssistant,
    entry: DiagralConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Discover reported equipment states using the existing coordinator polls."""
    coordinator = entry.runtime_data.coordinator
    added: set[tuple[str, str]] = set()

    @callback
    def async_discover() -> None:
        entities = []
        for equipment in iter_equipment(coordinator.data.get("alarm_config")):
            api_faults = api_faults_for(
                coordinator.data.get("anomalies"), equipment.category, equipment.index
            )
            api_fields = {fault["field"] for fault in api_faults if fault["field"]}
            for description in (
                EQUIPMENT_PROBLEM,
                EQUIPMENT_INHIBITED,
                *EQUIPMENT_FAULTS,
            ):
                identity = equipment.key, description.key
                has_api_fault = (
                    description.summarizes_faults and bool(api_faults)
                ) or description.fault_key in api_fields
                if identity not in added and (
                    description.exists_fn(equipment) or has_api_fault
                ):
                    added.add(identity)
                    entities.append(
                        DiagralDeviceBinarySensor(
                            coordinator, entry, equipment, description
                        )
                    )
        if entities:
            async_add_entities(entities)

    async_discover()
    entry.async_on_unload(coordinator.async_add_listener(async_discover))


class DiagralDeviceBinarySensor(DiagralEquipmentEntity, BinarySensorEntity):
    """Report one equipment field and preserve unknown values."""

    entity_description: DiagralDeviceBinarySensorDescription

    def __init__(
        self,
        coordinator: DiagralDataUpdateCoordinator,
        entry: DiagralConfigEntry,
        equipment: Equipment,
        description: DiagralDeviceBinarySensorDescription,
    ) -> None:
        """Initialize a sensor without retaining the mutable API model."""
        super().__init__(coordinator, entry, equipment, description.key)
        self.entity_description = description

    @property
    def is_on(self) -> bool | None:
        """Keep any explicit API fault active despite a clear configuration flag."""
        equipment = self.equipment
        if equipment is None:
            return None
        api_faults = self._api_faults(equipment)
        if self.entity_description.summarizes_faults and api_faults:
            return True
        if self.entity_description.fault_key is not None and any(
            fault["field"] == self.entity_description.fault_key for fault in api_faults
        ):
            return True
        return self.entity_description.is_on_fn(equipment)

    def _api_faults(self, equipment: Equipment) -> tuple[dict[str, Any], ...]:
        """Read only the faults for this family and reported device index."""
        return api_faults_for(
            self.coordinator.data.get("anomalies"), equipment.category, equipment.index
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose both sources while retaining unmapped API codes and names."""
        equipment = self.equipment
        if equipment is None:
            return {}
        attributes = super().extra_state_attributes
        api_faults = self._api_faults(equipment)
        api_fields = {fault["field"] for fault in api_faults if fault["field"]}
        faults = list(equipment.active_faults)
        for fault in api_faults:
            name = fault["field"] or fault["name"]
            if name not in faults:
                faults.append(name)
        attributes["faults"] = faults
        attributes["api_faults"] = list(api_faults)
        attributes["fault_count"] = (
            len(api_faults)
            + sum(field not in api_fields for field in equipment.active_faults)
            if api_faults or equipment.problem is not None
            else None
        )
        return attributes
