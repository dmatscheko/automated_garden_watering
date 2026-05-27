"""Daily timer enable switch."""
from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
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
    async_add_entities([DailyTimerSwitch(coordinator)])


class DailyTimerSwitch(IrrigationBaseEntity, SwitchEntity):
    _attr_icon = "mdi:timer-outline"

    def __init__(self, coordinator: IrrigationCoordinator) -> None:
        super().__init__(coordinator, "daily_timer", "Daily timer")

    @property
    def is_on(self) -> bool:
        return self.coordinator.daily_timer_enabled

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_daily_timer_enabled(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_daily_timer_enabled(False)
