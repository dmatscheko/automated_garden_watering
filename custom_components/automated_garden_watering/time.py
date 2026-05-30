"""Daily start time entity."""
from __future__ import annotations

from datetime import time as dt_time

from homeassistant.components.time import TimeEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import AutomatedGardenWateringConfigEntry
from .coordinator import IrrigationCoordinator
from .entity import IrrigationBaseEntity

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: AutomatedGardenWateringConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    async_add_entities([DailyStartTime(entry.runtime_data)])


class DailyStartTime(IrrigationBaseEntity, TimeEntity):
    _attr_translation_key = "daily_start"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: IrrigationCoordinator) -> None:
        super().__init__(coordinator, "daily_start")

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
