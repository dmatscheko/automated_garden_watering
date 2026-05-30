"""Setup, unload, runtime_data, and integration-level services."""
from __future__ import annotations

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.automated_garden_watering.const import DOMAIN, STATE_IDLE
from custom_components.automated_garden_watering.coordinator import IrrigationCoordinator

from .conftest import (
    BACKWASH_ENTITY,
    PUMP_ENTITY,
    ZONE1_ENTITY,
    advance,
    make_config,
    register_switch_mocks,
)


async def test_setup_and_unload(
    hass: HomeAssistant, setup_integration: MockConfigEntry
) -> None:
    """The coordinator is stored on entry.runtime_data and cleared on unload."""
    entry = setup_integration
    assert entry.state is ConfigEntryState.LOADED
    assert isinstance(entry.runtime_data, IrrigationCoordinator)
    assert entry.runtime_data.rt.state == STATE_IDLE

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.NOT_LOADED


async def test_service_water_all_starts_queue(
    hass: HomeAssistant,
    setup_integration: MockConfigEntry,
    mock_switch_services: tuple[list, list],
) -> None:
    """`water_all` queues every zone in run order."""
    coord: IrrigationCoordinator = setup_integration.runtime_data

    await hass.services.async_call(DOMAIN, "water_all", {}, blocking=True)
    assert coord.rt.queue == ["z1", "z2"]


async def test_service_stop_emergency_aborts(
    hass: HomeAssistant,
    setup_integration: MockConfigEntry,
    mock_switch_services: tuple[list, list],
) -> None:
    """`stop` clears the queue when something is running."""
    coord: IrrigationCoordinator = setup_integration.runtime_data
    await hass.services.async_call(DOMAIN, "water_all", {}, blocking=True)
    assert coord.rt.queue

    await hass.services.async_call(DOMAIN, "stop", {}, blocking=True)
    assert coord.rt.queue == []
    assert coord.rt.state == STATE_IDLE


async def test_service_stop_idle_is_noop(
    hass: HomeAssistant, setup_integration: MockConfigEntry
) -> None:
    """`stop` is a no-op when nothing is running."""
    coord: IrrigationCoordinator = setup_integration.runtime_data
    assert coord.rt.state == STATE_IDLE
    await hass.services.async_call(DOMAIN, "stop", {}, blocking=True)
    assert coord.rt.state == STATE_IDLE


async def test_service_water_zone_by_name(
    hass: HomeAssistant,
    setup_integration: MockConfigEntry,
    mock_switch_services: tuple[list, list],
) -> None:
    """`water_zone` resolves the zone by display name (case-insensitive)."""
    coord: IrrigationCoordinator = setup_integration.runtime_data
    await hass.services.async_call(
        DOMAIN, "water_zone", {"zone": "zone 1"}, blocking=True
    )
    assert coord.rt.queue == ["z1"]


async def test_service_water_zone_by_id(
    hass: HomeAssistant, setup_integration: MockConfigEntry
) -> None:
    coord: IrrigationCoordinator = setup_integration.runtime_data
    await hass.services.async_call(
        DOMAIN, "water_zone", {"zone": "z2"}, blocking=True
    )
    assert coord.rt.queue == ["z2"]


async def test_service_water_zone_by_entity_id(
    hass: HomeAssistant, setup_integration: MockConfigEntry
) -> None:
    coord: IrrigationCoordinator = setup_integration.runtime_data
    await hass.services.async_call(
        DOMAIN, "water_zone", {"zone": ZONE1_ENTITY}, blocking=True
    )
    assert coord.rt.queue == ["z1"]


async def test_service_water_zone_unknown_raises(
    hass: HomeAssistant, setup_integration: MockConfigEntry
) -> None:
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN, "water_zone", {"zone": "does-not-exist"}, blocking=True
        )


async def test_service_backwash_runs(
    hass: HomeAssistant,
    setup_integration: MockConfigEntry,
    mock_switch_services: tuple[list, list],
) -> None:
    coord: IrrigationCoordinator = setup_integration.runtime_data
    await hass.services.async_call(DOMAIN, "backwash", {}, blocking=True)
    # State should have moved into a backwash phase (pressure or reverse-flow).
    assert coord.rt.state != STATE_IDLE


async def test_service_backwash_no_valve_raises(
    hass: HomeAssistant, underlying_switch_states: None
) -> None:
    """`backwash` without a configured valve raises ServiceValidationError."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=make_config(backwash=None),
        unique_id=DOMAIN,
        title="Automated Garden Watering",
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    register_switch_mocks(hass)

    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(DOMAIN, "backwash", {}, blocking=True)


async def test_service_calls_without_entry_raises(hass: HomeAssistant) -> None:
    """Calling actions before setup surfaces `not_loaded`."""
    # The services are registered in async_setup which fires on first hass start.
    from custom_components.automated_garden_watering import async_setup

    await async_setup(hass, {})
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(DOMAIN, "water_all", {}, blocking=True)


async def test_zone_button_unavailable_when_switch_missing(
    hass: HomeAssistant, underlying_switch_states: None
) -> None:
    """Zone button reports unavailable if the underlying switch was removed."""
    hass.states.async_remove(ZONE1_ENTITY)
    entry = MockConfigEntry(
        domain=DOMAIN, data=make_config(), unique_id=DOMAIN, title="x"
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    # Find the zone button entity_id (order-renamed to *_zone_1).
    states = [s for s in hass.states.async_all("button") if "zone_1" in s.entity_id]
    assert states, "zone_1 button should exist"
    assert states[0].state == "unavailable"


async def test_options_updated_triggers_reload_when_zones_change(
    hass: HomeAssistant, setup_integration: MockConfigEntry
) -> None:
    """Adding/removing a zone in options causes a reload."""
    entry = setup_integration
    new_data = make_config()
    new_data["zones"] = new_data["zones"][:1]  # drop zone 2
    hass.config_entries.async_update_entry(entry, options=new_data)
    await hass.async_block_till_done()
    # After reload, the coordinator only knows about zone 1.
    assert set(entry.runtime_data.zones.keys()) == {"z1"}


async def test_options_updated_inplace_keeps_state(
    hass: HomeAssistant, setup_integration: MockConfigEntry
) -> None:
    """Changing only globals (e.g. delays) does not reload, so state is preserved."""
    entry = setup_integration
    coord_before = entry.runtime_data
    new_data = make_config(pump_delay=99)
    hass.config_entries.async_update_entry(entry, options=new_data)
    await hass.async_block_till_done()
    assert entry.runtime_data is coord_before
    assert entry.runtime_data.pump_delay == 99
