"""Global watering multiplier."""
from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
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
    async_add_entities([MultiplierNumber(coordinator)])


class MultiplierNumber(IrrigationBaseEntity, NumberEntity):
    _attr_icon = "mdi:multiplication"
    _attr_native_min_value = 0.0
    _attr_native_max_value = 10.0
    _attr_native_step = 0.05
    _attr_mode = NumberMode.BOX

    def __init__(self, coordinator: IrrigationCoordinator) -> None:
        super().__init__(coordinator, "multiplier", "Watering multiplier")

    @property
    def native_value(self) -> float:
        return self.coordinator.multiplier

    async def async_set_native_value(self, value: float) -> None:
        await self.coordinator.async_set_multiplier(value)
