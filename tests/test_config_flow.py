"""Config + options flow coverage."""
from __future__ import annotations

from typing import Any

import pytest
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.automated_garden_watering.const import (
    CONF_BACKWASH,
    CONF_PUMP,
    CONF_ZONE_DURATION,
    CONF_ZONE_ENTITY,
    CONF_ZONE_NAME,
    CONF_ZONE_ORDER,
    CONF_ZONES,
    DOMAIN,
)

from .conftest import (
    BACKWASH_ENTITY,
    PUMP_ENTITY,
    ZONE1_ENTITY,
    ZONE2_ENTITY,
    make_config,
)


def _globals_user_input(**overrides: Any) -> dict[str, Any]:
    base = {
        CONF_PUMP: PUMP_ENTITY,
        CONF_BACKWASH: BACKWASH_ENTITY,
        "pump_pressure_delay": 5,
        "backwash_pressure_delay": 5,
        "backwash_runtime": 10,
        "backwash_flush_runtime": 5,
        "backwash_interval": 0,
        "backwash_threshold": 0,
    }
    base.update(overrides)
    return base


def _zone_user_input(name: str = "Front lawn", order: int = 1) -> dict[str, Any]:
    return {
        CONF_ZONE_NAME: name,
        CONF_ZONE_ENTITY: ZONE1_ENTITY,
        CONF_ZONE_DURATION: 60,
        CONF_ZONE_ORDER: order,
    }


async def test_user_flow_single_zone(hass: HomeAssistant) -> None:
    """The happy path: globals → one zone → finish without adding more."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], _globals_user_input()
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "zone"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], _zone_user_input()
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "zone_more"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"add_another": False}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_PUMP] == PUMP_ENTITY
    assert len(result["data"][CONF_ZONES]) == 1
    assert result["data"][CONF_ZONES][0][CONF_ZONE_NAME] == "Front lawn"


async def test_user_flow_multiple_zones(hass: HomeAssistant) -> None:
    """Adding a second zone via the 'add another' loop."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], _globals_user_input()
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], _zone_user_input("A", order=1)
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"add_another": True}
    )
    assert result["step_id"] == "zone"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_ZONE_NAME: "B",
            CONF_ZONE_ENTITY: ZONE2_ENTITY,
            CONF_ZONE_DURATION: 60,
            CONF_ZONE_ORDER: 2,
        },
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"add_another": False}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert [z[CONF_ZONE_NAME] for z in result["data"][CONF_ZONES]] == ["A", "B"]


async def test_duplicate_config_aborts(hass: HomeAssistant) -> None:
    """A second config entry attempt is aborted by unique_id."""
    MockConfigEntry(
        domain=DOMAIN, data=make_config(), unique_id=DOMAIN, title="x"
    ).add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], _globals_user_input()
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], _zone_user_input()
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"add_another": False}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_options_flow_globals_edit(hass: HomeAssistant) -> None:
    """Options → globals step updates the entry options."""
    entry = MockConfigEntry(
        domain=DOMAIN, data=make_config(), unique_id=DOMAIN, title="x"
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.MENU
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "globals"}
    )
    assert result["step_id"] == "globals"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], _globals_user_input(pump_pressure_delay=99)
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options["pump_pressure_delay"] == 99


async def test_options_flow_add_zone(hass: HomeAssistant) -> None:
    """Adding a new zone via the options menu."""
    entry = MockConfigEntry(
        domain=DOMAIN, data=make_config(), unique_id=DOMAIN, title="x"
    )
    entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "zones_list"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"action": "__add__"}
    )
    assert result["step_id"] == "zone_edit"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_ZONE_NAME: "Zone 3",
            CONF_ZONE_ENTITY: "switch.zone3",
            CONF_ZONE_DURATION: 60,
            CONF_ZONE_ORDER: 3,
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    zones = entry.options[CONF_ZONES]
    assert len(zones) == 3
    assert zones[-1][CONF_ZONE_NAME] == "Zone 3"


async def test_options_flow_edit_existing_zone(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN, data=make_config(), unique_id=DOMAIN, title="x"
    )
    entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "zones_list"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"action": "edit:z1"}
    )
    assert result["step_id"] == "zone_edit"
    # Schema should include the delete_zone field for an existing zone.
    schema_keys = list(result["data_schema"].schema)
    assert any(getattr(k, "schema", k) == "delete_zone" for k in schema_keys)

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_ZONE_NAME: "Renamed",
            CONF_ZONE_ENTITY: ZONE1_ENTITY,
            CONF_ZONE_DURATION: 120,
            CONF_ZONE_ORDER: 1,
            "delete_zone": False,
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    zones = entry.options[CONF_ZONES]
    z1 = next(z for z in zones if z["id"] == "z1")
    assert z1[CONF_ZONE_NAME] == "Renamed"
    assert z1[CONF_ZONE_DURATION] == 120


async def test_options_flow_clears_optional_switches(
    hass: HomeAssistant, setup_integration: MockConfigEntry
) -> None:
    """Submitting globals with pump/backwash cleared actually removes them.

    A cleared EntitySelector is simply absent from user_input; the flow must
    store an explicit None so the merge with entry.data can't resurrect the
    old switches.
    """
    entry = setup_integration
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "globals"}
    )
    user_input = _globals_user_input()
    del user_input[CONF_PUMP]
    del user_input[CONF_BACKWASH]
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_PUMP] is None
    assert entry.options[CONF_BACKWASH] is None

    # The live coordinator picks the change up via the update listener.
    await hass.async_block_till_done()
    assert entry.runtime_data.pump_switch is None
    assert entry.runtime_data.backwash_switch is None


async def test_options_flow_delete_zone(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN, data=make_config(), unique_id=DOMAIN, title="x"
    )
    entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "zones_list"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"action": "edit:z1"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_ZONE_NAME: "Doesn't matter",
            CONF_ZONE_ENTITY: ZONE1_ENTITY,
            CONF_ZONE_DURATION: 60,
            CONF_ZONE_ORDER: 1,
            "delete_zone": True,
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    zones = entry.options[CONF_ZONES]
    assert {z["id"] for z in zones} == {"z2"}
