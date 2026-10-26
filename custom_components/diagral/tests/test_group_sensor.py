"""Tests for individual group states and their configured delays."""

from types import SimpleNamespace
from unittest.mock import MagicMock

from custom_components.diagral.arming import ArmingTracker
from custom_components.diagral.group_sensor import (
    DiagralGroupSensor,
    async_setup_group_sensors,
)
import pytest


def make_entry():
    """Return a group config entry without any cloud client."""
    config = SimpleNamespace(
        groups=[
            SimpleNamespace(
                index=1, name="Ground floor", input_delay=20, output_delay=90
            )
        ],
        presence_group=[1],
        alarm=SimpleNamespace(central=SimpleNamespace(serial="FAKESERIAL")),
    )
    coordinator = MagicMock()
    coordinator.data = {
        "alarm_config": config,
        "system_status": SimpleNamespace(status="OFF", activated_groups=[]),
    }
    coordinator.arming = ArmingTracker()
    coordinator.last_update_success = True
    entry = SimpleNamespace(
        entry_id="entry1",
        runtime_data=SimpleNamespace(coordinator=coordinator),
        async_on_unload=MagicMock(),
    )
    return entry


class TestGroupSensor:
    """The group's identity does not depend on labels or list order."""

    def test_identity_and_attributes(self):
        """A group gets stable identity, a translated name and configured delays."""
        entry = make_entry()
        entity = DiagralGroupSensor(entry.runtime_data.coordinator, entry, 1)
        assert entity.unique_id == "entry1_diagral_FAKESERIAL_group_1_status"
        assert entity.translation_key == "group_status"
        assert entity.translation_placeholders == {"group": "Ground floor"}
        assert entity.native_value == "disarmed"
        assert entity.extra_state_attributes["input_delay"] == 20
        assert entity.extra_state_attributes["output_delay"] == 90

    @pytest.mark.parametrize(
        ("value", "expected"),
        [("GROUP", "armed"), ("PRESENCE", "armed"), ("TEMPO_GROUP", "unknown")],
        ids=["armed", "presence", "tempo-without-target"],
    )
    def test_current_snapshot_is_read(self, value, expected):
        """A changed snapshot is read without another API call."""
        entry = make_entry()
        entity = DiagralGroupSensor(entry.runtime_data.coordinator, entry, 1)
        entry.runtime_data.coordinator.data["system_status"] = SimpleNamespace(
            status=value, activated_groups=[1]
        )
        assert entity.native_value == expected

    @pytest.mark.parametrize(
        "missing_group", [False, True], ids=["outage", "removed-group"]
    )
    def test_unavailable(self, missing_group):
        """A missing group or failed read never reports a healthy disarmed state."""
        entry = make_entry()
        entity = DiagralGroupSensor(entry.runtime_data.coordinator, entry, 1)
        if missing_group:
            entry.runtime_data.coordinator.data["alarm_config"].groups = []
        else:
            entry.runtime_data.coordinator.last_update_success = False
        assert entity.available is False

    async def test_new_groups_are_discovered_once(self):
        """An added group appears without duplicate entities on each poll."""
        entry = make_entry()
        add = MagicMock()
        await async_setup_group_sensors(MagicMock(), entry, add)
        assert len(add.call_args.args[0]) == 1
        refresh = entry.runtime_data.coordinator.async_add_listener.call_args.args[0]
        refresh()
        assert add.call_count == 1
        entry.runtime_data.coordinator.data["alarm_config"].groups.append(
            SimpleNamespace(index=2, name="Upstairs", input_delay=5, output_delay=30)
        )
        refresh()
        assert add.call_count == 2
        assert add.call_args.args[0][0].unique_id.endswith("_group_2_status")
        entry.async_on_unload.assert_called_once()

    async def test_duplicate_group_indices_do_not_create_duplicate_entities(self):
        """A duplicate cloud record must not register the same unique ID twice."""
        entry = make_entry()
        groups = entry.runtime_data.coordinator.data["alarm_config"].groups
        groups.append(groups[0])
        add = MagicMock()
        await async_setup_group_sensors(MagicMock(), entry, add)
        assert len(add.call_args.args[0]) == 1
