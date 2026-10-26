"""Tests for equipment inventory sensors, including cameras without health data."""

from types import SimpleNamespace
from unittest.mock import MagicMock

from custom_components.diagral.device_sensor import (
    DiagralDeviceSensor,
    async_setup_device_sensors,
)
from custom_components.diagral.equipment import iter_equipment


def setup_fixture():
    """Return a synthetic camera declaration without stream or health data."""
    camera = SimpleNamespace(index=1, name="Test camera", group=2, type="HIK")
    config = SimpleNamespace(
        alarm=SimpleNamespace(central=SimpleNamespace(serial="FAKECENTRAL")),
        cameras=[camera],
        groups=[],
    )
    coordinator = MagicMock(data={"alarm_config": config}, last_update_success=True)
    entry = MagicMock(
        entry_id="entry", runtime_data=SimpleNamespace(coordinator=coordinator)
    )
    return coordinator, entry


class TestDeviceInventory:
    """Inventory is truthful about the fields the central supplies."""

    def test_camera_is_configured_without_claiming_working_video(self):
        """A camera creates a child device while retaining only metadata."""
        coordinator, entry = setup_fixture()
        sensor = DiagralDeviceSensor(
            coordinator, entry, iter_equipment(coordinator.data["alarm_config"])[0]
        )

        assert sensor.native_value == "configured"
        assert sensor.translation_key == "equipment_information"
        assert sensor.extra_state_attributes["equipment_type"] == "cameras"
        assert sensor.extra_state_attributes["fault_count"] is None
        assert "stream" not in str(sensor.extra_state_attributes)

    async def test_every_equipment_has_one_inventory_entity(self):
        """Repeated updates do not add duplicate inventory records."""
        coordinator, entry = setup_fixture()
        added = []
        await async_setup_device_sensors(MagicMock(), entry, added.extend)
        coordinator.async_add_listener.call_args.args[0]()

        assert len(added) == 1
        assert (
            added[0].unique_id
            == "entry_diagral_FAKECENTRAL_cameras_1_equipment_information"
        )
        entry.async_on_unload.assert_called_once()
