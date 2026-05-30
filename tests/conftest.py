"""Shared pytest fixtures."""
from __future__ import annotations

from collections.abc import AsyncGenerator, Generator
from datetime import timedelta
from typing import Any

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
    async_mock_service,
)

from custom_components.automated_garden_watering.const import (
    CONF_BACKWASH,
    CONF_BACKWASH_DELAY,
    CONF_BACKWASH_FLUSH_RUNTIME,
    CONF_BACKWASH_INTERVAL,
    CONF_BACKWASH_RUNTIME,
    CONF_BACKWASH_THRESHOLD,
    CONF_PUMP,
    CONF_PUMP_DELAY,
    CONF_ZONE_DURATION,
    CONF_ZONE_ENTITY,
    CONF_ZONE_ID,
    CONF_ZONE_NAME,
    CONF_ZONE_ORDER,
    CONF_ZONES,
    DOMAIN,
)


@pytest.fixture(autouse=True)
def _auto_enable_custom_integrations(
    enable_custom_integrations: Any,
) -> Generator[None, None, None]:
    """Enable loading of custom_components/ in every test."""
    yield


PUMP_ENTITY = "switch.test_pump"
BACKWASH_ENTITY = "switch.test_backwash"
ZONE1_ENTITY = "switch.zone1"
ZONE2_ENTITY = "switch.zone2"


def make_config(
    *,
    pump: str | None = PUMP_ENTITY,
    backwash: str | None = BACKWASH_ENTITY,
    pump_delay: int = 2,
    backwash_delay: int = 2,
    backwash_runtime: int = 3,
    backwash_flush_runtime: int = 2,
    backwash_interval: int = 0,
    # Default high enough that two short zones don't trigger an end-of-queue
    # backwash; tests focused on the backwash path override this.
    backwash_threshold: int = 9999,
    zone_duration: int = 5,
) -> dict[str, Any]:
    """Return a config entry data dict with two zones."""
    return {
        CONF_PUMP: pump,
        CONF_BACKWASH: backwash,
        CONF_PUMP_DELAY: pump_delay,
        CONF_BACKWASH_DELAY: backwash_delay,
        CONF_BACKWASH_RUNTIME: backwash_runtime,
        CONF_BACKWASH_FLUSH_RUNTIME: backwash_flush_runtime,
        CONF_BACKWASH_INTERVAL: backwash_interval,
        CONF_BACKWASH_THRESHOLD: backwash_threshold,
        CONF_ZONES: [
            {
                CONF_ZONE_ID: "z1",
                CONF_ZONE_ENTITY: ZONE1_ENTITY,
                CONF_ZONE_NAME: "Zone 1",
                CONF_ZONE_DURATION: zone_duration,
                CONF_ZONE_ORDER: 1,
            },
            {
                CONF_ZONE_ID: "z2",
                CONF_ZONE_ENTITY: ZONE2_ENTITY,
                CONF_ZONE_NAME: "Zone 2",
                CONF_ZONE_DURATION: zone_duration,
                CONF_ZONE_ORDER: 2,
            },
        ],
    }


@pytest.fixture
def underlying_switch_states(hass: HomeAssistant) -> None:
    """Pre-populate the user switch states the integration references."""
    for ent in (PUMP_ENTITY, BACKWASH_ENTITY, ZONE1_ENTITY, ZONE2_ENTITY):
        hass.states.async_set(ent, "off")


@pytest.fixture
async def setup_integration(
    hass: HomeAssistant, underlying_switch_states: None
) -> AsyncGenerator[MockConfigEntry, None]:
    """Set up the integration with a default 2-zone config."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=make_config(),
        unique_id=DOMAIN,
        title="Automated Garden Watering",
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    yield entry


def register_switch_mocks(hass: HomeAssistant) -> tuple[list, list]:
    """Helper: register switch.turn_on/turn_off mocks AFTER integration setup.

    HA's `switch` domain re-registers its own services during platform setup,
    so any mock registered before that point gets overwritten. Call this after
    `async_config_entries.async_setup` finishes.
    """
    return (
        async_mock_service(hass, "switch", "turn_on"),
        async_mock_service(hass, "switch", "turn_off"),
    )


@pytest.fixture
def mock_switch_services(
    hass: HomeAssistant, setup_integration: MockConfigEntry
) -> tuple[list, list]:
    """Mock switch.turn_on / switch.turn_off, registered AFTER setup_integration."""
    return register_switch_mocks(hass)


async def advance(hass: HomeAssistant, seconds: int) -> None:
    """Fire the 1-second tick `seconds` times, awaiting work between ticks."""
    now = dt_util.utcnow()
    for i in range(1, seconds + 1):
        async_fire_time_changed(hass, now + timedelta(seconds=i))
        await hass.async_block_till_done()
