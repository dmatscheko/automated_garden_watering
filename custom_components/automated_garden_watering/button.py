"""Buttons: Water All, Backwash, per-zone toggle, plus config helpers."""
from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.components.persistent_notification import (
    async_create as async_create_notification,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import AutomatedGardenWateringConfigEntry
from .const import (
    BACKWASH_ACTIVE_STATES,
    DOMAIN,
    STATE_IDLE,
)
from .coordinator import IrrigationCoordinator
from .dashboard import build_dashboard
from .entity import IrrigationBaseEntity

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: AutomatedGardenWateringConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    entities: list[ButtonEntity] = [
        WaterAllButton(coordinator),
        BackwashButton(coordinator),
        GenerateDashboardButton(coordinator),
    ]
    for zone in coordinator.zones.values():
        entities.append(ZoneToggleButton(coordinator, zone.id))
    async_add_entities(entities)


class WaterAllButton(IrrigationBaseEntity, ButtonEntity):
    _attr_translation_key = "water_all"

    def __init__(self, coordinator: IrrigationCoordinator) -> None:
        super().__init__(coordinator, "water_all")

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
    _attr_translation_key = "backwash"

    def __init__(self, coordinator: IrrigationCoordinator) -> None:
        super().__init__(coordinator, "backwash")

    async def async_press(self) -> None:
        await self.coordinator.async_backwash_now()

    @property
    def available(self) -> bool:
        return self.coordinator.backwash_switch is not None

    @property
    def extra_state_attributes(self) -> dict:
        running = self.coordinator.rt.state in BACKWASH_ACTIVE_STATES
        last_run = self.coordinator.backwash_last_run
        return {
            "status": "running" if running else "idle",
            "is_active": running,
            "last_run": last_run.isoformat() if last_run else None,
            "last_run_friendly": self.coordinator.backwash_last_run_friendly(),
        }


class GenerateDashboardButton(IrrigationBaseEntity, ButtonEntity):
    """Generate a ready-to-paste Lovelace dashboard for this integration."""

    _attr_translation_key = "dashboard_yaml"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: IrrigationCoordinator) -> None:
        # The unique-id suffix stays `generate_dashboard` so existing entries
        # in the entity registry survive the rename to "Dashboard YAML".
        super().__init__(coordinator, "generate_dashboard")

    async def async_press(self) -> None:
        yaml_text = build_dashboard(self.hass, self.coordinator)
        message = (
            "Copy the YAML below into your dashboard's **Raw configuration "
            "editor** (under the existing `views:` key).\n\n"
            f"```yaml\n{yaml_text}\n```"
        )
        async_create_notification(
            self.hass,
            message,
            title="Automated Garden Watering dashboard",
            notification_id=f"{DOMAIN}_dashboard_{self.coordinator.entry_id}",
        )


class ZoneToggleButton(IrrigationBaseEntity, ButtonEntity):
    # User-supplied display name; rendered via the `name` property below.
    # No translation_key, so HA uses the dynamic property directly.
    _attr_icon = "mdi:water"

    def __init__(self, coordinator: IrrigationCoordinator, zone_id: str) -> None:
        zone = coordinator.zones[zone_id]
        # HA generates the natural button.<device>_<name> id; __init__.py then
        # renames it to button.<device>_zone_<order> so the buttons sort by run
        # order. unique_id stays uuid-based so the entity survives renames.
        super().__init__(coordinator, f"zone_{zone_id}", zone.name)
        self._zone_id = zone_id

    @property
    def name(self) -> str:
        zone = self.coordinator.zones.get(self._zone_id)
        return zone.name if zone else self._attr_name

    @property
    def available(self) -> bool:
        # If the underlying valve switch was deleted from HA, the zone button
        # should mark itself unavailable so users see something is wrong.
        zone = self.coordinator.zones.get(self._zone_id)
        if not zone:
            return False
        return self.hass.states.get(zone.entity_id) is not None

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
        last_run = self.coordinator.zone_last_run(self._zone_id)
        return {
            "zone_name": zone.name,
            "default_duration_seconds": zone.duration,
            "run_order": zone.order,
            "queue_position": position,
            "is_active": is_active,
            "status": status,
            "remaining_seconds": rt.active_remaining if is_active else None,
            "last_run": last_run.isoformat() if last_run else None,
            "last_run_friendly": self.coordinator.zone_last_run_friendly(self._zone_id),
        }
