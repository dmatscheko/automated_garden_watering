"""Automated Garden Watering integration."""
from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.storage import Store
from homeassistant.util import slugify

from .const import CONF_ZONE_ID, CONF_ZONE_ORDER, CONF_ZONES, DOMAIN
from .coordinator import IrrigationCoordinator

# Marker key in entry.data set by the config flow's import step. When seen on
# setup we pre-write the integration's Store with the imported state so the
# coordinator restores last-run history just like a regular reload would.
IMPORT_STATE_KEY = "__import_state__"
STORAGE_VERSION = 1

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


def _zone_ids(data: dict) -> set[str]:
    return {z[CONF_ZONE_ID] for z in (data.get(CONF_ZONES) or []) if z.get(CONF_ZONE_ID)}


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    # If this entry came from the import flow, pre-seed the integration's Store
    # so the coordinator's normal load path picks up the migrated last-run
    # history. Done before creating the coordinator so its async_start() reads
    # the freshly written store.
    if IMPORT_STATE_KEY in entry.data:
        await _consume_import_state(hass, entry)

    coordinator = IrrigationCoordinator(hass, entry.entry_id, _merged(entry))
    await coordinator.async_start()
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    # Entities (and the device) now exist; give zone buttons their ordered ids.
    _sync_zone_entity_ids(hass, entry)

    entry.async_on_unload(entry.add_update_listener(_options_updated))
    return True


async def _consume_import_state(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Move an imported state payload from entry.data into the Store.

    Idempotent and one-shot: after writing the Store we drop the marker key
    from entry.data so a subsequent reload doesn't re-import (and overwrite
    fresh state with the snapshot from the backup).
    """
    state = entry.data.get(IMPORT_STATE_KEY) or {}
    if isinstance(state, dict) and state:
        # Same key the coordinator uses for its Store.
        store: Store = Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}")
        await store.async_save(
            {
                "last_run": state.get("last_run") or {},
                "pump_last_run": state.get("pump_last_run"),
                "backwash_last_run": state.get("backwash_last_run"),
                "details_visible": bool(state.get("details_visible", False)),
            }
        )
        _LOGGER.info(
            "Restored last-run history for %d zone(s) from imported backup",
            len(state.get("last_run") or {}),
        )

    new_data = {k: v for k, v in entry.data.items() if k != IMPORT_STATE_KEY}
    hass.config_entries.async_update_entry(entry, data=new_data)


async def _options_updated(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle an options/data change on the config entry."""
    coordinator: IrrigationCoordinator = hass.data[DOMAIN][entry.entry_id]

    # Compare the set of zones BEFORE applying the new config. The coordinator
    # still holds the previously loaded zones at this point.
    old_ids = set(coordinator.zones.keys())
    new_ids = _zone_ids(_merged(entry))

    if old_ids != new_ids:
        # A zone was added or removed -> entities must be created/destroyed.
        # A full reload is the simplest robust way to do that.
        await hass.config_entries.async_reload(entry.entry_id)
        return

    # Only in-place changes (multiplier, timings, daily timer, zone name /
    # duration / order). Update live and re-sync ordered entity_ids; no reload,
    # so an active watering run is never interrupted.
    coordinator.update_config(_merged(entry))
    _sync_zone_entity_ids(hass, entry)


def _device_slug(hass: HomeAssistant, entry: ConfigEntry) -> str:
    """Slug of the integration device name, used as the entity_id prefix.

    Respects a user-renamed device so zone buttons become
    button.<device-name>_zone_<order> rather than a hardcoded prefix.
    """
    dev_reg = dr.async_get(hass)
    device = dev_reg.async_get_device(identifiers={(DOMAIN, entry.entry_id)})
    name = None
    if device:
        name = device.name_by_user or device.name
    return slugify(name or "Automated Garden Watering")


def _sync_zone_entity_ids(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Keep zone button entity_ids as button.<device>_zone_<order>.

    Walks the entity registry, finds each zone button (by its stable unique_id)
    and renames its entity_id to match its current run order, preserving the
    device-name prefix. unique_id never changes, so the rename is
    non-destructive: HA offers to migrate references in automations/dashboards.
    """
    registry = er.async_get(hass)
    prefix = _device_slug(hass, entry)
    zones = _merged(entry).get(CONF_ZONES) or []

    desired: dict[str, str] = {}
    for z in zones:
        zid = z.get(CONF_ZONE_ID)
        order = z.get(CONF_ZONE_ORDER)
        if not zid or order is None:
            continue
        ent = registry.async_get_entity_id(
            "button", DOMAIN, f"{entry.entry_id}_zone_{zid}"
        )
        if ent:
            desired[ent] = f"button.{prefix}_zone_{int(order)}"

    # Pass 1: move entities whose current id is some other entity's target out
    # of the way, so swapping two zones' orders doesn't collide mid-rename.
    temp_map: dict[str, str] = {}
    target_set = set(desired.values())
    for current_eid in list(desired):
        if current_eid in target_set and desired[current_eid] != current_eid:
            tmp = f"{current_eid}__tmp_reorder"
            try:
                registry.async_update_entity(current_eid, new_entity_id=tmp)
                temp_map[current_eid] = tmp
            except Exception:  # noqa: BLE001
                pass

    # Pass 2: move each entity to its target id.
    for original_eid, target_eid in desired.items():
        eid_now = temp_map.get(original_eid, original_eid)
        if eid_now == target_eid:
            continue
        try:
            registry.async_update_entity(eid_now, new_entity_id=target_eid)
        except Exception:  # noqa: BLE001
            # Real collision (e.g. two zones share the same order) -> leave it;
            # user can fix by giving zones distinct order numbers.
            _LOGGER.warning(
                "Could not rename %s to %s (entity_id already in use)",
                eid_now,
                target_eid,
            )


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    coordinator: IrrigationCoordinator | None = hass.data.get(DOMAIN, {}).pop(
        entry.entry_id, None
    )
    if coordinator:
        await coordinator.async_stop()
    return unload_ok
