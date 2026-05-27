"""Buttons: Water All, Backwash, per-zone toggle."""
from __future__ import annotations

from homeassistant.components.button import ButtonEntity
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
    entities: list[ButtonEntity] = [
        WaterAllButton(coordinator),
        BackwashButton(coordinator),
    ]
    for zone in coordinator.zones.values():
        entities.append(ZoneToggleButton(coordinator, zone.id))
    async_add_entities(entities)


class WaterAllButton(IrrigationBaseEntity, ButtonEntity):
    _attr_icon = "mdi:sprinkler-variant"

    def __init__(self, coordinator: IrrigationCoordinator) -> None:
        super().__init__(coordinator, "water_all", "Water all")

    async def async_press(self) -> None:
        await self.coordinator.async_water_all(from_timer=False)

    @property
    def extra_state_attributes(self) -> dict:
        rt = self.coordinator.rt
        return {
            "running": rt.state != STATE_IDLE,
            "queue_length": len(rt.queue),
        }


class BackwashButton(IrrigationBaseEntity, ButtonEntity):
    _attr_icon = "mdi:backup-restore"

    def __init__(self, coordinator: IrrigationCoordinator) -> None:
        super().__init__(coordinator, "backwash", "Backwash")

    async def async_press(self) -> None:
        await self.coordinator.async_backwash_now()

    @property
    def available(self) -> bool:
        return self.coordinator.backwash_switch is not None


class ZoneToggleButton(IrrigationBaseEntity, ButtonEntity):
    _attr_icon = "mdi:water"

    def __init__(self, coordinator: IrrigationCoordinator, zone_id: str) -> None:
        zone = coordinator.zones[zone_id]
        # The slug used by HA to build entity_id comes from `_attr_name`, so
        # using the run order produces button.garden_irrigation_zone_<order>.
        # unique_id stays uuid-based so the entity survives renames/reorders.
        super().__init__(coordinator, f"zone_{zone_id}", f"Zone {zone.order}")
        self._zone_id = zone_id

    @property
    def name(self) -> str:
        zone = self.coordinator.zones.get(self._zone_id)
        return f"Zone {zone.order}" if zone else self._attr_name

    async def async_press(self) -> None:
        await self.coordinator.async_toggle_zone(self._zone_id)

    @property
    def extra_state_attributes(self) -> dict:
        rt = self.coordinator.rt
        zone = self.coordinator.zones.get(self._zone_id)
        if not zone:
            return {}
        position = self.coordinator.zone_position_in_queue(self._zone_id)
        is_active = self.coordinator.active_zone_id() == self._zone_id
        status = "idle"
        if is_active:
            status = "running"
        elif position is not None:
            status = "queued"
        return {
            "zone_name": zone.name,
            "default_duration_seconds": zone.duration,
            "run_order": zone.order,
            "queue_position": position,
            "is_active": is_active,
            "status": status,
            "remaining_seconds": rt.active_remaining if is_active else None,
        }
