"""Build complete fault lists for local entities without copying identifiers."""

from __future__ import annotations

import re
from typing import Any

from .const import EQUIPMENT_CATEGORIES
from .equipment import iter_equipment

FAULT_LABELS = {
    "radio_alert": ("Radio fault", "Défaut radio"),
    "power_supply_alert": ("Power supply fault", "Défaut d'alimentation"),
    "autoprotection_mechanical_alert": ("Tamper", "Autoprotection ouverte"),
    "loop_alert": ("Loop fault", "Défaut de boucle"),
    "mask_alert": ("Masking detected", "Masquage détecté"),
    "sensor_alert": ("Detector fault", "Défaut du détecteur"),
    "media_gsm_alert": ("GSM transmission fault", "Défaut de transmission GSM"),
    "media_rtc_alert": ("Telephone transmission fault", "Défaut de transmission RTC"),
    "media_adsl_alert": (
        "Internet transmission fault",
        "Défaut de transmission Internet",
    ),
    "out_of_order_alert": ("Out of service", "Hors service"),
    "main_power_supply_alert": ("Mains supply fault", "Défaut d'alimentation secteur"),
    "secondary_power_supply_alert": (
        "Backup supply fault",
        "Défaut d'alimentation de secours",
    ),
    "default_media_alert": ("Transmission fault", "Défaut de transmission"),
    "autoprotection_wired_alert": ("Wired tamper", "Autoprotection filaire ouverte"),
}
FAULT_ALIASES = {
    "lowbattery": "power_supply_alert",
    "batteryfault": "power_supply_alert",
    "tamper": "autoprotection_mechanical_alert",
    "mechanicaltamper": "autoprotection_mechanical_alert",
    **{re.sub(r"[^a-z0-9]", "", key): key for key in FAULT_LABELS},
}
CATEGORY_LABELS = {
    "sensors": ("Detector", "Détecteur"),
    "sirens": ("Siren", "Sirène"),
    "cameras": ("Camera", "Caméra"),
    "commands": ("Control", "Commande"),
    "transceivers": ("Repeater", "Relais"),
    "transmitters": ("Transmitter", "Transmetteur"),
    "badges": ("Badge", "Badge"),
    "central": ("Central", "Centrale"),
}


def _label(value: Any) -> str | None:
    """Accept a short text label rather than arbitrary model values."""
    return value[:160] if isinstance(value, str) and value.strip() else None


def _index(value: Any) -> int | None:
    """Retain only real numeric device and group indexes."""
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _fault_key(name: str) -> str | None:
    """Match only known protocol names when merging the two fault sources."""
    return FAULT_ALIASES.get(re.sub(r"[^a-z0-9]", "", name.lower()))


def api_faults_for(
    anomalies: Any, category: str, index: int
) -> tuple[dict[str, Any], ...]:
    """Expose only the faults of the requested device, without any identifiers."""
    items = getattr(anomalies, category, None)
    if not isinstance(items, list):
        return ()
    result = []
    seen = set()
    for item in items:
        if _index(getattr(item, "index", None)) != index:
            continue
        faults = getattr(item, "anomaly_names", None) or []
        for fault in faults:
            name = _label(fault.name) or "Unspecified fault"
            identity = (fault.id, name)
            if identity in seen:
                continue
            seen.add(identity)
            result.append({"code": fault.id, "name": name, "field": _fault_key(name)})
        if not faults and ("unspecified", "Unspecified fault") not in seen:
            seen.add(("unspecified", "Unspecified fault"))
            result.append(
                {"code": "unspecified", "name": "Unspecified fault", "field": None}
            )
    return tuple(result)


def build_faults(
    anomalies: Any, config: Any, *, devices: Any = None, language: str = "en"
) -> list[dict[str, Any]]:
    """Group every reported fault by its family and index, never by its name."""
    locale = int(language.startswith("fr"))
    equipment = {(item.category, item.index): item for item in iter_equipment(config)}
    group_names = {
        group.index: group.name
        for group in (getattr(config, "groups", None) or [])
        if isinstance(getattr(group, "index", None), int)
        and _label(getattr(group, "name", None))
    }
    names = {
        (category, device.index): device.label
        for category in EQUIPMENT_CATEGORIES
        for device in (getattr(devices, category, None) or [])
        if _index(getattr(device, "index", None)) is not None
        and _label(getattr(device, "label", None))
    }
    grouped: dict[tuple, dict[str, Any]] = {}
    seen: dict[tuple, set[str]] = {}
    seen_api: dict[tuple, set[tuple]] = {}

    def target(
        category: str,
        index: int | None,
        group: int | None,
        label: str | None,
        ordinal: int = 0,
    ) -> tuple[tuple, dict]:
        if category == "central":
            index, ordinal = None, 0
        key = (category, index, ordinal if index is None else 0)
        if key not in grouped:
            device = equipment.get((category, index))
            name = device.name if device else names.get((category, index)) or label
            fallback = CATEGORY_LABELS.get(category, ("Device", "Équipement"))[locale]
            device_group = device.group if device else group
            grouped[key] = {
                "device_type": category,
                "device_index": index,
                "device_name": name
                or (f"{fallback} {index}" if index is not None else fallback),
                "group_index": device_group,
                "group_name": group_names.get(device_group),
                "faults": [],
            }
            seen[key] = set()
            seen_api[key] = set()
        return key, grouped[key]

    for category in (*EQUIPMENT_CATEGORIES, "badges", "central"):
        items = getattr(anomalies, category, None)
        if not isinstance(items, list):
            continue
        for ordinal, item in enumerate(items):
            key, entry = target(
                category,
                _index(item.index),
                _index(item.group),
                _label(item.label),
                ordinal,
            )
            faults = getattr(item, "anomaly_names", None) or []
            for fault in faults:
                raw_name = (
                    _label(fault.name)
                    or ("Unspecified fault", "Défaut non précisé")[locale]
                )
                canonical = _fault_key(raw_name)
                identity = (fault.id, raw_name)
                if identity in seen_api[key]:
                    continue
                seen_api[key].add(identity)
                if canonical:
                    seen[key].add(canonical)
                if re.sub(r"[^a-z0-9]", "", raw_name.lower()) == "lowbattery":
                    name = ("Low battery", "Pile faible")[locale]
                else:
                    name = FAULT_LABELS[canonical][locale] if canonical else raw_name
                entry["faults"].append(
                    {"code": fault.id, "name": name, "source": "anomalies"}
                )
            if not faults:
                seen[key].add("unspecified")
                entry["faults"].append(
                    {
                        "code": "unspecified",
                        "name": ("Unspecified fault", "Défaut non précisé")[locale],
                        "source": "anomalies",
                    }
                )

    for (category, index), device in equipment.items():
        for field in device.active_faults:
            key, entry = target(category, index, device.group, device.name)
            if field not in seen[key]:
                seen[key].add(field)
                entry["faults"].append(
                    {
                        "code": field,
                        "name": FAULT_LABELS[field][locale],
                        "source": "configuration",
                    }
                )

    central = getattr(config, "central_information", None)
    central_faults = getattr(central, "anomalies", None)
    for field, labels in FAULT_LABELS.items():
        if getattr(central_faults, field, None) is True:
            key, entry = target("central", None, None, None)
            if field not in seen[key]:
                seen[key].add(field)
                entry["faults"].append(
                    {"code": field, "name": labels[locale], "source": "configuration"}
                )
    return [entry for entry in grouped.values() if entry["faults"]]


def fault_summary(details: list[dict[str, Any]], language: str = "en") -> str:
    """Keep the state short while complete details remain in attributes."""
    french = language.startswith("fr")
    if not details:
        return "Aucun défaut" if french else "No faults"
    count = sum(len(item["faults"]) for item in details)
    if french:
        prefix = f"{count} défaut{'s' if count != 1 else ''} sur {len(details)} équipement{'s' if len(details) != 1 else ''}"
    else:
        prefix = f"{count} fault{'s' if count != 1 else ''} on {len(details)} device{'s' if len(details) != 1 else ''}"
    descriptions = "; ".join(
        f"{item['device_name']} : {', '.join(fault['name'] for fault in item['faults'])}"
        for item in details
    )
    return f"{prefix} — {descriptions}"[:255]
