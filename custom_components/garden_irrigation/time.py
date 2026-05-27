"""Daily start time entity."""
from __future__ import annotations

from datetime import time as dt_time

from homeassistant.components.time import TimeEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import IrrigationCoordinator
from .entity import IrrigationBaseEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: IrrigationCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([DailyStartTime(coordinator)])


class DailyStartTime(IrrigationBaseEntity, TimeEntity):
    _attr_icon = "mdi:clock-time-four-outline"

    def __init__(self, coordinator: IrrigationCoordinator) -> None:
        super().__init__(coordinator, "daily_start", "Daily start time")

    @property
    def native_value(self) -> dt_time | None:
        try:
            parts = [int(p) for p in self.coordinator.daily_start.split(":")]
            while len(parts) < 3:
                parts.append(0)
            return dt_time(parts[0], parts[1], parts[2])
        except (ValueError, IndexError):
            return None

    async def async_set_value(self, value: dt_time) -> None:
        await self.coordinator.async_set_daily_start(value)
