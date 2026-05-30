"""Shared entity base."""
from __future__ import annotations

from homeassistant.core import callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity

from .const import DOMAIN, SIGNAL_UPDATE
from .coordinator import IrrigationCoordinator


class IrrigationBaseEntity(Entity):
    _attr_should_poll = False
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: IrrigationCoordinator,
        key: str,
        name: str | None = None,
    ) -> None:
        self.coordinator = coordinator
        self._attr_unique_id = f"{coordinator.entry_id}_{key}"
        # Subclasses that set _attr_translation_key get their display name from
        # translations/*.json; this fallback name is only used when neither
        # translation_key nor a dynamic `name` property is set on the subclass.
        if name is not None:
            self._attr_name = name
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.entry_id)},
            name="Automated Garden Watering",
            manufacturer="Automated Garden Watering",
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
