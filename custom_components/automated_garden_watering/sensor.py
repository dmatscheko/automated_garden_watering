"""Status and queue sensors."""
from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import AutomatedGardenWateringConfigEntry
from .const import (
    BACKWASH_ACTIVE_STATES,
    STATE_BACKWASH,
    STATE_BACKWASH_FLUSH,
    STATE_BACKWASH_PRESSURE,
    STATE_IDLE,
    STATE_PUMP_PRESSURE,
    STATE_WATERING,
)
from .coordinator import IrrigationCoordinator
from .entity import IrrigationBaseEntity

# Coordinator pushes via dispatcher; no parallel polling required.
PARALLEL_UPDATES = 0

STATUS_OPTIONS = [
    STATE_IDLE,
    STATE_PUMP_PRESSURE,
    STATE_WATERING,
    STATE_BACKWASH_PRESSURE,
    STATE_BACKWASH,
    STATE_BACKWASH_FLUSH,
]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: AutomatedGardenWateringConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(
        [
            StatusSensor(coordinator),
            ActiveZoneSensor(coordinator),
            QueueSensor(coordinator),
            CurrentStepRemainingSensor(coordinator),
            QueueRemainingSensor(coordinator),
        ]
    )


def _fmt_hms(seconds: int) -> str:
    seconds = max(0, int(seconds))
    return f"{seconds // 3600:d}:{(seconds % 3600) // 60:02d}:{seconds % 60:02d}"


def _hms_attrs(seconds: int) -> dict[str, Any]:
    seconds = max(0, int(seconds))
    return {
        "total_seconds": seconds,
        "hours": seconds // 3600,
        "minutes": (seconds % 3600) // 60,
        "seconds": seconds % 60,
        "formatted": _fmt_hms(seconds),
    }


class StatusSensor(IrrigationBaseEntity, SensorEntity):
    _attr_translation_key = "status"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = STATUS_OPTIONS

    def __init__(self, coordinator: IrrigationCoordinator) -> None:
        super().__init__(coordinator, "status")

    @property
    def native_value(self) -> str:
        return self.coordinator.rt.state

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        # Only slow-changing attributes here. The per-second countdown values
        # live on the dedicated "*_time_remaining" sensors so this entity's
        # state/attributes stay stable between transitions and don't flood the
        # recorder with a new row every second.
        rt = self.coordinator.rt
        active_ids = self.coordinator.active_zone_ids()
        active_names = [
            self.coordinator.zones[zid].name
            for zid in active_ids
            if zid in self.coordinator.zones
        ]
        queue_names = [
            self.coordinator.zones[zid].name
            for zid in rt.queue
            if zid in self.coordinator.zones
        ]
        return {
            "queue": queue_names,
            "queue_length": len(rt.queue),
            "active_zone": active_names[0] if len(active_names) == 1 else None,
            "active_zones": active_names,
            "active_count": len(active_names),
            "multiplier": self.coordinator.multiplier,
            "started_by_timer": rt.started_by_timer,
        }


class ActiveZoneSensor(IrrigationBaseEntity, SensorEntity):
    _attr_translation_key = "active_zone"

    def __init__(self, coordinator: IrrigationCoordinator) -> None:
        super().__init__(coordinator, "active_zone")

    @property
    def native_value(self) -> str:
        active_ids = self.coordinator.active_zone_ids()
        if not active_ids:
            return "none"
        names = [
            self.coordinator.zones[zid].name
            for zid in active_ids
            if zid in self.coordinator.zones
        ]
        if not names:
            return "none"
        return ", ".join(names)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        active_ids = self.coordinator.active_zone_ids()
        return {
            "ids": active_ids,
            "names": [
                self.coordinator.zones[zid].name
                for zid in active_ids
                if zid in self.coordinator.zones
            ],
            "count": len(active_ids),
            "group_cap": (
                min(
                    self.coordinator.zones[zid].max_parallel
                    for zid in active_ids
                    if zid in self.coordinator.zones
                )
                if active_ids
                else None
            ),
        }


class QueueSensor(IrrigationBaseEntity, SensorEntity):
    _attr_translation_key = "queue"

    def __init__(self, coordinator: IrrigationCoordinator) -> None:
        super().__init__(coordinator, "queue")

    @property
    def native_value(self) -> str:
        rt = self.coordinator.rt
        names = [
            self.coordinator.zones[zid].name
            for zid in rt.queue
            if zid in self.coordinator.zones
        ]
        if not names:
            return "empty"
        return ", ".join(names)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        rt = self.coordinator.rt
        return {
            "length": len(rt.queue),
            "ids": list(rt.queue),
            "idle": rt.state == STATE_IDLE,
        }


class CurrentStepRemainingSensor(IrrigationBaseEntity, SensorEntity):
    """Time left in the active zone, or in the backwash while it runs."""

    _attr_translation_key = "step_remaining"

    def __init__(self, coordinator: IrrigationCoordinator) -> None:
        super().__init__(coordinator, "step_remaining")

    @property
    def native_value(self) -> str:
        return _fmt_hms(self.coordinator.current_step_remaining_seconds())

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        rt = self.coordinator.rt
        attrs = _hms_attrs(self.coordinator.current_step_remaining_seconds())
        active_ids = self.coordinator.active_zone_ids()
        names = [
            self.coordinator.zones[zid].name
            for zid in active_ids
            if zid in self.coordinator.zones
        ]
        attrs["phase"] = rt.state
        attrs["label"] = (
            ", ".join(names)
            if names
            else ("Backwash" if rt.state in BACKWASH_ACTIVE_STATES else "—")
        )
        return attrs


class QueueRemainingSensor(IrrigationBaseEntity, SensorEntity):
    """Total watering time left for the whole queue (pauses during backwash)."""

    _attr_translation_key = "queue_remaining"

    def __init__(self, coordinator: IrrigationCoordinator) -> None:
        super().__init__(coordinator, "queue_remaining")

    @property
    def native_value(self) -> str:
        return _fmt_hms(self.coordinator.queue_remaining_seconds())

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        attrs = _hms_attrs(self.coordinator.queue_remaining_seconds())
        attrs["queue_length"] = len(self.coordinator.rt.queue)
        attrs["paused_for_backwash"] = (
            self.coordinator.rt.state in BACKWASH_ACTIVE_STATES
        )
        return attrs
