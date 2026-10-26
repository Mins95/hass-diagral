"""Build a local dashboard from explicitly resolved Home Assistant entity IDs."""

import argparse
import json
import math
from pathlib import Path
import re

ENTITY_ID = re.compile(r"[a-z0-9_]+\.[a-z0-9_]+\Z")
LIST_KEYS = ("group_entities", "equipment_entities", "communication_entities")


def build_dashboard(mapping: dict) -> dict:
    """Return a Lovelace dashboard without creating or changing backend state."""
    for key in ("alarm_entity", "anomalies_entity"):
        if not isinstance(mapping.get(key), str) or not ENTITY_ID.fullmatch(mapping[key]):
            raise ValueError(f"{key} must be an explicit entity ID")
    optional_end = mapping.get("arming_end_entity")
    if optional_end is not None and (
        not isinstance(optional_end, str) or not ENTITY_ID.fullmatch(optional_end)
    ):
        raise ValueError("arming_end_entity must be an explicit entity ID")
    stale_after_seconds = mapping.get("stale_after_seconds", 600)
    if isinstance(stale_after_seconds, bool) or not isinstance(stale_after_seconds, (int, float)) or not math.isfinite(stale_after_seconds) or stale_after_seconds <= 0:
        raise ValueError("stale_after_seconds must be a positive finite number")
    overview = {
        "type": "custom:diagral-overview-card",
        "title": "État et diagnostics",
        "alarm_entity": mapping["alarm_entity"],
        "anomalies_entity": mapping["anomalies_entity"],
        "stale_after_seconds": stale_after_seconds,
    }
    if optional_end:
        overview["arming_end_entity"] = optional_end
    for key in LIST_KEYS:
        values = mapping.get(key, [])
        if not isinstance(values, list):
            raise ValueError(f"{key} must be a list")
        for value in values:
            entity_id = value if isinstance(value, str) else value.get("entity") if isinstance(value, dict) else None
            if not isinstance(entity_id, str) or not ENTITY_ID.fullmatch(entity_id):
                raise ValueError(f"{key} contains an invalid entity ID")
        overview[key] = values
    return {
        "title": "Diagral",
        "views": [{
            "title": "Diagral",
            "path": "overview",
            "icon": "mdi:shield-home-outline",
            "type": "sections",
            "max_columns": 2,
            "sections": [
                {"type": "grid", "cards": [
                    {"type": "heading", "heading": "Commandes de la centrale", "icon": "mdi:shield-home-outline"},
                    {"type": "alarm-panel", "entity": mapping["alarm_entity"], "name": "Centrale Diagral"},
                ]},
                {"type": "grid", "cards": [overview]},
            ],
        }],
    }


def main() -> None:
    """Write JSON, which is also valid YAML for the HA YAML dashboard loader."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mapping", type=Path)
    parser.add_argument("--output", type=Path, default=Path(__file__).with_name("diagral-dashboard.yaml"))
    args = parser.parse_args()
    mapping = json.loads(args.mapping.read_text(encoding="utf-8-sig"))
    result = build_dashboard(mapping)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"written": args.output.name, "groups": len(mapping.get("group_entities", [])), "equipment": len(mapping.get("equipment_entities", [])), "communication": len(mapping.get("communication_entities", []))}))


if __name__ == "__main__":
    main()
