"""Read-only snapshots of Diagral equipment and their child-device entities."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from .const import (
    BRAND,
    EQUIPMENT_CATEGORIES,
    EQUIPMENT_FAULT_KEYS,
    EQUIPMENT_FIRMWARE_FIELDS,
)

if TYPE_CHECKING:
    from pydiagral.models import AlarmConfiguration


def _text(value: Any) -> str | None:
    """Keep model strings, never mock sentinels or arbitrary objects."""
    return value if isinstance(value, str) and value else None


def _index(value: Any) -> int | None:
    """Normalize a reported index without falling back to list position."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value >= 0:
        return value
    if isinstance(value, str) and value.isdecimal():
        return int(value)
    return None


def _boolean(value: Any) -> bool | None:
    """Unknown and missing flags must not be confused with a clear fault."""
    return value if isinstance(value, bool) else None


@dataclass(frozen=True, kw_only=True)
class Equipment:
    """An identifier-free snapshot keyed by category and reported index.

    `key` is stable across list reordering and label changes. `fault_flags`
    includes None for flags which the model does not report, while
    `active_faults` retains every true flag. No serial, UID or credential is
    copied from the source model; the central identity stays in entity metadata.
    """

    category: str
    index: int
    name: str
    group: int | None
    group_name: str | None
    reference: str | None
    product_type: int | str | None
    gamme: int | None
    subtype: int | None
    is_video: bool | None
    can_inhibit: bool | None
    inhibited: bool | None
    fault_items: tuple[tuple[str, bool | None], ...]
    firmware_items: tuple[tuple[str, str], ...]

    @property
    def key(self) -> str:
        """Return the category-scoped identity used to find later snapshots."""
        return f"{self.category}:{self.index}"

    @property
    def fault_flags(self) -> dict[str, bool | None]:
        """Return flags without allowing callers to mutate the snapshot."""
        return dict(self.fault_items)

    @property
    def active_faults(self) -> tuple[str, ...]:
        """Return all known active faults, never just the last one."""
        return tuple(name for name, value in self.fault_items if value is True)

    @property
    def problem(self) -> bool | None:
        """Return unknown when the cloud reported no usable fault state."""
        if not any(value is not None for _, value in self.fault_items):
            return None
        return bool(self.active_faults)

    @property
    def firmware(self) -> str | None:
        """Return only firmware versions explicitly reported for this device."""
        return (
            "; ".join(f"{name}: {value}" for name, value in self.firmware_items) or None
        )

    @property
    def attributes(self) -> dict[str, Any]:
        """Describe the device without a serial, account or bearer identifier."""
        return {
            "equipment_type": self.category,
            "index": self.index,
            "group": self.group,
            "group_name": self.group_name,
            "reference": self.reference,
            "product_type": self.product_type,
            "gamme": self.gamme,
            "subtype": self.subtype,
            "is_video": self.is_video,
            "can_inhibit": self.can_inhibit,
            "inhibited": self.inhibited,
            "faults": list(self.active_faults),
            "fault_count": len(self.active_faults)
            if self.problem is not None
            else None,
            "reported_faults": {
                name: value for name, value in self.fault_items if value is not None
            },
            "firmwares": dict(self.firmware_items),
        }


def iter_equipment(
    config: AlarmConfiguration | None, *, only_key: str | None = None
) -> tuple[Equipment, ...]:
    """Return stable, allowlisted equipment snapshots from one configuration.

    An equipment is identified only by its category and reported index. Devices
    without that identity are omitted instead of being associated by list order.
    Camera declarations are retained even though they have no fault fields.
    `only_key` avoids rebuilding every device for each entity state property.
    """
    if config is None:
        return ()
    groups = getattr(config, "groups", None)
    group_names = (
        {
            index: _text(group.name)
            for group in groups
            if (index := _index(getattr(group, "index", None))) is not None
        }
        if isinstance(groups, (list, tuple))
        else {}
    )
    result: dict[str, Equipment] = {}
    for category in EQUIPMENT_CATEGORIES:
        if only_key is not None and not only_key.startswith(f"{category}:"):
            continue
        devices = getattr(config, category, None)
        if not isinstance(devices, (list, tuple)):
            continue
        for device in devices:
            index = _index(getattr(device, "index", None))
            if index is None:
                continue
            if only_key is not None and only_key != f"{category}:{index}":
                continue
            reference = _text(getattr(device, "ref_code", None))
            name = _text(getattr(device, "label", None)) or _text(
                getattr(device, "name", None)
            )
            group = _index(getattr(device, "group", None))
            anomalies = getattr(device, "anomalies", None)
            firmware = getattr(device, "firmwares", None)
            product_type = getattr(device, "type", None)
            if isinstance(product_type, bool) or not isinstance(
                product_type, (str, int)
            ):
                product_type = None
            item = Equipment(
                category=category,
                index=index,
                name=name or f"{BRAND} {reference or category} {index}",
                group=group,
                group_name=group_names.get(group),
                reference=reference,
                product_type=product_type,
                gamme=_index(getattr(device, "gamme", None)),
                subtype=_index(getattr(device, "subtype", None)),
                is_video=_boolean(getattr(device, "is_video", None)),
                can_inhibit=_boolean(getattr(device, "can_inhibit", None)),
                inhibited=_boolean(getattr(device, "inhibited", None)),
                fault_items=tuple(
                    (name, _boolean(getattr(anomalies, name, None)))
                    for name in EQUIPMENT_FAULT_KEYS
                    if hasattr(anomalies, name)
                ),
                firmware_items=tuple(
                    (name, value)
                    for name in EQUIPMENT_FIRMWARE_FIELDS
                    if (value := _text(getattr(firmware, name, None))) is not None
                ),
            )
            result.setdefault(item.key, item)
    return tuple(result.values())
