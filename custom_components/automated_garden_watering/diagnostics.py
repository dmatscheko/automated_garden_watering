"""Diagnostics support for Automated Garden Watering."""
from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from . import AutomatedGardenWateringConfigEntry

# Nothing here is secret (no credentials), but redact entity_ids of user-chosen
# switches anyway to keep shared diagnostic dumps tidy.
_REDACT = {"pump_switch", "backwash_switch", "entity_id"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: AutomatedGardenWateringConfigEntry
) -> dict[str, Any]:
    coordinator = entry.runtime_data
    rt = coordinator.rt
    return {
        "entry": {
            "title": entry.title,
            "data": async_redact_data(dict(entry.data), _REDACT),
            "options": async_redact_data(dict(entry.options or {}), _REDACT),
        },
        "coordinator": {
            "state": rt.state,
            "queue": list(rt.queue),
            "active_remaining": rt.active_remaining,
            "phase_remaining": rt.phase_remaining,
            "accumulated_watering": rt.accumulated_watering,
            "since_last_backwash": rt.since_last_backwash,
            "started_by_timer": rt.started_by_timer,
            "pending_backwash": rt.pending_backwash,
            "multiplier_runtime": rt.multiplier,
            "multiplier_setting": coordinator.multiplier,
            "daily_start": coordinator.daily_start,
            "daily_timer_enabled": coordinator.daily_timer_enabled,
            "details_visible": coordinator.details_visible,
            "pump_configured": bool(coordinator.pump_switch),
            "backwash_configured": bool(coordinator.backwash_switch),
            "zones": [
                {
                    "id": z.id,
                    "name": z.name,
                    "duration": z.duration,
                    "order": z.order,
                    "last_run": (
                        coordinator.zone_last_run(z.id).isoformat()
                        if coordinator.zone_last_run(z.id)
                        else None
                    ),
                }
                for z in coordinator.zones.values()
            ],
            "pump_last_run": (
                coordinator.pump_last_run.isoformat()
                if coordinator.pump_last_run
                else None
            ),
            "backwash_last_run": (
                coordinator.backwash_last_run.isoformat()
                if coordinator.backwash_last_run
                else None
            ),
        },
    }
