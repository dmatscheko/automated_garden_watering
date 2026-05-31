"""Automated Garden Watering integration."""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.typing import ConfigType
from homeassistant.util import slugify

from .const import (
    CONF_ZONE_ID,
    CONF_ZONE_NAME,
    CONF_ZONE_ORDER,
    CONF_ZONES,
    DOMAIN,
    STATE_IDLE,
)
from .coordinator import IrrigationCoordinator
from .repairs import async_sync_issues

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

# ConfigEntry with our coordinator attached via runtime_data.
type AutomatedGardenWateringConfigEntry = ConfigEntry[IrrigationCoordinator]

PLATFORMS: list[Platform] = [
    Platform.BUTTON,
    Platform.NUMBER,
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.TIME,
]

SERVICE_WATER_ALL = "water_all"
SERVICE_STOP = "stop"
SERVICE_BACKWASH = "backwash"
SERVICE_WATER_ZONE = "water_zone"

ATTR_ZONE = "zone"

_WATER_ZONE_SCHEMA = vol.Schema({vol.Required(ATTR_ZONE): cv.string})


def _merged(entry: ConfigEntry) -> dict[str, Any]:
    """Options override data."""
    merged: dict[str, Any] = dict(entry.data)
    merged.update(entry.options or {})
    return merged


def _zone_ids(data: dict[str, Any]) -> set[str]:
    return {z[CONF_ZONE_ID] for z in (data.get(CONF_ZONES) or []) if z.get(CONF_ZONE_ID)}


def _loaded_coordinator(hass: HomeAssistant) -> IrrigationCoordinator:
    """Return the coordinator of the (single) loaded config entry.

    Raises ServiceValidationError if no entry is loaded — surfaced to the user
    via the HA UI when they call an action without the integration set up.
    """
    for entry in hass.config_entries.async_entries(DOMAIN):
        if entry.state is ConfigEntryState.LOADED:
            coord: IrrigationCoordinator = entry.runtime_data
            return coord
    raise ServiceValidationError(
        translation_domain=DOMAIN,
        translation_key="not_loaded",
    )


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register integration-level service actions (one-time)."""

    async def _water_all(call: ServiceCall) -> None:
        await _loaded_coordinator(hass).async_water_all(from_timer=False)

    async def _stop(call: ServiceCall) -> None:
        coord = _loaded_coordinator(hass)
        # async_water_all doubles as emergency stop when something is running.
        if coord.rt.queue or coord.rt.state != STATE_IDLE:
            await coord.async_water_all(from_timer=False)

    async def _backwash(call: ServiceCall) -> None:
        coord = _loaded_coordinator(hass)
        if not coord.backwash_switch:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="no_backwash_configured",
            )
        await coord.async_backwash_now()

    async def _water_zone(call: ServiceCall) -> None:
        coord = _loaded_coordinator(hass)
        target = (call.data.get(ATTR_ZONE) or "").strip()
        if not target:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="zone_required",
            )
        # Resolve by zone id, zone display name, or valve entity_id.
        match_id: str | None = None
        lowered = target.lower()
        for zone in coord.zones.values():
            if (
                zone.id == target
                or zone.entity_id == target
                or zone.name.lower() == lowered
            ):
                match_id = zone.id
                break
        if not match_id:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="zone_not_found",
                translation_placeholders={"zone": target},
            )
        await coord.async_toggle_zone(match_id)

    hass.services.async_register(DOMAIN, SERVICE_WATER_ALL, _water_all)
    hass.services.async_register(DOMAIN, SERVICE_STOP, _stop)
    hass.services.async_register(DOMAIN, SERVICE_BACKWASH, _backwash)
    hass.services.async_register(
        DOMAIN, SERVICE_WATER_ZONE, _water_zone, schema=_WATER_ZONE_SCHEMA
    )
    return True


async def async_setup_entry(
    hass: HomeAssistant, entry: AutomatedGardenWateringConfigEntry
) -> bool:
    coordinator = IrrigationCoordinator(hass, entry.entry_id, _merged(entry))
    await coordinator.async_start()
    entry.runtime_data = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    # Entities (and the device) now exist; give zone buttons their ordered ids.
    _sync_zone_entity_ids(hass, entry)
    # Surface a repair issue for any configured switch entity that's missing.
    async_sync_issues(hass, coordinator)

    entry.async_on_unload(entry.add_update_listener(_options_updated))
    return True


async def _options_updated(
    hass: HomeAssistant, entry: AutomatedGardenWateringConfigEntry
) -> None:
    """Handle an options/data change on the config entry."""
    coordinator = entry.runtime_data

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
    async_sync_issues(hass, coordinator)


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
    """Keep zone button entity_ids as button.<device>_zone_<name-slug>.

    The entity_id mirrors the zone's display name (slugified). This means:
    - dashboards and automations referencing zones by entity_id stay readable
      ('…_zone_fensterblumen' beats '…_zone_4');
    - reordering zones does NOT change entity_ids, so dashboard buttons keep
      pointing at the same zone after the user changes run-order numbers.

    Collisions (two zones whose names slugify identically) are broken by
    appending '_2', '_3', … in run-order order — the zone with the lower
    `order` keeps the bare slug. unique_id never changes, so any rename is
    non-destructive: HA offers to migrate references in automations and
    dashboards automatically.
    """
    registry = er.async_get(hass)
    prefix = _device_slug(hass, entry)
    zones = _merged(entry).get(CONF_ZONES) or []

    # Resolve each zone -> desired entity_id in a deterministic order so
    # collision-breaking is stable across runs.
    sorted_zones = sorted(
        (z for z in zones if z.get(CONF_ZONE_ID)),
        key=lambda z: (int(z.get(CONF_ZONE_ORDER) or 0), z.get(CONF_ZONE_NAME) or ""),
    )
    taken: set[str] = set()
    desired: dict[str, str] = {}
    for z in sorted_zones:
        zid = z[CONF_ZONE_ID]
        name = (z.get(CONF_ZONE_NAME) or "").strip()
        slug = slugify(name) or "zone"
        candidate = f"button.{prefix}_zone_{slug}"
        n = 2
        while candidate in taken:
            candidate = f"button.{prefix}_zone_{slug}_{n}"
            n += 1
        taken.add(candidate)
        ent = registry.async_get_entity_id(
            "button", DOMAIN, f"{entry.entry_id}_zone_{zid}"
        )
        if ent:
            desired[ent] = candidate

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


async def async_unload_entry(
    hass: HomeAssistant, entry: AutomatedGardenWateringConfigEntry
) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok and (coordinator := getattr(entry, "runtime_data", None)):
        await coordinator.async_stop()
    return unload_ok
