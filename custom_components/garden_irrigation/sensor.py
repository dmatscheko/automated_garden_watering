"""Status and queue sensors."""
from __future__ import annotations

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    BACKWASH_ACTIVE_STATES,
    DOMAIN,
    STATE_IDLE,
)
from .coordinator import IrrigationCoordinator
from .entity import IrrigationBaseEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: IrrigationCoordinator = hass.data[DOMAIN][entry.entry_id]
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


def _hms_attrs(seconds: int) -> dict:
    seconds = max(0, int(seconds))
    return {
        "total_seconds": seconds,
        "hours": seconds // 3600,
        "minutes": (seconds % 3600) // 60,
        "seconds": seconds % 60,
        "formatted": _fmt_hms(seconds),
    }


class StatusSensor(IrrigationBaseEntity, SensorEntity):
    _attr_icon = "mdi:state-machine"

    def __init__(self, coordinator: IrrigationCoordinator) -> None:
        super().__init__(coordinator, "status", "Status")

    @property
    def native_value(self) -> str:
        return self.coordinator.rt.state

    @property
    def extra_state_attributes(self) -> dict:
        # Only slow-changing attributes here. The per-second countdown values
        # live on the dedicated "*_time_remaining" sensors so this entity's
        # state/attributes stay stable between transitions and don't flood the
        # recorder with a new row every second.
        rt = self.coordinator.rt
        active_id = self.coordinator.active_zone_id()
        active = self.coordinator.zones.get(active_id) if active_id else None
        queue_names = [
            self.coordinator.zones[zid].name
            for zid in rt.queue
            if zid in self.coordinator.zones
        ]
        return {
            "state": rt.state,
            "queue": queue_names,
            "queue_length": len(rt.queue),
            "active_zone": active.name if active else None,
            "multiplier": self.coordinator.multiplier,
            "started_by_timer": rt.started_by_timer,
        }


class ActiveZoneSensor(IrrigationBaseEntity, SensorEntity):
    _attr_icon = "mdi:water-pump"

    def __init__(self, coordinator: IrrigationCoordinator) -> None:
        super().__init__(coordinator, "active_zone", "Active zone")

    @property
    def native_value(self) -> str:
        active_id = self.coordinator.active_zone_id()
        if active_id and active_id in self.coordinator.zones:
            return self.coordinator.zones[active_id].name
        return "none"

    # No per-second attributes: state is the zone name, which only changes when
    # the active zone changes. Remaining time is on the step-remaining sensor.


class QueueSensor(IrrigationBaseEntity, SensorEntity):
    _attr_icon = "mdi:format-list-numbered"

    def __init__(self, coordinator: IrrigationCoordinator) -> None:
        super().__init__(coordinator, "queue", "Queue")

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
    def extra_state_attributes(self) -> dict:
        rt = self.coordinator.rt
        return {
            "length": len(rt.queue),
            "ids": list(rt.queue),
            "idle": rt.state == STATE_IDLE,
        }


class CurrentStepRemainingSensor(IrrigationBaseEntity, SensorEntity):
    """Time left in the active zone, or in the backwash while it runs."""

    _attr_icon = "mdi:timer-sand"

    def __init__(self, coordinator: IrrigationCoordinator) -> None:
        super().__init__(coordinator, "step_remaining", "Current step time remaining")

    @property
    def native_value(self) -> str:
        return _fmt_hms(self.coordinator.current_step_remaining_seconds())

    @property
    def extra_state_attributes(self) -> dict:
        rt = self.coordinator.rt
        attrs = _hms_attrs(self.coordinator.current_step_remaining_seconds())
        active_id = self.coordinator.active_zone_id()
        active = self.coordinator.zones.get(active_id) if active_id else None
        attrs["phase"] = rt.state
        attrs["label"] = active.name if active else (
            "Backwash" if rt.state in BACKWASH_ACTIVE_STATES else "—"
        )
        return attrs


class QueueRemainingSensor(IrrigationBaseEntity, SensorEntity):
    """Total watering time left for the whole queue (pauses during backwash)."""

    _attr_icon = "mdi:timer-outline"

    def __init__(self, coordinator: IrrigationCoordinator) -> None:
        super().__init__(coordinator, "queue_remaining", "Queue time remaining")

    @property
    def native_value(self) -> str:
        return _fmt_hms(self.coordinator.queue_remaining_seconds())

    @property
    def extra_state_attributes(self) -> dict:
        attrs = _hms_attrs(self.coordinator.queue_remaining_seconds())
        attrs["queue_length"] = len(self.coordinator.rt.queue)
        attrs["paused_for_backwash"] = (
            self.coordinator.rt.state in BACKWASH_ACTIVE_STATES
        )
        return attrs
