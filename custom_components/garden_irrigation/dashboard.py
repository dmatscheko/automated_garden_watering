"""Generate a ready-to-paste Lovelace dashboard for a configured entry."""
from __future__ import annotations

from typing import Any

import yaml
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .const import DOMAIN
from .coordinator import IrrigationCoordinator

GREEN = "#2e7d32"
AMBER = "#ef6c00"


def _resolve(hass: HomeAssistant, entry_id: str, suffix: str, domain: str) -> str | None:
    """Look up the live entity_id for one of our entities by its unique_id."""
    registry = er.async_get(hass)
    return registry.async_get_entity_id(domain, DOMAIN, f"{entry_id}_{suffix}")


def _press_action(eid: str) -> dict[str, Any]:
    return {
        "action": "perform-action",
        "perform_action": "button.press",
        "target": {"entity_id": eid},
    }


def _styled(color: str) -> dict[str, Any]:
    return {"card": [{"background-color": color}, {"color": "white"}]}


def _button_card(
    eid: str,
    name: str,
    icon: str,
    states: list[dict[str, Any]],
    label: str | None = None,
) -> dict[str, Any]:
    card: dict[str, Any] = {
        "type": "custom:button-card",
        "entity": eid,
        "name": name,
        "icon": icon,
        "show_state": False,
        "tap_action": _press_action(eid),
        "state": states,
    }
    if label is not None:
        card["show_label"] = True
        card["label"] = label
    return card


def build_dashboard(hass: HomeAssistant, coordinator: IrrigationCoordinator) -> str:
    """Return a YAML string with a complete view for this integration."""
    entry_id = coordinator.entry_id

    water_all = _resolve(hass, entry_id, "water_all", "button")
    backwash = _resolve(hass, entry_id, "backwash", "button")
    status = _resolve(hass, entry_id, "status", "sensor")
    active_zone = _resolve(hass, entry_id, "active_zone", "sensor")
    queue = _resolve(hass, entry_id, "queue", "sensor")
    step_remaining = _resolve(hass, entry_id, "step_remaining", "sensor")
    queue_remaining = _resolve(hass, entry_id, "queue_remaining", "sensor")
    multiplier = _resolve(hass, entry_id, "multiplier", "number")
    daily_start = _resolve(hass, entry_id, "daily_start", "time")
    daily_timer = _resolve(hass, entry_id, "daily_timer", "switch")
    manual_pump = _resolve(hass, entry_id, "manual_pump", "switch")

    # Entities card (drop any that couldn't be resolved).
    entity_rows = [
        (status, "Status"),
        (step_remaining, "Current step time remaining"),
        (queue_remaining, "Queue time remaining"),
        (active_zone, "Active zone"),
        (queue, "Queue"),
        (multiplier, "Watering multiplier"),
        (daily_start, "Daily start time"),
        (daily_timer, "Daily timer"),
    ]
    entities_card = {
        "type": "entities",
        "title": "Garden Irrigation",
        "show_header_toggle": False,
        "entities": [
            {"entity": eid, "name": label} for eid, label in entity_rows if eid
        ],
    }

    # Action buttons.
    action_cards: list[dict[str, Any]] = []
    if water_all:
        action_cards.append(
            _button_card(
                water_all,
                "Water all / STOP",
                "mdi:sprinkler-variant",
                [
                    {
                        "operator": "template",
                        "value": f"[[[ return states['{water_all}'].attributes.running === true ]]]",
                        "styles": _styled(GREEN),
                    }
                ],
            )
        )
    if backwash:
        backwash_label = (
            f"[[[ const a = states['{backwash}'].attributes; "
            "if (a.status === 'running') return 'Running'; "
            "if (a.last_run_friendly) return 'Last: ' + a.last_run_friendly; "
            "return 'Never run'; ]]]"
        )
        action_cards.append(
            _button_card(
                backwash,
                "Backwash",
                "mdi:backup-restore",
                [
                    {
                        "operator": "template",
                        "value": f"[[[ return states['{backwash}'].attributes.status === 'running' ]]]",
                        "styles": _styled(GREEN),
                    }
                ],
                label=backwash_label,
            )
        )
    if manual_pump:
        # The manual pump is a switch -> tap toggles it. Styled like the zone
        # buttons: green while on, with a "last run" line when off.
        pump_label = (
            f"[[[ const e = states['{manual_pump}']; "
            "if (e.state === 'on') return 'On'; "
            "if (e.attributes.last_run_friendly) return 'Last: ' + e.attributes.last_run_friendly; "
            "return 'Never run'; ]]]"
        )
        action_cards.append(
            {
                "type": "custom:button-card",
                "entity": manual_pump,
                "name": "Pump (manual)",
                "icon": "mdi:water-pump",
                "show_state": False,
                "show_label": True,
                "tap_action": {"action": "toggle"},
                "state": [{"value": "on", "styles": _styled(GREEN)}],
                "label": pump_label,
            }
        )

    # Zone buttons in run order, colored by status with a live countdown label.
    zone_cards: list[dict[str, Any]] = []
    for zid in coordinator.ordered_zone_ids():
        zone = coordinator.zones[zid]
        eid = _resolve(hass, entry_id, f"zone_{zid}", "button")
        if not eid:
            continue
        label = (
            f"[[[ const a = states['{eid}'].attributes; "
            "if (a.status === 'running') { const s = Math.max(0, a.remaining_seconds || 0); "
            "return 'Running ' + Math.floor(s/60) + ':' + ('0'+(s%60)).slice(-2); } "
            "if (a.status === 'queued') return 'Queued #' + a.queue_position; "
            "if (a.last_run_friendly) return 'Last: ' + a.last_run_friendly; "
            "return 'Never run'; ]]]"
        )
        zone_cards.append(
            _button_card(
                eid,
                zone.name,
                "mdi:water",
                [
                    {
                        "operator": "template",
                        "value": f"[[[ return states['{eid}'].attributes.status === 'running' ]]]",
                        "styles": _styled(GREEN),
                    },
                    {
                        "operator": "template",
                        "value": f"[[[ return states['{eid}'].attributes.status === 'queued' ]]]",
                        "styles": _styled(AMBER),
                    },
                ],
                label=label,
            )
        )

    # All buttons share one 2-column grid so the global actions are the same
    # (full) size as the zone buttons and have room for their last-run labels.
    # Their distinct icons keep them recognizable. Actions come first.
    inner_cards: list[dict[str, Any]] = [entities_card]
    grid_cards = action_cards + zone_cards
    if grid_cards:
        inner_cards.append(
            {"type": "grid", "columns": 2, "square": False, "cards": grid_cards}
        )

    view = {
        "title": "Garden",
        "path": "garden-irrigation",
        "icon": "mdi:sprinkler",
        "cards": [{"type": "vertical-stack", "cards": inner_cards}],
    }

    header = (
        "# Generated by Garden Irrigation.\n"
        "# Open your dashboard -> Edit -> (top-right) Raw configuration editor,\n"
        "# and paste the block below under the existing 'views:' key.\n"
        "# Colored buttons need the 'button-card' frontend resource (install via HACS).\n"
        "# Without it, replace 'custom:button-card' with 'button' (you lose coloring,\n"
        "# the press actions still work).\n\n"
    )
    body = yaml.safe_dump(
        [view], sort_keys=False, allow_unicode=True, default_flow_style=False
    )
    return header + body
