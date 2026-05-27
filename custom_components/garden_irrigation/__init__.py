"""Garden Irrigation integration."""
from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .const import CONF_ZONE_ID, CONF_ZONE_ORDER, CONF_ZONES, DOMAIN
from .coordinator import IrrigationCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.BUTTON,
    Platform.NUMBER,
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.TIME,
]


def _merged(entry: ConfigEntry) -> dict:
    """Options override data."""
    merged = dict(entry.data)
    merged.update(entry.options or {})
    return merged


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    coordinator = IrrigationCoordinator(hass, entry.entry_id, _merged(entry))
    await coordinator.async_start()
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    async def _options_updated(_hass: HomeAssistant, updated_entry: ConfigEntry) -> None:
        coordinator.update_config(_merged(updated_entry))
        _sync_zone_entity_ids(hass, updated_entry)

    entry.async_on_unload(entry.add_update_listener(_options_updated))
    return True


def _sync_zone_entity_ids(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Keep button.garden_irrigation_zone_<order> in sync with the configured order.

    Walks the entity registry, finds each zone button (by unique_id) and renames
    its entity_id to match its current run order. unique_id never changes, so the
    rename is non-destructive — automations referencing the entity by unique_id /
    via the device keep working; only the entity_id string changes.
    """
    registry = er.async_get(hass)
    merged = dict(entry.data)
    merged.update(entry.options or {})
    zones = merged.get(CONF_ZONES) or []

    # Two-pass rename to avoid transient collisions when two zones swap orders.
    desired: dict[str, str] = {}
    for z in zones:
        zid = z.get(CONF_ZONE_ID)
        order = z.get(CONF_ZONE_ORDER)
        if not zid or order is None:
            continue
        unique_id = f"{entry.entry_id}_zone_{zid}"
        ent = registry.async_get_entity_id("button", DOMAIN, unique_id)
        if ent:
            desired[ent] = f"button.garden_irrigation_zone_{int(order)}"

    # Pass 1: move all conflicting current ids out of the way to a temp slug.
    temp_map: dict[str, str] = {}
    target_set = set(desired.values())
    for current_eid in list(desired.keys()):
        if current_eid in target_set and desired[current_eid] != current_eid:
            tmp = f"{current_eid}__tmp_reorder"
            try:
                registry.async_update_entity(current_eid, new_entity_id=tmp)
                temp_map[current_eid] = tmp
            except Exception:  # noqa: BLE001
                pass

    # Pass 2: move each entity (possibly under its temp id) to its target.
    for original_eid, target_eid in desired.items():
        eid_now = temp_map.get(original_eid, original_eid)
        if eid_now == target_eid:
            continue
        try:
            registry.async_update_entity(eid_now, new_entity_id=target_eid)
        except Exception:  # noqa: BLE001
            # Likely a real collision (e.g. two zones share the same order).
            # Leave the entity at its temp/original id; user can fix manually.
            pass


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    coordinator: IrrigationCoordinator | None = hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    if coordinator:
        await coordinator.async_stop()
    return unload_ok
