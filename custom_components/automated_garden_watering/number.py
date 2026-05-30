"""Global watering multiplier."""
from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
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
    async_add_entities([MultiplierNumber(entry.runtime_data)])


class MultiplierNumber(IrrigationBaseEntity, NumberEntity):
    _attr_translation_key = "multiplier"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_native_min_value = 0.0
    _attr_native_max_value = 10.0
    _attr_native_step = 0.05
    _attr_mode = NumberMode.BOX

    def __init__(self, coordinator: IrrigationCoordinator) -> None:
        super().__init__(coordinator, "multiplier")

    @property
    def native_value(self) -> float:
        return self.coordinator.multiplier

    async def async_set_native_value(self, value: float) -> None:
        await self.coordinator.async_set_multiplier(value)
