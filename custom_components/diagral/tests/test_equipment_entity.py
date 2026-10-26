"""Tests for equipment identities and child-device metadata."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from custom_components.diagral.equipment import iter_equipment
from custom_components.diagral.equipment_entity import DiagralEquipmentEntity
from pydiagral.models import SensorModel


def configuration(**categories):
    """Build a synthetic configuration with no private identifiers."""
    return SimpleNamespace(
        alarm=SimpleNamespace(central=SimpleNamespace(serial="FAKECENTRAL")),
        groups=[],
        **categories,
    )


class TestEquipmentEntity:
    """Child devices remain associated with their Diagral central."""

    def test_child_device_uses_stable_identity_and_via_device(self):
        """The serial identifies the parent internally, not in state attributes."""
        config = configuration(sensors=[SensorModel(index=1, ref_code="TEST-REF")])
        coordinator = MagicMock(data={"alarm_config": config}, last_update_success=True)
        item = iter_equipment(config)[0]
        entity = DiagralEquipmentEntity(
            coordinator, MagicMock(entry_id="entry"), item, "test"
        )

        assert entity.unique_id == "entry_diagral_FAKECENTRAL_sensors_1_test"
        assert entity.device_info["identifiers"] == {
            ("diagral", "FAKECENTRAL_sensors:1")
        }
        entity.hass = MagicMock()
        registry = MagicMock()
        registry.async_get_device_by_identifier.return_value = SimpleNamespace(
            id="parent-device"
        )
        with patch(
            "homeassistant.helpers.device_registry.async_get", return_value=registry
        ):
            assert entity.device_info["via_device_id"] == "parent-device"
        assert entity.device_info["model"] == "TEST-REF"
        assert "FAKECENTRAL" not in str(entity.extra_state_attributes)

    def test_removal_and_failed_refresh_make_equipment_unavailable(self):
        """An old snapshot must not advertise a missing device as available."""
        config = configuration(sensors=[SensorModel(index=1)])
        coordinator = MagicMock(data={"alarm_config": config}, last_update_success=True)
        entity = DiagralEquipmentEntity(
            coordinator, MagicMock(entry_id="entry"), iter_equipment(config)[0], "test"
        )

        coordinator.last_update_success = False
        assert entity.available is False

    def test_parent_is_created_before_a_new_child(self):
        """Concurrent platform setup must still attach every child to its central."""
        config = configuration(sensors=[SensorModel(index=1)])
        coordinator = MagicMock(data={"alarm_config": config}, last_update_success=True)
        coordinator.get_alarm_central_device.return_value = {
            "identifiers": {("diagral", "FAKECENTRAL")},
            "name": "Central",
        }
        entity = DiagralEquipmentEntity(
            coordinator, MagicMock(entry_id="entry"), iter_equipment(config)[0], "test"
        )
        entity.hass = MagicMock()
        registry = MagicMock()
        registry.async_get_device_by_identifier.return_value = None
        registry.async_get_or_create.return_value = SimpleNamespace(id="created-parent")
        with patch(
            "homeassistant.helpers.device_registry.async_get", return_value=registry
        ):
            assert entity.device_info["via_device_id"] == "created-parent"
        registry.async_get_or_create.assert_called_once_with(
            config_entry_id="entry",
            identifiers={("diagral", "FAKECENTRAL")},
            name="Central",
        )
        coordinator.last_update_success = True
        config.sensors = []
        assert entity.available is False
