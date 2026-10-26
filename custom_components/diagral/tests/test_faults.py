"""Test complete, readable fault summaries without changing cloud snapshots."""

from copy import deepcopy
from types import SimpleNamespace

from custom_components.diagral.faults import build_faults, fault_summary
from pydiagral.models import (
    Anomalies,
    AnomalyDetail,
    AnomalyName,
    DeviceInfos,
    DeviceList,
)
import pytest


def configuration() -> SimpleNamespace:
    """Return several device families that reuse an index."""
    return SimpleNamespace(
        groups=[SimpleNamespace(index=1, name="Downstairs")],
        sensors=[SimpleNamespace(index=1, label="Front door", group=1, anomalies=None)],
        sirens=[
            SimpleNamespace(index=1, label="Outside siren", group=None, anomalies=None)
        ],
        cameras=[],
        commands=[],
        transmitters=[],
        transceivers=[],
        central_information=None,
    )


class TestFaultDetails:
    """All faults and their device identity survive the presentation layer."""

    def test_two_faults_on_one_device_are_retained(self):
        """Never flatten two faults into a single last name."""
        anomalies = Anomalies(
            created_at=None,
            sensors=[
                AnomalyDetail(
                    index=1,
                    group=1,
                    anomaly_names=[
                        AnomalyName(id=1384, name="lowBattery"),
                        AnomalyName(id=1374, name="Tamper"),
                    ],
                )
            ],
        )
        original = deepcopy(anomalies)
        result = build_faults(anomalies, configuration(), language="fr")
        assert len(result) == 1
        assert result[0]["device_name"] == "Front door"
        assert result[0]["group_name"] == "Downstairs"
        assert [fault["code"] for fault in result[0]["faults"]] == [1384, 1374]
        assert result[0]["faults"][0]["name"] == "Pile faible"
        assert anomalies == original

    def test_device_number_is_scoped_by_family(self):
        """Sensor 1 and siren 1 remain separate devices."""
        anomaly = AnomalyDetail(
            index=1, anomaly_names=[AnomalyName(id=1, name="Fault")]
        )
        result = build_faults(
            Anomalies(created_at=None, sensors=[anomaly], sirens=[anomaly]),
            configuration(),
        )
        assert {item["device_name"] for item in result} == {
            "Front door",
            "Outside siren",
        }

    def test_configuration_flags_are_visible_without_anomaly_list(self):
        """An active configuration fault is not hidden by an empty anomalies read."""
        config = configuration()
        config.sensors[0].anomalies = SimpleNamespace(
            radio_alert=True, power_supply_alert=False
        )
        result = build_faults({}, config, language="fr")
        assert len(result) == 1
        assert result[0]["faults"] == [
            {"code": "radio_alert", "name": "Défaut radio", "source": "configuration"}
        ]

    def test_config_and_anomaly_descriptions_are_not_duplicated(self):
        """A low battery reported in both reads is one fault."""
        config = configuration()
        config.sensors[0].anomalies = SimpleNamespace(power_supply_alert=True)
        anomaly = AnomalyDetail(
            index=1, anomaly_names=[AnomalyName(id=1384, name="lowBattery")]
        )
        result = build_faults(
            Anomalies(created_at=None, sensors=[anomaly]), config, language="fr"
        )
        assert len(result[0]["faults"]) == 1
        assert result[0]["faults"][0]["source"] == "anomalies"

    def test_distinct_api_codes_are_kept_even_when_they_share_a_family(self):
        """Dedupe a matching configuration flag, never two distinct API codes."""
        config = configuration()
        config.sensors[0].anomalies = SimpleNamespace(power_supply_alert=True)
        anomaly = AnomalyDetail(
            index=1,
            anomaly_names=[
                AnomalyName(id=1384, name="lowBattery"),
                AnomalyName(id=1400, name="batteryFault"),
            ],
        )
        result = build_faults(Anomalies(created_at=None, sensors=[anomaly]), config)
        assert [fault["code"] for fault in result[0]["faults"]] == [1384, 1400]

    def test_names_are_resolved_from_device_list_when_config_is_unavailable(self):
        """Do not replace a known device name with an unrelated family."""
        devices = DeviceList(sensors=[DeviceInfos(index=1, label="Front door")])
        anomaly = AnomalyDetail(
            index=1, anomaly_names=[AnomalyName(id=1, name="Fault")]
        )
        result = build_faults(
            Anomalies(created_at=None, sensors=[anomaly]), None, devices=devices
        )
        assert result[0]["device_name"] == "Front door"

    @pytest.mark.parametrize("value", [None, {}], ids=["none", "empty"])
    def test_absent_faults_produce_an_empty_list(self, value):
        """Missing flags are not interpreted as active faults."""
        assert build_faults(value, configuration()) == []

    def test_serials_are_not_copied_to_presentation(self):
        """New display attributes contain no raw serial or UID."""
        anomaly = AnomalyDetail(
            index=1,
            serial="SECRET-SERIAL",
            anomaly_names=[AnomalyName(id=1, name="Fault")],
        )
        result = build_faults(
            Anomalies(created_at=None, sensors=[anomaly]), configuration()
        )
        assert "SECRET-SERIAL" not in str(result)

    def test_central_faults_belong_to_one_device_without_an_index(self):
        """Several fault records do not imply several central units."""
        anomalies = Anomalies(
            created_at=None,
            central=[
                AnomalyDetail(anomaly_names=[AnomalyName(id=1, name="Fault A")]),
                AnomalyDetail(anomaly_names=[AnomalyName(id=2, name="Fault B")]),
            ],
        )
        result = build_faults(anomalies, configuration())
        assert len(result) == 1
        assert len(result[0]["faults"]) == 2


class TestFaultSummary:
    """Summary text is readable and within HA's state length limit."""

    def test_no_faults_in_french(self):
        """The dashboard can distinguish a successful empty read."""
        assert fault_summary([], "fr") == "Aucun défaut"

    def test_multiple_faults_are_counted_separately_from_devices(self):
        """Two faults on one device are described accurately."""
        details = [
            {
                "device_name": "Front door",
                "faults": [{"name": "Battery"}, {"name": "Tamper"}],
            }
        ]
        summary = fault_summary(details, "fr")
        assert "2" in summary and "1" in summary
        assert "Front door" in summary

    def test_long_device_names_do_not_exceed_state_limit(self):
        """Keep the complete details in attributes, not in an overlong state."""
        assert (
            len(
                fault_summary(
                    [{"device_name": "X" * 400, "faults": [{"name": "Battery"}]}], "fr"
                )
            )
            <= 255
        )
