"""Shared entity base."""
from __future__ import annotations

from homeassistant.core import callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import DeviceInfo, Entity

from .const import DOMAIN, SIGNAL_UPDATE
from .coordinator import IrrigationCoordinator


class IrrigationBaseEntity(Entity):
    _attr_should_poll = False
    _attr_has_entity_name = True

    def __init__(self, coordinator: IrrigationCoordinator, key: str, name: str) -> None:
        self.coordinator = coordinator
        self._attr_unique_id = f"{coordinator.entry_id}_{key}"
        self._attr_name = name
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.entry_id)},
            name="Garden Irrigation",
            manufacturer="Garden Irrigation",
            model="Queue Controller",
        )

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                f"{SIGNAL_UPDATE}_{self.coordinator.entry_id}",
                self._handle_update,
            )
        )

    @callback
    def _handle_update(self) -> None:
        self.async_write_ha_state()
