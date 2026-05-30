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
# Exactly the fallback chain button-card uses for the switch-button icons
# (Details, Pump). `--paper-item-icon-color` is usually undefined, so it falls
# back to `--state-icon-color` (the HA blue). Applying this to Water all /
# Backwash gives all four "main" buttons a matching blue icon, separating them
# from the white-icon zone buttons.
ICON_BLUE = "var(--paper-item-icon-color, var(--state-icon-color))"
# The active icon color an "on" switch (Details, Pump) gets. Used to turn the
# Water all / Backwash icons yellow while running. Includes a fallback chain so
# it never resolves to "undefined" (which would make the icon inherit white):
# modern switch-active color -> legacy active var -> a literal HA amber.
ICON_YELLOW = (
    "var(--state-switch-active-color, "
    "var(--paper-item-icon-active-color, #fdd835))"
)


# Dashboard labels per language (the generated dashboard's visible texts).
_LABELS = {
    "en": {
        "view_title": "Garden",
        "status": "Status",
        "step_remaining": "Current step time remaining",
        "queue_remaining": "Queue time remaining",
        "active_zone": "Active zone",
        "queue": "Queue",
        "multiplier": "Watering multiplier",
        "daily_start": "Daily start time",
        "daily_timer": "Daily timer",
        "water_all": "Water all / STOP",
        "details": "Details",
        "backwash": "Backwash",
        "pump": "Pump (manual)",
        "running": "Running",
        "queued": "Queued #",
        "last": "Last: ",
        "never": "Never run",
        "on": "On",
    },
    "de": {
        "view_title": "Garten",
        "status": "Status",
        "step_remaining": "Aktueller Schritt – Restzeit",
        "queue_remaining": "Warteschlange – Restzeit",
        "active_zone": "Aktive Zone",
        "queue": "Warteschlange",
        "multiplier": "Bewässerungs-Multiplikator",
        "daily_start": "Tägliche Startzeit",
        "daily_timer": "Tages-Timer",
        "water_all": "Alles bewässern / STOPP",
        "details": "Details",
        "backwash": "Rückspülung",
        "pump": "Pumpe (manuell)",
        "running": "Läuft",
        "queued": "Warteschlange #",
        "last": "Zuletzt: ",
        "never": "Nie gelaufen",
        "on": "An",
    },
}


def _labels(hass: HomeAssistant) -> dict[str, str]:
    lang = (hass.config.language or "en").lower()
    return _LABELS["de"] if lang.startswith("de") else _LABELS["en"]


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


def _styled_active(color: str) -> dict[str, Any]:
    """Like _styled but also turns the icon yellow (for the main action buttons)."""
    return {
        "card": [{"background-color": color}, {"color": "white"}],
        "icon": [{"color": ICON_YELLOW}],
    }


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
    L = _labels(hass)

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
    details = _resolve(hass, entry_id, "details_visible", "switch")

    # Entities card (drop any that couldn't be resolved).
    entity_rows = [
        (status, L["status"]),
        (step_remaining, L["step_remaining"]),
        (queue_remaining, L["queue_remaining"]),
        (active_zone, L["active_zone"]),
        (queue, L["queue"]),
        (multiplier, L["multiplier"]),
        (daily_start, L["daily_start"]),
        (daily_timer, L["daily_timer"]),
    ]
    entities_card = {
        "type": "entities",
        "show_header_toggle": False,
        "entities": [
            {"entity": eid, "name": label} for eid, label in entity_rows if eid
        ],
    }

    # --- Action buttons (built individually so we can place them precisely) ---
    water_all_card = (
        _button_card(
            water_all,
            L["water_all"],
            "mdi:sprinkler-variant",
            [
                {
                    "operator": "template",
                    "value": f"[[[ return states['{water_all}'].attributes.running === true ]]]",
                    "styles": _styled_active(GREEN),
                }
            ],
        )
        if water_all
        else None
    )
    if water_all_card:
        water_all_card["styles"] = {"icon": [{"color": ICON_BLUE}]}

    details_card = (
        {
            "type": "custom:button-card",
            "entity": details,
            "name": L["details"],
            "icon": "mdi:information-outline",
            "show_state": False,
            "tap_action": {"action": "toggle"},
            "state": [{"value": "on", "styles": _styled(GREEN)}],
        }
        if details
        else None
    )

    backwash_card = None
    if backwash:
        backwash_label = (
            f"[[[ const a = states['{backwash}'].attributes; "
            f"if (a.status === 'running') return '{L['running']}'; "
            f"if (a.last_run_friendly) return '{L['last']}' + a.last_run_friendly; "
            f"return '{L['never']}'; ]]]"
        )
        backwash_card = _button_card(
            backwash,
            L["backwash"],
            "mdi:backup-restore",
            [
                {
                    "operator": "template",
                    "value": f"[[[ return states['{backwash}'].attributes.status === 'running' ]]]",
                    "styles": _styled_active(GREEN),
                }
            ],
            label=backwash_label,
        )
        backwash_card["styles"] = {"icon": [{"color": ICON_BLUE}]}

    pump_card = None
    if manual_pump:
        # The manual pump is a switch -> tap toggles it. Styled like the zone
        # buttons: green while on, with a "last run" line when off.
        pump_label = (
            f"[[[ const e = states['{manual_pump}']; "
            f"if (e.state === 'on') return '{L['on']}'; "
            f"if (e.attributes.last_run_friendly) return '{L['last']}' + e.attributes.last_run_friendly; "
            f"return '{L['never']}'; ]]]"
        )
        pump_card = {
            "type": "custom:button-card",
            "entity": manual_pump,
            "name": L["pump"],
            "icon": "mdi:water-pump",
            "show_state": False,
            "show_label": True,
            "tap_action": {"action": "toggle"},
            "state": [{"value": "on", "styles": _styled(GREEN)}],
            "label": pump_label,
        }

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
            f"return '{L['running']} ' + Math.floor(s/60) + ':' + ('0'+(s%60)).slice(-2); }} "
            f"if (a.status === 'queued') return '{L['queued']}' + a.queue_position; "
            f"if (a.last_run_friendly) return '{L['last']}' + a.last_run_friendly; "
            f"return '{L['never']}'; ]]]"
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

    # Layout: a first 2-column row holds [Water all, Details]. Pressing Details
    # toggles a conditional card that reveals the status/timer/multiplier list
    # right below that row. Everything else (Backwash, Pump, zones) follows in a
    # second 2-column grid, so all buttons keep the same full size.
    def _grid(cards: list[dict[str, Any]]) -> dict[str, Any]:
        return {"type": "grid", "columns": 2, "square": False, "cards": cards}

    inner_cards: list[dict[str, Any]] = []

    if details_card:
        top_row = [c for c in (water_all_card, details_card) if c]
        inner_cards.append(_grid(top_row))
        inner_cards.append(
            {
                "type": "conditional",
                "conditions": [{"entity": details, "state": "on"}],
                "card": entities_card,
            }
        )
        rest = [c for c in (backwash_card, pump_card) if c] + zone_cards
        if rest:
            inner_cards.append(_grid(rest))
    else:
        # Fallback (no details switch): show the list, then all buttons.
        inner_cards.append(entities_card)
        all_buttons = [c for c in (water_all_card, backwash_card, pump_card) if c]
        all_buttons += zone_cards
        if all_buttons:
            inner_cards.append(_grid(all_buttons))

    view = {
        "title": L["view_title"],
        "path": "garden-irrigation",
        "icon": "mdi:sprinkler",
        "cards": [{"type": "vertical-stack", "cards": inner_cards}],
    }

    header = (
        "# Generated by Automated Garden Watering.\n"
        "# Open your dashboard -> Edit -> (top-right) Raw configuration editor,\n"
        "# and paste the block below under the existing 'views:' key.\n"
        "# Colored buttons need the 'button-card' frontend resource (install via HACS).\n"
        "# Without it, replace 'custom:button-card' with 'button' (you lose coloring,\n"
        "# the press actions still work).\n\n"
    )
    body: str = yaml.safe_dump(
        [view], sort_keys=False, allow_unicode=True, default_flow_style=False
    )
    return header + body
