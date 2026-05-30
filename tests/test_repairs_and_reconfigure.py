"""Tests for repair issues and the reconfiguration flow."""
from __future__ import annotations

import pytest
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.automated_garden_watering.const import (
    CONF_BACKWASH,
    CONF_PUMP,
    DOMAIN,
)

from .conftest import (
    BACKWASH_ENTITY,
    PUMP_ENTITY,
    ZONE1_ENTITY,
    ZONE2_ENTITY,
    make_config,
)


async def test_repair_issue_raised_for_missing_pump(
    hass: HomeAssistant,
) -> None:
    """A missing pump switch shows up as a WARNING-level repair issue."""
    # Only register backwash + zones; pump is missing.
    for ent in (BACKWASH_ENTITY, ZONE1_ENTITY, ZONE2_ENTITY):
        hass.states.async_set(ent, "off")
    entry = MockConfigEntry(
        domain=DOMAIN, data=make_config(), unique_id=DOMAIN, title="x"
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    issue_reg = ir.async_get(hass)
    issue = issue_reg.async_get_issue(DOMAIN, "missing_switch_pump")
    assert issue is not None
    assert issue.severity is ir.IssueSeverity.WARNING
    assert issue.translation_placeholders == {
        "name": "pump",
        "entity": PUMP_ENTITY,
    }


async def test_repair_issue_cleared_when_switch_reappears(
    hass: HomeAssistant,
) -> None:
    """If the switch re-appears, the repair issue is removed on config update."""
    for ent in (BACKWASH_ENTITY, ZONE1_ENTITY, ZONE2_ENTITY):
        hass.states.async_set(ent, "off")
    entry = MockConfigEntry(
        domain=DOMAIN, data=make_config(), unique_id=DOMAIN, title="x"
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    issue_reg = ir.async_get(hass)
    assert issue_reg.async_get_issue(DOMAIN, "missing_switch_pump") is not None

    # Now register the pump and trigger an options update so async_sync_issues
    # runs again.
    hass.states.async_set(PUMP_ENTITY, "off")
    new_data = make_config(pump_delay=42)
    hass.config_entries.async_update_entry(entry, options=new_data)
    await hass.async_block_till_done()
    assert issue_reg.async_get_issue(DOMAIN, "missing_switch_pump") is None


async def test_repair_issue_zone_specific(
    hass: HomeAssistant,
) -> None:
    """A missing zone valve yields a per-zone repair issue."""
    for ent in (PUMP_ENTITY, BACKWASH_ENTITY, ZONE2_ENTITY):
        hass.states.async_set(ent, "off")
    entry = MockConfigEntry(
        domain=DOMAIN, data=make_config(), unique_id=DOMAIN, title="x"
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    issue_reg = ir.async_get(hass)
    issue = issue_reg.async_get_issue(DOMAIN, "missing_switch_zone_z1")
    assert issue is not None


async def test_reconfigure_flow_updates_entry(
    hass: HomeAssistant, setup_integration: MockConfigEntry
) -> None:
    """The reconfigure step writes back into entry.data and reloads."""
    entry = setup_integration
    assert entry.data[CONF_PUMP] == PUMP_ENTITY

    result = await entry.start_reconfigure_flow(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reconfigure"

    new_pump = "switch.new_pump"
    hass.states.async_set(new_pump, "off")
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_PUMP: new_pump,
            CONF_BACKWASH: BACKWASH_ENTITY,
            "pump_pressure_delay": 7,
            "backwash_pressure_delay": 5,
            "backwash_runtime": 10,
            "backwash_flush_runtime": 5,
            "backwash_interval": 0,
            "backwash_threshold": 0,
        },
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.data[CONF_PUMP] == new_pump
    assert entry.data["pump_pressure_delay"] == 7
