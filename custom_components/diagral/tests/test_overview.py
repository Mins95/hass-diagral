"""Test the additional local sensors against synthetic coordinator snapshots."""

from datetime import UTC, datetime
from types import SimpleNamespace

from custom_components.diagral.arming import ArmingTracker
from custom_components.diagral.connection import ClockDiagnostics
from custom_components.diagral.overview import OVERVIEW_SENSORS, DiagralOverviewSensor


def sensor(key: str, *, available: bool = True) -> DiagralOverviewSensor:
    """Build an entity without starting HA or contacting the cloud."""
    item = object.__new__(DiagralOverviewSensor)
    item.entity_description = next(
        description for description in OVERVIEW_SENSORS if description.key == key
    )
    item.coordinator = SimpleNamespace(
        data={
            "alarm_config": SimpleNamespace(
                groups=[
                    SimpleNamespace(
                        index=1, name="One", input_delay=17, output_delay=63
                    ),
                    SimpleNamespace(
                        index=2, name="Two", input_delay=25, output_delay=120
                    ),
                ],
                sensors=[],
                sirens=[],
                cameras=[],
                commands=[],
                transmitters=[],
                transceivers=[],
                central_information=None,
            ),
            "anomalies": {},
        },
        arming=ArmingTracker(),
        last_update_success=available,
        last_success=datetime(2026, 10, 26, 10, tzinfo=UTC),
        last_error=None,
        last_error_status=None,
        cloud_reachable=True,
        webhook_status="connected",
    )
    item.hass = SimpleNamespace(config=SimpleNamespace(language="fr"))
    return item


class TestOverviewSensors:
    """Configured delays and readable summaries are independent of hardcoded90s."""

    def test_redundant_global_sensors_are_not_created(self):
        """Firmware belongs to device metadata; timing stays in diagnostics."""
        keys = {description.key for description in OVERVIEW_SENSORS}

        assert keys.isdisjoint({"clock_offset", "firmware"})

    def test_communication_keeps_passive_clock_diagnostics(self):
        """Removing the dedicated clock entity must not hide signing failures."""
        item = sensor("communication_state")
        clock = ClockDiagnostics()
        clock.offset_seconds = 3600
        item.coordinator.clock_diagnostics = clock

        assert item.native_value == "clock_skew"
        assert item.extra_state_attributes["clock"] == clock.as_dict()

    def test_exit_delay_comes_from_the_actual_groups(self):
        """A different installation can have a120-second delay."""
        item = sensor("exit_delay")
        assert item.native_value == 120
        assert item.extra_state_attributes["groups"][0]["output_delay"] == 63

    def test_selected_group_uses_its_own_delay(self):
        """The arming target determines which configured delay to show."""
        item = sensor("exit_delay")
        item.coordinator.arming.target_groups = (1,)
        assert item.native_value == 63

    def test_missing_delay_does_not_become_zero_or90(self):
        """Unknown device data must remain unknown."""
        item = sensor("exit_delay")
        item.coordinator.data["alarm_config"].groups[0].output_delay = None
        assert item.native_value is None

    def test_disarmed_alarm_has_no_arming_deadline(self):
        """An inactive timer is unknown, not a new countdown."""
        assert sensor("arming_ends_at").native_value is None

    def test_summary_is_in_french(self):
        """A clear successful snapshot has readable summary text."""
        assert sensor("anomaly_summary").native_value == "Aucun défaut"

    def test_unavailable_fault_data_is_not_described_as_clear(self):
        """The entity becomes unavailable if the cloud read failed."""
        assert not sensor("anomaly_summary", available=False).available

    def test_last_success_stays_readable_during_an_outage(self):
        """Diagnostics retain the time of the last working read."""
        item = sensor("last_success", available=False)
        assert item.available
        assert item.native_value == item.coordinator.last_success
