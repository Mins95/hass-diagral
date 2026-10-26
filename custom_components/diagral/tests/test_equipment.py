"""Tests for equipment snapshots and stable child-device identities."""

from types import SimpleNamespace

from custom_components.diagral.equipment import iter_equipment
from pydiagral.models import ConfAnomaliesModel, Group, SensorModel
import pytest


def configuration(**categories):
    """Build a minimal configuration with a synthetic central."""
    return SimpleNamespace(
        alarm=SimpleNamespace(central=SimpleNamespace(serial="FAKECENTRAL")),
        groups=[Group(index=2, name="Test group")],
        **categories,
    )


class TestEquipment:
    """Snapshots retain useful values without publishing identifiers."""

    def test_identity_uses_category_and_index_not_name_or_list_position(self):
        """Reordering or renaming devices cannot create new entity identities."""
        sensor = SensorModel(index=1, label="Before", group=2)
        other = SensorModel(index=2, label="Other")
        first = iter_equipment(configuration(sensors=[sensor, other], sirens=[sensor]))
        sensor.label = "After"
        second = iter_equipment(configuration(sensors=[other, sensor], sirens=[sensor]))

        assert {item.key for item in first} == {item.key for item in second}
        assert len({item.key for item in first}) == 3
        assert next(item for item in second if item.key == "sensors:1").name == "After"
        assert (
            next(item for item in second if item.key == "sensors:1").group_name
            == "Test group"
        )

    @pytest.mark.parametrize(
        ("flags", "expected"),
        [
            (ConfAnomaliesModel(radio_alert=True, power_supply_alert=True), True),
            (ConfAnomaliesModel(radio_alert=False), False),
            (ConfAnomaliesModel(), None),
            (None, None),
        ],
        ids=["multiple-faults", "known-clear", "unknown-flags", "missing-flags"],
    )
    def test_problem_preserves_unknown_instead_of_claiming_healthy(
        self, flags, expected
    ):
        """Missing fault information must not turn into a false healthy state."""
        item = iter_equipment(
            configuration(sensors=[SensorModel(index=1, anomalies=flags)])
        )[0]

        assert item.problem is expected

    def test_all_active_faults_are_kept(self):
        """One device may report several simultaneous problems."""
        item = iter_equipment(
            configuration(
                sensors=[
                    SensorModel(
                        index=1,
                        anomalies=ConfAnomaliesModel(
                            radio_alert=True, power_supply_alert=True, mask_alert=False
                        ),
                    )
                ]
            )
        )[0]

        assert set(item.active_faults) == {"radio_alert", "power_supply_alert"}
        assert item.fault_flags["mask_alert"] is False
        assert item.fault_flags["sensor_alert"] is None

    def test_missing_index_is_not_replaced_with_list_position(self):
        """An unstable fallback would associate a fault with the wrong device."""
        items = iter_equipment(
            configuration(sensors=[SensorModel(label="No index"), SensorModel(index=2)])
        )

        assert [item.key for item in items] == ["sensors:2"]

    def test_secrets_are_not_copied_to_snapshot_or_attributes(self):
        """Only the metadata needed by equipment entities is retained."""
        item = iter_equipment(
            configuration(
                sensors=[
                    SensorModel(
                        index=1,
                        serial="PRIVATE_DEVICE_SERIAL",
                        uid="PRIVATE_UID",
                        ref_code="TEST-REF",
                    )
                ]
            )
        )[0]

        assert item.reference == "TEST-REF"
        assert "PRIVATE" not in repr(item)
        assert "serial" not in item.attributes
        assert "uid" not in item.attributes

    def test_camera_metadata_does_not_invent_a_health_state(self):
        """A camera declaration is an inventory record, not a working video feed."""
        camera = SimpleNamespace(index=1, name="Test camera", type="HIK", group=2)
        item = iter_equipment(configuration(cameras=[camera]))[0]

        assert item.name == "Test camera"
        assert item.problem is None
        assert item.inhibited is None
        assert item.fault_flags == {}

    def test_firmware_and_hardware_metadata_are_allowlisted(self):
        """Only reported firmware versions and known hardware fields are kept."""
        device = SimpleNamespace(
            index=1,
            ref_code="TEST-REF",
            type=9,
            gamme=2,
            subtype=3,
            is_video=False,
            inhibited=False,
            can_inhibit=True,
            firmwares=SimpleNamespace(plug="1.2.3", boxradio="4", secret_key="PRIVATE"),
        )
        item = iter_equipment(configuration(transmitters=[device]))[0]

        assert item.attributes["firmwares"] == {"boxradio": "4", "plug": "1.2.3"}
        assert item.product_type == 9
        assert item.gamme == 2
        assert item.subtype == 3
        assert item.is_video is False
        assert item.firmware == "boxradio: 4; plug: 1.2.3"
        assert "PRIVATE" not in repr(item)
