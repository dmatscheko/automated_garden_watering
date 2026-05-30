"""Repair-issue helpers for missing/invalid switch entities.

A configured pump / backwash / zone valve may disappear from Home Assistant
(renamed, deleted, integration removed). The state machine still loads, but
the affected entities become unavailable. We surface that to the user as a
repair issue so they know to fix it instead of silently failing.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir

from .const import DOMAIN

if TYPE_CHECKING:
    from .coordinator import IrrigationCoordinator


_TRANSLATION_KEY = "missing_switch"


def _issue_id(kind: str, suffix: str | None = None) -> str:
    return f"missing_switch_{kind}" if suffix is None else f"missing_switch_{kind}_{suffix}"


@callback
def async_sync_issues(
    hass: HomeAssistant, coordinator: IrrigationCoordinator
) -> None:
    """Create or clear repair issues for each configured switch entity.

    Safe to call from setup, options-update, and any time the coordinator's
    configured entities change. Issues are keyed by `kind` (and zone id) so
    repeat calls converge on the right set without leaking stale issues.
    """
    expected: dict[str, tuple[str, str]] = {}
    # (kind, suffix, configured_entity_id, friendly_name)
    if coordinator.pump_switch:
        expected[_issue_id("pump")] = (coordinator.pump_switch, "pump")
    if coordinator.backwash_switch:
        expected[_issue_id("backwash")] = (coordinator.backwash_switch, "backwash valve")
    for zone in coordinator.zones.values():
        expected[_issue_id("zone", zone.id)] = (zone.entity_id, f"zone '{zone.name}'")

    # Raise an issue per missing entity. We check the entity *registry* rather
    # than `hass.states` because dependent integrations (ESPHome, MQTT, etc.)
    # may not have set up their entities yet by the time we run on startup —
    # checking states there would fire false-positive repairs that vanish
    # seconds later. A registry entry, on the other hand, means the user truly
    # has that entity configured even if its state isn't loaded yet.
    entity_reg = er.async_get(hass)
    raised: set[str] = set()
    for issue_id, (entity_id, name) in expected.items():
        if entity_reg.async_get(entity_id) is None and hass.states.get(entity_id) is None:
            ir.async_create_issue(
                hass,
                DOMAIN,
                issue_id,
                is_fixable=False,
                severity=ir.IssueSeverity.WARNING,
                translation_key=_TRANSLATION_KEY,
                translation_placeholders={"name": name, "entity": entity_id},
            )
            raised.add(issue_id)

    # Clear issues that no longer apply (entity reappeared, or no longer configured).
    issue_reg = ir.async_get(hass)
    for issue in list(issue_reg.issues.values()):
        if issue.domain != DOMAIN:
            continue
        if not issue.issue_id.startswith("missing_switch_"):
            continue
        if issue.issue_id not in raised:
            ir.async_delete_issue(hass, DOMAIN, issue.issue_id)
