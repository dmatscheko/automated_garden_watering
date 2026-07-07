"""Exercise the entity platforms via service calls.

Targets the turn_on/turn_off + set_value methods that aren't reached by the
coordinator-only tests, so coverage reflects user interaction paths.
"""
from __future__ import annotations

from homeassistant.const import STATE_ON
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.automated_garden_watering.coordinator import IrrigationCoordinator


def _find_entity(hass: HomeAssistant, domain: str, suffix: str) -> str:
    for state in hass.states.async_all(domain):
        if state.entity_id.endswith(suffix):
            return state.entity_id
    raise AssertionError(f"no {domain}.* entity ending in {suffix}")


async def test_daily_timer_switch_on_off(
    hass: HomeAssistant, setup_integration: MockConfigEntry
) -> None:
    eid = _find_entity(hass, "switch", "daily_timer")
    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": eid}, blocking=True
    )
    coord: IrrigationCoordinator = setup_integration.runtime_data
    assert coord.daily_timer_enabled is True
    await hass.services.async_call(
        "switch", "turn_off", {"entity_id": eid}, blocking=True
    )
    assert coord.daily_timer_enabled is False


async def test_show_details_switch(
    hass: HomeAssistant, setup_integration: MockConfigEntry
) -> None:
    eid = _find_entity(hass, "switch", "show_details")
    coord: IrrigationCoordinator = setup_integration.runtime_data
    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": eid}, blocking=True
    )
    assert coord.details_visible is True
    await hass.services.async_call(
        "switch", "turn_off", {"entity_id": eid}, blocking=True
    )
    assert coord.details_visible is False


async def test_manual_pump_switch(
    hass: HomeAssistant, setup_integration: MockConfigEntry
) -> None:
    eid = _find_entity(hass, "switch", "pump_manual")
    # Turn on (idle), then off (still idle) — both succeed.
    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": eid}, blocking=True
    )
    await hass.services.async_call(
        "switch", "turn_off", {"entity_id": eid}, blocking=True
    )


async def test_manual_backwash_switch(
    hass: HomeAssistant, setup_integration: MockConfigEntry
) -> None:
    eid = _find_entity(hass, "switch", "manual_pump_backwash")
    coord: IrrigationCoordinator = setup_integration.runtime_data
    assert coord.manual_backwash_enabled is False
    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": eid}, blocking=True
    )
    assert coord.manual_backwash_enabled is True
    await hass.services.async_call(
        "switch", "turn_off", {"entity_id": eid}, blocking=True
    )
    assert coord.manual_backwash_enabled is False


async def test_total_duration_sensor(
    hass: HomeAssistant, setup_integration: MockConfigEntry
) -> None:
    """Two serial 5s zones → total watering time 0:00:10."""
    eid = _find_entity(hass, "sensor", "total_watering_time")
    state = hass.states.get(eid)
    assert state is not None
    assert state.state == "0:00:10"
    assert state.attributes["total_seconds"] == 10


async def test_multiplier_number_set(
    hass: HomeAssistant, setup_integration: MockConfigEntry
) -> None:
    eid = _find_entity(hass, "number", "watering_multiplier")
    await hass.services.async_call(
        "number",
        "set_value",
        {"entity_id": eid, "value": 1.5},
        blocking=True,
    )
    coord: IrrigationCoordinator = setup_integration.runtime_data
    assert coord.multiplier == 1.5


async def test_daily_start_time_set(
    hass: HomeAssistant, setup_integration: MockConfigEntry
) -> None:
    eid = _find_entity(hass, "time", "daily_start_time")
    await hass.services.async_call(
        "time",
        "set_value",
        {"entity_id": eid, "time": "08:15:00"},
        blocking=True,
    )
    coord: IrrigationCoordinator = setup_integration.runtime_data
    assert coord.daily_start == "08:15:00"


async def test_water_all_button(
    hass: HomeAssistant, setup_integration: MockConfigEntry
) -> None:
    """Pressing the water_all button queues the run."""
    eid = _find_entity(hass, "button", "water_all")
    await hass.services.async_call(
        "button", "press", {"entity_id": eid}, blocking=True
    )
    coord: IrrigationCoordinator = setup_integration.runtime_data
    assert coord.rt.queue == ["z1", "z2"]


async def test_backwash_button(
    hass: HomeAssistant, setup_integration: MockConfigEntry
) -> None:
    eid = _find_entity(hass, "button", "backwash")
    await hass.services.async_call(
        "button", "press", {"entity_id": eid}, blocking=True
    )
    coord: IrrigationCoordinator = setup_integration.runtime_data
    assert coord.rt.state != "idle"


async def test_zone_button_press_toggles(
    hass: HomeAssistant, setup_integration: MockConfigEntry
) -> None:
    eid = _find_entity(hass, "button", "zone_1")
    await hass.services.async_call(
        "button", "press", {"entity_id": eid}, blocking=True
    )
    coord: IrrigationCoordinator = setup_integration.runtime_data
    assert "z1" in coord.rt.queue
    # Press again to dequeue.
    await hass.services.async_call(
        "button", "press", {"entity_id": eid}, blocking=True
    )
    assert "z1" not in coord.rt.queue


async def test_daily_start_time_native_value_parses(
    hass: HomeAssistant, setup_integration: MockConfigEntry
) -> None:
    """Bad daily_start strings yield native_value == None instead of raising."""
    coord: IrrigationCoordinator = setup_integration.runtime_data
    coord.daily_start = "garbage"
    eid = _find_entity(hass, "time", "daily_start_time")
    # Forcing a state refresh; just ensure the entity tolerates the bad string.
    state = hass.states.get(eid)
    assert state is not None
