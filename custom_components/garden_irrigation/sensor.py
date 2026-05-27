"""Status and queue sensors."""
from __future__ import annotations

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, STATE_IDLE
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
        ]
    )


def _fmt_mmss(seconds: int) -> str:
    seconds = max(0, int(seconds))
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


class StatusSensor(IrrigationBaseEntity, SensorEntity):
    _attr_icon = "mdi:state-machine"

    def __init__(self, coordinator: IrrigationCoordinator) -> None:
        super().__init__(coordinator, "status", "Status")

    @property
    def native_value(self) -> str:
        return self.coordinator.rt.state

    @property
    def extra_state_attributes(self) -> dict:
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
            "active_remaining_seconds": rt.active_remaining,
            "active_remaining_mmss": _fmt_mmss(rt.active_remaining),
            "phase_remaining_seconds": rt.phase_remaining,
            "phase_remaining_mmss": _fmt_mmss(rt.phase_remaining),
            "accumulated_watering_seconds": rt.accumulated_watering,
            "since_last_backwash_seconds": rt.since_last_backwash,
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

    @property
    def extra_state_attributes(self) -> dict:
        rt = self.coordinator.rt
        return {
            "remaining_seconds": rt.active_remaining,
            "remaining_mmss": _fmt_mmss(rt.active_remaining),
        }


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
