"""Buttons: Water All, Backwash, per-zone toggle, plus config helpers."""
from __future__ import annotations

import json
import logging

from homeassistant.components.button import ButtonEntity
from homeassistant.components.persistent_notification import (
    async_create as async_create_notification,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from .const import (
    BACKWASH_ACTIVE_STATES,
    DOMAIN,
    STATE_IDLE,
)
from .coordinator import IrrigationCoordinator
from .dashboard import build_dashboard
from .entity import IrrigationBaseEntity

_LOGGER = logging.getLogger(__name__)

# Backup file the export button writes into <config>/. The legacy filename is
# what pre-rename users still have on disk from v0.4.1; the importer accepts
# either, but new exports use the current name.
EXPORT_FILENAME = "automated_garden_watering_export.json"
LEGACY_EXPORT_FILENAME = "garden_irrigation_export.json"
EXPORT_SCHEMA_VERSION = 1


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: IrrigationCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[ButtonEntity] = [
        WaterAllButton(coordinator),
        BackwashButton(coordinator),
        GenerateDashboardButton(coordinator),
        ExportConfigButton(coordinator),
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

    _attr_icon = "mdi:view-dashboard-outline"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: IrrigationCoordinator) -> None:
        super().__init__(coordinator, "generate_dashboard", "Generate dashboard YAML")

    async def async_press(self) -> None:
        yaml_text = build_dashboard(self.hass, self.coordinator)

        # Best-effort: also drop a file next to configuration.yaml.
        path = self.hass.config.path("automated_garden_watering_dashboard.yaml")

        def _write() -> None:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(yaml_text)

        wrote = True
        try:
            await self.hass.async_add_executor_job(_write)
        except Exception:  # noqa: BLE001
            wrote = False

        file_line = (
            f"Also saved to `{path}`.\n\n" if wrote else ""
        )
        message = (
            "Copy the YAML below into your dashboard's **Raw configuration editor** "
            "(under the existing `views:` key).\n\n"
            f"{file_line}"
            f"```yaml\n{yaml_text}\n```"
        )
        async_create_notification(
            self.hass,
            message,
            title="Automated Garden Watering dashboard",
            notification_id=f"{DOMAIN}_dashboard_{self.coordinator.entry_id}",
        )


class ExportConfigButton(IrrigationBaseEntity, ButtonEntity):
    """Write a JSON backup of this integration's config and last-run history.

    Intended for migrating to a renamed/reinstalled integration: press once,
    keep the resulting file safe, then import it from the new integration's
    setup flow to restore zones, timings and the last-run timestamps.
    """

    _attr_icon = "mdi:database-export-outline"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: IrrigationCoordinator) -> None:
        super().__init__(coordinator, "export_config", "Export configuration")

    def _find_entry(self) -> ConfigEntry | None:
        for entry in self.hass.config_entries.async_entries(DOMAIN):
            if entry.entry_id == self.coordinator.entry_id:
                return entry
        return None

    def _build_payload(self, entry: ConfigEntry) -> dict:
        c = self.coordinator
        # Flatten HA's entry.data (initial setup snapshot) and entry.options
        # (every later change) into the single "config" dict the coordinator
        # actually uses — options overrides data, just like _merged() does at
        # load time. This avoids confusing duplicate / stale-looking blocks in
        # the export and gives the importer one unambiguous source of truth.
        config = dict(entry.data)
        config.update(entry.options or {})
        return {
            "schema_version": EXPORT_SCHEMA_VERSION,
            "source_domain": DOMAIN,
            "exported_at": dt_util.now().isoformat(),
            "config": config,
            "state": {
                "last_run": {
                    zid: dt.isoformat() for zid, dt in c.last_run.items()
                },
                "pump_last_run": (
                    c.pump_last_run.isoformat() if c.pump_last_run else None
                ),
                "backwash_last_run": (
                    c.backwash_last_run.isoformat() if c.backwash_last_run else None
                ),
                "details_visible": c.details_visible,
            },
        }

    async def async_press(self) -> None:
        entry = self._find_entry()
        if entry is None:
            _LOGGER.error("Export pressed but config entry not found")
            return
        payload = self._build_payload(entry)
        path = self.hass.config.path(EXPORT_FILENAME)

        def _write() -> None:
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, indent=2, ensure_ascii=False)

        try:
            await self.hass.async_add_executor_job(_write)
        except OSError as err:
            _LOGGER.error("Could not write %s: %s", path, err)
            async_create_notification(
                self.hass,
                f"Could not write backup file `{path}`: {err}",
                title="Automated Garden Watering – backup failed",
                notification_id=f"{DOMAIN}_export_{self.coordinator.entry_id}",
            )
            return

        de = (self.hass.config.language or "en").lower().startswith("de")
        n_zones = len(payload["config"].get("zones") or [])
        if de:
            title = "Gartenbewässerung – Sicherung erstellt"
            message = (
                f"Konfiguration und Verlauf nach `{path}` exportiert "
                f"({n_zones} Zone(n)).\n\n"
                "Bewahre diese Datei sicher auf. Nach dem Umbenennen oder "
                "Neu-Installieren der Integration kannst du sie im Einrichtungs-"
                "Dialog importieren, um Zonen, Zeiten und „Zuletzt gelaufen“-"
                "Zeiten wiederherzustellen."
            )
        else:
            title = "Automated Garden Watering – backup created"
            message = (
                f"Saved configuration and history to `{path}` "
                f"({n_zones} zone(s)).\n\n"
                "Keep this file safe. When you rename or re-install the "
                "integration, you can import it from the setup dialog to "
                "restore your zones, timings and last-run times."
            )
        async_create_notification(
            self.hass,
            message,
            title=title,
            notification_id=f"{DOMAIN}_export_{self.coordinator.entry_id}",
        )


class ZoneToggleButton(IrrigationBaseEntity, ButtonEntity):
    _attr_icon = "mdi:water"

    def __init__(self, coordinator: IrrigationCoordinator, zone_id: str) -> None:
        zone = coordinator.zones[zone_id]
        # Friendly name = the user-supplied display name (updates dynamically
        # via the `name` property below). HA generates the natural
        # button.<device>_<name> id; __init__.py then renames it to
        # button.<device>_zone_<order> so the buttons sort by run order.
        # unique_id stays uuid-based so the entity survives renames/reorders.
        super().__init__(coordinator, f"zone_{zone_id}", zone.name)
        self._zone_id = zone_id

    @property
    def name(self) -> str:
        zone = self.coordinator.zones.get(self._zone_id)
        return zone.name if zone else self._attr_name

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
