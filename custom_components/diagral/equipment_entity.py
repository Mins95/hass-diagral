"""Base entities for Diagral equipment attached to the central device."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.core import callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceInfo

from .const import BRAND, DOMAIN
from .entity import DiagralEntity
from .equipment import Equipment, iter_equipment

if TYPE_CHECKING:
    from . import DiagralConfigEntry
    from .coordinator import DiagralDataUpdateCoordinator


class DiagralEquipmentEntity(DiagralEntity):
    """Bind an entity to one equipment while preserving the central as parent."""

    def __init__(
        self,
        coordinator: DiagralDataUpdateCoordinator,
        config_entry: DiagralConfigEntry,
        equipment: Equipment,
        key: str,
    ) -> None:
        """Keep only the stable identity and initial device metadata."""
        super().__init__(coordinator)
        self._equipment_key = equipment.key
        self._config_entry_id = config_entry.entry_id
        self._initial_equipment = equipment
        self._central_serial = coordinator.data["alarm_config"].alarm.central.serial
        self._attr_unique_id = (
            f"{config_entry.entry_id}_{DOMAIN}_{self._central_serial}_"
            f"{equipment.category}_{equipment.index}_{key}"
        )

    @property
    def equipment(self) -> Equipment | None:
        """Read the newest snapshot instead of retaining stale per-device flags."""
        return next(
            (
                item
                for item in iter_equipment(
                    self.coordinator.data.get("alarm_config"),
                    only_key=self._equipment_key,
                )
                if item.key == self._equipment_key
            ),
            None,
        )

    @property
    def available(self) -> bool:
        """A failed poll or missing equipment must not advertise healthy data."""
        return super().available and self.equipment is not None

    @property
    def device_info(self) -> DeviceInfo:
        """Associate the child with its central through the device registry."""
        equipment = self.equipment or self._initial_equipment
        info = DeviceInfo(
            identifiers={(DOMAIN, f"{self._central_serial}_{self._equipment_key}")},
            name=equipment.name,
            manufacturer=BRAND,
            model=equipment.reference
            or str(equipment.product_type or equipment.category),
            sw_version=equipment.firmware,
        )
        if getattr(self, "hass", None) is not None:
            registry = dr.async_get(self.hass)
            parent = registry.async_get_device_by_identifier(
                (DOMAIN, self._central_serial), self._config_entry_id
            )
            if parent is None:
                parent = registry.async_get_or_create(
                    config_entry_id=self._config_entry_id,
                    **self.coordinator.get_alarm_central_device(),
                )
            info["via_device_id"] = parent.id
        return info

    async def async_added_to_hass(self) -> None:
        """Associate the device and keep future firmware changes visible."""
        await super().async_added_to_hass()
        self._async_sync_metadata()

    @callback
    def _handle_coordinator_update(self) -> None:
        """Refresh registry metadata as well as the entity's state."""
        self._async_sync_metadata()
        super()._handle_coordinator_update()

    @callback
    def _async_sync_metadata(self) -> None:
        """Update reported metadata without changing IDs or user-assigned names."""
        if getattr(self, "hass", None) is None or self.equipment is None:
            return
        registry = dr.async_get(self.hass)
        device = registry.async_get_device_by_identifier(
            (DOMAIN, f"{self._central_serial}_{self._equipment_key}"),
            self._config_entry_id,
        )
        if device is None:
            return
        info = self.device_info
        changes = {
            key: info[key]
            for key in ("name", "model", "sw_version", "via_device_id")
            if info.get(key) is not None and getattr(device, key) != info[key]
        }
        if changes:
            registry.async_update_device(device.id, **changes)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return only the latest equipment metadata and reported states."""
        equipment = self.equipment
        return equipment.attributes if equipment is not None else {}
