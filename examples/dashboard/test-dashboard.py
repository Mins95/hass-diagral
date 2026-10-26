"""Offline validation of the generated Lovelace dictionary."""

import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location("dashboard_builder", Path(__file__).with_name("build-dashboard.py"))
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


class DashboardBuilderTests(unittest.TestCase):
    """Keep existing entity IDs and keep commands in the native card."""

    def setUp(self):
        self.mapping = {
            "alarm_entity": "alarm_control_panel.existing_panel",
            "anomalies_entity": "sensor.existing_anomalies",
            "arming_end_entity": "sensor.new_arming_ends_at",
            "group_entities": ["sensor.new_group_1"],
            "equipment_entities": [{"entity": "sensor.new_camera_information", "name": "Camera"}],
        }

    def test_existing_ids_are_used_verbatim(self):
        dashboard = builder.build_dashboard(self.mapping)
        sections = dashboard["views"][0]["sections"]
        native = sections[0]["cards"][1]
        custom = sections[1]["cards"][0]
        self.assertEqual(native["type"], "alarm-panel")
        self.assertEqual(native["entity"], self.mapping["alarm_entity"])
        self.assertEqual(custom["alarm_entity"], self.mapping["alarm_entity"])
        self.assertEqual(custom["anomalies_entity"], self.mapping["anomalies_entity"])
        self.assertNotIn("tap_action", custom)

    def test_optional_timestamp_can_be_omitted(self):
        self.mapping.pop("arming_end_entity")
        dashboard = builder.build_dashboard(self.mapping)
        custom = dashboard["views"][0]["sections"][1]["cards"][0]
        self.assertNotIn("arming_end_entity", custom)

    def test_invalid_entity_urls_are_rejected(self):
        self.mapping["alarm_entity"] = "https://example.com"
        with self.assertRaisesRegex(ValueError, "alarm_entity"):
            builder.build_dashboard(self.mapping)

    def test_arbitrary_custom_actions_are_not_carried_over(self):
        self.mapping["tap_action"] = {"action": "call-service", "service": "alarm_control_panel.alarm_arm_away"}
        custom = builder.build_dashboard(self.mapping)["views"][0]["sections"][1]["cards"][0]
        self.assertNotIn("tap_action", custom)

    def test_invalid_list_entries_are_rejected(self):
        self.mapping["equipment_entities"] = [None]
        with self.assertRaisesRegex(ValueError, "equipment_entities"):
            builder.build_dashboard(self.mapping)

    def test_stale_threshold_is_configurable(self):
        self.mapping["stale_after_seconds"] = 120
        custom = builder.build_dashboard(self.mapping)["views"][0]["sections"][1]["cards"][0]
        self.assertEqual(custom["stale_after_seconds"], 120)

    def test_nonpositive_stale_threshold_is_rejected(self):
        self.mapping["stale_after_seconds"] = 0
        with self.assertRaisesRegex(ValueError, "stale_after_seconds"):
            builder.build_dashboard(self.mapping)


if __name__ == "__main__":
    unittest.main()
