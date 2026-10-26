"""Tests for per-equipment problem and inhibition binary sensors."""

from types import SimpleNamespace
from unittest.mock import MagicMock

from custom_components.diagral.device_health import (
    EQUIPMENT_FAULTS,
    EQUIPMENT_INHIBITED,
    EQUIPMENT_PROBLEM,
    DiagralDeviceBinarySensor,
    async_setup_device_binary_sensors,
)
from custom_components.diagral.equipment import iter_equipment
from pydiagral.models import (
    Anomalies,
    AnomalyDetail,
    AnomalyName,
    ConfAnomaliesModel,
    SensorModel,
)
import pytest

from homeassistant.components.binary_sensor import BinarySensorDeviceClass


def setup_fixture(devices):
    """Return a coordinator and entry whose data uses synthetic devices."""
    config = SimpleNamespace(
        alarm=SimpleNamespace(central=SimpleNamespace(serial="FAKECENTRAL")),
        sensors=devices,
        groups=[],
    )
    coordinator = MagicMock(data={"alarm_config": config}, last_update_success=True)
    entry = MagicMock(
        entry_id="entry", runtime_data=SimpleNamespace(coordinator=coordinator)
    )
    return coordinator, entry


class TestDeviceHealth:
    """Device status follows the latest known fields of the cloud snapshot."""

    def test_problem_reports_all_active_faults_and_recovers(self):
        """Both faults remain visible until a later read clears them."""
        flags = ConfAnomaliesModel(radio_alert=True, power_supply_alert=True)
        device = SensorModel(index=1, anomalies=flags)
        coordinator, entry = setup_fixture([device])
        sensor = DiagralDeviceBinarySensor(
            coordinator,
            entry,
            iter_equipment(coordinator.data["alarm_config"])[0],
            EQUIPMENT_PROBLEM,
        )

        assert sensor.is_on is True
        assert sensor.device_class == BinarySensorDeviceClass.PROBLEM
        assert set(sensor.extra_state_attributes["faults"]) == {
            "radio_alert",
            "power_supply_alert",
        }
        flags.radio_alert = False
        flags.power_supply_alert = False
        assert sensor.is_on is False
        assert sensor.extra_state_attributes["faults"] == []

    def test_inhibition_is_read_only_and_unknown_when_not_reported(self):
        """A missing flag is not equivalent to an enabled device."""
        device = SensorModel(index=1, inhibited=True)
        coordinator, entry = setup_fixture([device])
        sensor = DiagralDeviceBinarySensor(
            coordinator,
            entry,
            iter_equipment(coordinator.data["alarm_config"])[0],
            EQUIPMENT_INHIBITED,
        )

        assert sensor.is_on is True
        device.inhibited = None
        assert sensor.is_on is None
        coordinator.api.assert_not_called()

    async def test_discovery_adds_new_fields_and_devices_once(self):
        """A newly installed device is discovered on the existing poll."""
        device = SensorModel(
            index=1,
            inhibited=False,
            anomalies=ConfAnomaliesModel(radio_alert=False, mask_alert=False),
        )
        coordinator, entry = setup_fixture([device])
        added = []
        await async_setup_device_binary_sensors(MagicMock(), entry, added.extend)
        initial_ids = {entity.unique_id for entity in added}
        listener = coordinator.async_add_listener.call_args.args[0]
        listener()
        assert {entity.unique_id for entity in added} == initial_ids
        coordinator.data["alarm_config"].sensors.append(
            SensorModel(index=2, inhibited=False)
        )
        listener()

        assert len(added) == len({entity.unique_id for entity in added})
        assert any(
            "_sensors_2_equipment_inhibited" in entity.unique_id for entity in added
        )
        assert any(entity.entity_registry_enabled_default is False for entity in added)
        entry.async_on_unload.assert_called_once()

    async def test_camera_does_not_receive_invented_problem_flags(self):
        """Metadata-only devices have inventory records on the sensor platform."""
        coordinator, entry = setup_fixture([])
        coordinator.data["alarm_config"].cameras = [
            SimpleNamespace(index=1, name="Test camera")
        ]
        added = []
        await async_setup_device_binary_sensors(MagicMock(), entry, added.extend)

        assert added == []

    async def test_newly_reported_fault_field_is_discovered(self):
        """A field absent from an earlier response can later become usable."""
        device = SensorModel(index=1, anomalies=ConfAnomaliesModel(radio_alert=False))
        coordinator, entry = setup_fixture([device])
        added = []
        await async_setup_device_binary_sensors(MagicMock(), entry, added.extend)
        before = {entity.unique_id for entity in added}
        device.anomalies.power_supply_alert = True
        coordinator.async_add_listener.call_args.args[0]()
        after = {entity.unique_id for entity in added}

        assert after - before == {
            "entry_diagral_FAKECENTRAL_sensors_1_equipment_power_supply_alert"
        }
        assert (
            next(
                entity
                for entity in added
                if entity.unique_id.endswith("equipment_power_supply_alert")
            ).is_on
            is True
        )


class TestApiDeviceFaults:
    """The explicit anomaly list must not be hidden by configuration flags."""

    @pytest.mark.parametrize("configured", [False, None], ids=["false", "missing"])
    def test_api_fault_overrides_a_clear_or_missing_configuration(self, configured):
        """A known API fault drives both the device summary and its own sensor."""
        device = SensorModel(
            index=1, anomalies=ConfAnomaliesModel(power_supply_alert=configured)
        )
        coordinator, entry = setup_fixture([device])
        coordinator.data["anomalies"] = Anomalies(
            created_at=None,
            sensors=[
                AnomalyDetail(
                    index=1,
                    serial="PRIVATE-SERIAL",
                    anomaly_names=[AnomalyName(id=1384, name="lowBattery")],
                )
            ],
        )
        equipment = iter_equipment(coordinator.data["alarm_config"])[0]
        summary = DiagralDeviceBinarySensor(
            coordinator, entry, equipment, EQUIPMENT_PROBLEM
        )
        power = DiagralDeviceBinarySensor(
            coordinator,
            entry,
            equipment,
            next(
                description
                for description in EQUIPMENT_FAULTS
                if description.key == "equipment_power_supply_alert"
            ),
        )

        assert summary.is_on is True
        assert power.is_on is True
        assert summary.extra_state_attributes["faults"] == ["power_supply_alert"]
        assert summary.extra_state_attributes["fault_count"] == 1
        assert summary.extra_state_attributes["api_faults"] == [
            {"code": 1384, "name": "lowBattery", "field": "power_supply_alert"}
        ]
        assert "PRIVATE-SERIAL" not in str(summary.extra_state_attributes)

    def test_unknown_api_fault_keeps_the_device_in_problem(self):
        """Unmapped fault codes remain visible without inventing a known cause."""
        coordinator, entry = setup_fixture(
            [SensorModel(index=1, anomalies=ConfAnomaliesModel(radio_alert=False))]
        )
        coordinator.data["anomalies"] = Anomalies(
            created_at=None,
            sensors=[
                AnomalyDetail(
                    index=1,
                    anomaly_names=[AnomalyName(id=9000, name="newProtocolFault")],
                )
            ],
        )
        sensor = DiagralDeviceBinarySensor(
            coordinator,
            entry,
            iter_equipment(coordinator.data["alarm_config"])[0],
            EQUIPMENT_PROBLEM,
        )

        assert sensor.is_on is True
        assert sensor.extra_state_attributes["fault_count"] == 1
        assert sensor.extra_state_attributes["api_faults"] == [
            {"code": 9000, "name": "newProtocolFault", "field": None}
        ]
        assert sensor.extra_state_attributes["reported_faults"] == {
            "radio_alert": False
        }

    def test_api_fault_is_scoped_to_its_equipment_family(self):
        """A siren and a detector may reuse index 1 without sharing faults."""
        flags = ConfAnomaliesModel(power_supply_alert=False)
        coordinator, entry = setup_fixture([SensorModel(index=1, anomalies=flags)])
        coordinator.data["alarm_config"].sirens = [
            SimpleNamespace(index=1, anomalies=flags)
        ]
        coordinator.data["anomalies"] = Anomalies(
            created_at=None,
            sirens=[
                AnomalyDetail(
                    index=1,
                    anomaly_names=[AnomalyName(id=1384, name="lowBattery")],
                )
            ],
        )
        equipment = {
            item.category: item
            for item in iter_equipment(coordinator.data["alarm_config"])
        }
        detector = DiagralDeviceBinarySensor(
            coordinator, entry, equipment["sensors"], EQUIPMENT_PROBLEM
        )
        siren = DiagralDeviceBinarySensor(
            coordinator, entry, equipment["sirens"], EQUIPMENT_PROBLEM
        )

        assert detector.is_on is False
        assert detector.extra_state_attributes["api_faults"] == []
        assert siren.is_on is True

    def test_distinct_api_codes_do_not_duplicate_the_configuration_fault(self):
        """Keep two explicit API faults but do not count their matching flag again."""
        coordinator, entry = setup_fixture(
            [
                SensorModel(
                    index=1, anomalies=ConfAnomaliesModel(power_supply_alert=True)
                )
            ]
        )
        coordinator.data["anomalies"] = Anomalies(
            created_at=None,
            sensors=[
                AnomalyDetail(
                    index=1,
                    anomaly_names=[
                        AnomalyName(id=1384, name="lowBattery"),
                        AnomalyName(id=1400, name="batteryFault"),
                    ],
                )
            ],
        )
        sensor = DiagralDeviceBinarySensor(
            coordinator,
            entry,
            iter_equipment(coordinator.data["alarm_config"])[0],
            EQUIPMENT_PROBLEM,
        )

        assert sensor.extra_state_attributes["fault_count"] == 2
        assert [
            fault["code"] for fault in sensor.extra_state_attributes["api_faults"]
        ] == [
            1384,
            1400,
        ]

    async def test_api_fault_discovers_summary_and_known_fault_sensors(self):
        """A field reported only by the anomaly endpoint still becomes usable."""
        coordinator, entry = setup_fixture([SensorModel(index=1)])
        coordinator.data["anomalies"] = Anomalies(
            created_at=None,
            sensors=[
                AnomalyDetail(
                    index=1,
                    anomaly_names=[AnomalyName(id=1384, name="lowBattery")],
                )
            ],
        )
        added = []
        await async_setup_device_binary_sensors(MagicMock(), entry, added.extend)

        assert {entity.entity_description.key for entity in added} == {
            "equipment_problem",
            "equipment_power_supply_alert",
        }
        assert all(entity.is_on is True for entity in added)

    async def test_unmapped_fault_discovers_summary_without_invented_fields(self):
        """An undocumented cause must not be guessed as a battery or radio fault."""
        coordinator, entry = setup_fixture([SensorModel(index=1)])
        coordinator.data["anomalies"] = Anomalies(
            created_at=None,
            sensors=[AnomalyDetail(index=1, anomaly_names=[])],
        )
        added = []
        await async_setup_device_binary_sensors(MagicMock(), entry, added.extend)

        assert [entity.entity_description.key for entity in added] == [
            "equipment_problem"
        ]
        assert added[0].is_on is True
        assert added[0].extra_state_attributes["api_faults"] == [
            {"code": "unspecified", "name": "Unspecified fault", "field": None}
        ]

    def test_api_fault_clears_to_the_latest_configuration_value(self):
        """A later empty anomalies read clears the earlier low-battery report."""
        coordinator, entry = setup_fixture(
            [
                SensorModel(
                    index=1, anomalies=ConfAnomaliesModel(power_supply_alert=False)
                )
            ]
        )
        coordinator.data["anomalies"] = Anomalies(
            created_at=None,
            sensors=[
                AnomalyDetail(
                    index=1,
                    anomaly_names=[AnomalyName(id=1384, name="lowBattery")],
                )
            ],
        )
        sensor = DiagralDeviceBinarySensor(
            coordinator,
            entry,
            iter_equipment(coordinator.data["alarm_config"])[0],
            EQUIPMENT_PROBLEM,
        )
        assert sensor.is_on is True

        coordinator.data["anomalies"] = Anomalies(created_at=None)

        assert sensor.is_on is False
        assert sensor.extra_state_attributes["api_faults"] == []
        assert sensor.extra_state_attributes["fault_count"] == 0

    def test_empty_anomaly_list_does_not_invent_configuration_fields(self):
        """An unreported capability remains unknown when no active fault exists."""
        coordinator, entry = setup_fixture([SensorModel(index=1)])
        coordinator.data["anomalies"] = Anomalies(created_at=None)
        sensor = DiagralDeviceBinarySensor(
            coordinator,
            entry,
            iter_equipment(coordinator.data["alarm_config"])[0],
            EQUIPMENT_PROBLEM,
        )

        assert sensor.is_on is None
        assert sensor.extra_state_attributes["fault_count"] is None
