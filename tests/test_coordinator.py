"""Coordinator state-machine coverage.

Drives the 1-second tick deterministically via async_fire_time_changed so the
queue → pump-pressure → watering → backwash sequence can be verified end-to-end.
"""
from __future__ import annotations

from datetime import time as dt_time
from typing import Any

import pytest
from homeassistant.const import STATE_ON
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.automated_garden_watering.const import (
    BACKWASH_ACTIVE_STATES,
    DOMAIN,
    STATE_BACKWASH,
    STATE_BACKWASH_FLUSH,
    STATE_BACKWASH_PRESSURE,
    STATE_IDLE,
    STATE_PUMP_PRESSURE,
    STATE_WATERING,
)
from custom_components.automated_garden_watering.coordinator import IrrigationCoordinator

from .conftest import (
    BACKWASH_ENTITY,
    PUMP_ENTITY,
    ZONE1_ENTITY,
    ZONE2_ENTITY,
    advance,
    make_config,
    register_switch_mocks,
)


def _last_call(calls: list, entity: str) -> dict[str, Any] | None:
    """Return the most recent call data dict that targeted `entity`."""
    for c in reversed(calls):
        if entity in (c.data.get("entity_id") or []) or c.data.get("entity_id") == entity:
            return c.data
    return None


def _calls_for(calls: list, entity: str) -> list:
    """All calls that targeted `entity`."""
    out = []
    for c in calls:
        eids = c.data.get("entity_id")
        if eids == entity or (isinstance(eids, list) and entity in eids):
            out.append(c)
    return out


async def test_water_all_runs_pump_then_zones(
    hass: HomeAssistant,
    setup_integration: MockConfigEntry,
    mock_switch_services: tuple[list, list],
) -> None:
    """water_all should: turn pump on, build pressure, run zone 1, then zone 2."""
    turn_on, turn_off = mock_switch_services
    coord: IrrigationCoordinator = setup_integration.runtime_data

    await hass.services.async_call(DOMAIN, "water_all", {}, blocking=True)
    assert coord.rt.state == STATE_IDLE  # queued only

    # Tick 1: state advances IDLE → PUMP_PRESSURE, pump turned on.
    await advance(hass, 1)
    assert coord.rt.state == STATE_PUMP_PRESSURE
    assert _last_call(turn_on, PUMP_ENTITY) is not None

    # Wait out pump_delay (2s) → start zone 1.
    await advance(hass, 2)
    assert coord.rt.state == STATE_WATERING
    assert coord.active_zone_id() == "z1"
    assert _last_call(turn_on, ZONE1_ENTITY) is not None

    # Zone 1 finishes after 5s; zone 2 starts (same multiplier).
    await advance(hass, 5)
    assert coord.active_zone_id() == "z2"
    assert _last_call(turn_off, ZONE1_ENTITY) is not None
    assert _last_call(turn_on, ZONE2_ENTITY) is not None

    # Zone 2 finishes.
    await advance(hass, 5)
    assert coord.rt.state == STATE_IDLE
    assert _last_call(turn_off, ZONE2_ENTITY) is not None
    assert _last_call(turn_off, PUMP_ENTITY) is not None


async def test_water_all_pressed_twice_is_emergency_stop(
    hass: HomeAssistant,
    setup_integration: MockConfigEntry,
    mock_switch_services: tuple[list, list],
) -> None:
    """Pressing 'water all' while running aborts immediately (no backwash)."""
    coord: IrrigationCoordinator = setup_integration.runtime_data
    await hass.services.async_call(DOMAIN, "water_all", {}, blocking=True)
    await advance(hass, 4)
    assert coord.rt.state == STATE_WATERING

    await hass.services.async_call(DOMAIN, "water_all", {}, blocking=True)
    assert coord.rt.state == STATE_IDLE
    assert coord.rt.queue == []


async def test_toggle_zone_then_untoggle(
    hass: HomeAssistant,
    setup_integration: MockConfigEntry,
    mock_switch_services: tuple[list, list],
) -> None:
    """Toggling a zone twice removes it from the queue."""
    coord: IrrigationCoordinator = setup_integration.runtime_data
    await coord.async_toggle_zone("z1")
    assert coord.rt.queue == ["z1"]
    await coord.async_toggle_zone("z1")
    assert coord.rt.queue == []


async def test_toggle_active_zone_advances_to_next(
    hass: HomeAssistant,
    setup_integration: MockConfigEntry,
    mock_switch_services: tuple[list, list],
) -> None:
    """Removing the currently-watering zone advances to the next queued zone."""
    coord: IrrigationCoordinator = setup_integration.runtime_data
    await coord.async_toggle_zone("z1")
    await coord.async_toggle_zone("z2")
    await advance(hass, 3)  # past pump pressure
    assert coord.active_zone_id() == "z1"

    await coord.async_toggle_zone("z1")  # remove the currently-active zone
    # Coordinator should immediately start z2 in the same tick boundary.
    await advance(hass, 1)
    assert coord.active_zone_id() == "z2"


async def test_backwash_only_when_configured(
    hass: HomeAssistant, underlying_switch_states: None
) -> None:
    """async_backwash_now is a no-op when no backwash valve is configured."""
    entry = MockConfigEntry(
        domain=DOMAIN, data=make_config(backwash=None), unique_id=DOMAIN, title="x"
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    register_switch_mocks(hass)
    coord: IrrigationCoordinator = entry.runtime_data
    await coord.async_backwash_now()
    assert coord.rt.state == STATE_IDLE


async def test_backwash_from_idle_runs_full_sequence(
    hass: HomeAssistant,
    setup_integration: MockConfigEntry,
    mock_switch_services: tuple[list, list],
) -> None:
    """Backwash from idle: pressure (pump ON) → reverse-flow (pump OFF) → flush (pump ON)."""
    turn_on, turn_off = mock_switch_services
    coord: IrrigationCoordinator = setup_integration.runtime_data

    await coord.async_backwash_now()
    # Immediately enters BACKWASH_PRESSURE (pump on, valves closed).
    assert coord.rt.state == STATE_BACKWASH_PRESSURE
    assert _last_call(turn_on, PUMP_ENTITY) is not None

    # Pressure delay = 2s. After 2 ticks we hit BACKWASH (reverse-flow, pump off).
    await advance(hass, 2)
    assert coord.rt.state == STATE_BACKWASH
    # Pump went off, backwash valve opened.
    assert _last_call(turn_off, PUMP_ENTITY) is not None
    assert _last_call(turn_on, BACKWASH_ENTITY) is not None

    # Reverse-flow = 3s → BACKWASH_FLUSH (pump on again).
    await advance(hass, 3)
    assert coord.rt.state == STATE_BACKWASH_FLUSH

    # Flush = 2s → idle, backwash valve closed, pump off.
    await advance(hass, 2)
    assert coord.rt.state == STATE_IDLE
    assert _last_call(turn_off, BACKWASH_ENTITY) is not None


async def test_mid_cycle_auto_backwash(
    hass: HomeAssistant, underlying_switch_states: None
) -> None:
    """Cumulative-watering interval triggers a backwash mid-zone, then resumes."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=make_config(
            zone_duration=10, backwash_interval=3, pump_delay=1, backwash_delay=1,
            backwash_runtime=1, backwash_flush_runtime=1,
        ),
        unique_id=DOMAIN,
        title="x",
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    register_switch_mocks(hass)
    coord: IrrigationCoordinator = entry.runtime_data

    await coord.async_toggle_zone("z1")
    await advance(hass, 2)  # past pump-pressure
    assert coord.rt.state == STATE_WATERING

    # After 3s of watering, mid-cycle backwash should kick in.
    await advance(hass, 4)
    assert coord.rt.state in (
        STATE_BACKWASH_PRESSURE, STATE_BACKWASH, STATE_BACKWASH_FLUSH,
    )
    # Coordinator preserves z1 in `paused` so it resumes after backwash.
    assert "z1" in coord.rt.paused


async def test_end_of_queue_backwash_above_threshold(
    hass: HomeAssistant, underlying_switch_states: None
) -> None:
    """End-of-queue backwash fires when accumulated watering ≥ threshold."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=make_config(
            zone_duration=3, backwash_threshold=2,
            pump_delay=1, backwash_delay=1, backwash_runtime=1, backwash_flush_runtime=1,
        ),
        unique_id=DOMAIN,
        title="x",
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    register_switch_mocks(hass)
    coord: IrrigationCoordinator = entry.runtime_data

    await coord.async_toggle_zone("z1")
    # 1 (IDLE→PUMP_PRESSURE) + 1 (pump_delay) + 3 (zone) = 5 ticks → zone done,
    # threshold met → backwash starts.
    await advance(hass, 5)
    assert coord.rt.state in (
        STATE_BACKWASH_PRESSURE, STATE_BACKWASH, STATE_BACKWASH_FLUSH,
    )


async def test_end_of_queue_no_backwash_below_threshold(
    hass: HomeAssistant, underlying_switch_states: None
) -> None:
    """Short runs do not trigger the end-of-queue backwash."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=make_config(zone_duration=1, backwash_threshold=100, pump_delay=1),
        unique_id=DOMAIN,
        title="x",
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    register_switch_mocks(hass)
    coord: IrrigationCoordinator = entry.runtime_data

    await coord.async_toggle_zone("z1")
    await advance(hass, 5)
    assert coord.rt.state == STATE_IDLE


async def test_multiplier_scales_duration(
    hass: HomeAssistant,
    setup_integration: MockConfigEntry,
    mock_switch_services: tuple[list, list],
) -> None:
    """The watering multiplier scales zone runtime."""
    coord: IrrigationCoordinator = setup_integration.runtime_data
    await coord.async_set_multiplier(2.0)
    await coord.async_toggle_zone("z1")
    # Tick 1 enters PUMP_PRESSURE; then pump_delay=2 ticks until WATERING starts.
    await advance(hass, 3)
    assert coord.rt.state == STATE_WATERING
    # Zone duration 5 × multiplier 2 = 10s; still watering after 5s.
    await advance(hass, 5)
    assert coord.rt.state == STATE_WATERING
    await advance(hass, 6)
    assert coord.rt.state == STATE_IDLE


async def test_daily_timer_start_persistence(
    hass: HomeAssistant, setup_integration: MockConfigEntry
) -> None:
    """Setting daily_start writes back to entry options."""
    entry = setup_integration
    coord: IrrigationCoordinator = entry.runtime_data
    await coord.async_set_daily_start(dt_time(7, 30, 15))
    assert entry.options["daily_start"] == "07:30:15"
    assert coord.daily_start == "07:30:15"


async def test_daily_timer_enable_persistence(
    hass: HomeAssistant, setup_integration: MockConfigEntry
) -> None:
    entry = setup_integration
    coord: IrrigationCoordinator = entry.runtime_data
    await coord.async_set_daily_timer_enabled(True)
    assert coord.daily_timer_enabled is True
    assert entry.options["daily_timer_enabled"] is True


async def test_manual_pump_refused_while_running(
    hass: HomeAssistant,
    setup_integration: MockConfigEntry,
    mock_switch_services: tuple[list, list],
) -> None:
    """Manual pump-off is refused while a queue is active."""
    coord: IrrigationCoordinator = setup_integration.runtime_data
    await coord.async_toggle_zone("z1")
    await advance(hass, 2)  # PUMP_PRESSURE or WATERING — definitely not idle
    assert coord.rt.state != STATE_IDLE
    refused = await coord.async_manual_pump(False)
    assert refused is False


async def test_manual_pump_on_then_off_when_idle(
    hass: HomeAssistant,
    setup_integration: MockConfigEntry,
    mock_switch_services: tuple[list, list],
) -> None:
    coord: IrrigationCoordinator = setup_integration.runtime_data
    assert await coord.async_manual_pump(True) is True
    assert await coord.async_manual_pump(False) is True


async def test_friendly_dt_formats(
    hass: HomeAssistant, setup_integration: MockConfigEntry
) -> None:
    """Spot-check the friendly date formatter."""
    coord: IrrigationCoordinator = setup_integration.runtime_data
    from datetime import timedelta

    from homeassistant.util import dt as dt_util

    now = dt_util.now()
    assert coord._friendly_dt(now).startswith(("Today", "Heute"))
    assert coord._friendly_dt(now - timedelta(days=1)).startswith(
        ("Yesterday", "Gestern")
    )
    assert coord._friendly_dt(now - timedelta(days=30))  # month-day variant
    assert coord._friendly_dt(None) is None


async def test_details_visible_persists(
    hass: HomeAssistant, setup_integration: MockConfigEntry
) -> None:
    coord: IrrigationCoordinator = setup_integration.runtime_data
    await coord.async_set_details_visible(True)
    assert coord.details_visible is True


async def test_remaining_helpers(
    hass: HomeAssistant,
    setup_integration: MockConfigEntry,
    mock_switch_services: tuple[list, list],
) -> None:
    """current_step_remaining_seconds + queue_remaining_seconds smoke check."""
    coord: IrrigationCoordinator = setup_integration.runtime_data
    assert coord.current_step_remaining_seconds() == 0
    assert coord.queue_remaining_seconds() == 0

    await coord.async_toggle_zone("z1")
    await coord.async_toggle_zone("z2")
    # While idle, queue_remaining_seconds returns 0 by design (no active step yet).
    assert coord.queue_remaining_seconds() == 0
    await advance(hass, 3)  # PUMP_PRESSURE → WATERING with pump_delay=2
    assert coord.rt.state == STATE_WATERING
    assert coord.current_step_remaining_seconds() > 0
    # Now we have an active zone (z1, 5s) + queued zone (z2, 5s) → ~10s left.
    assert coord.queue_remaining_seconds() > 0


async def test_daily_timer_fire_does_not_stop_active_run(
    hass: HomeAssistant,
    setup_integration: MockConfigEntry,
    mock_switch_services: tuple[list, list],
) -> None:
    """A timer-started water_all while busy tops up the queue — no emergency stop."""
    coord: IrrigationCoordinator = setup_integration.runtime_data
    await coord.async_toggle_zone("z1")
    await advance(hass, 3)  # past pump pressure
    assert coord.rt.state == STATE_WATERING
    assert coord.active_zone_id() == "z1"

    await coord.async_water_all(from_timer=True)
    # z1 keeps watering; z2 is appended for the daily run.
    assert coord.rt.state == STATE_WATERING
    assert "z1" in coord.rt.active
    assert coord.rt.queue == ["z2"]
    assert coord.rt.started_by_timer is True


async def test_toggle_paused_zone_cancels_resume(
    hass: HomeAssistant,
    setup_integration: MockConfigEntry,
    mock_switch_services: tuple[list, list],
) -> None:
    """Tapping a zone paused for a backwash cancels it instead of re-queueing it."""
    coord: IrrigationCoordinator = setup_integration.runtime_data
    await coord.async_toggle_zone("z1")
    await advance(hass, 3)
    assert coord.rt.state == STATE_WATERING
    await coord.async_backwash_now()
    assert "z1" in coord.rt.paused

    await coord.async_toggle_zone("z1")
    assert coord.rt.paused == {}
    assert "z1" not in coord.rt.queue

    # The backwash completes and — with nothing left to resume — goes idle.
    await advance(hass, 10)
    assert coord.rt.state == STATE_IDLE
    assert coord.rt.active == {}


async def test_queue_remaining_holds_during_backwash(
    hass: HomeAssistant,
    setup_integration: MockConfigEntry,
    mock_switch_services: tuple[list, list],
) -> None:
    """The queue countdown keeps counting paused zones while a backwash runs."""
    coord: IrrigationCoordinator = setup_integration.runtime_data
    await coord.async_toggle_zone("z1")
    await coord.async_toggle_zone("z2")
    await advance(hass, 3)
    assert coord.rt.state == STATE_WATERING
    before = coord.queue_remaining_seconds()

    await coord.async_backwash_now()
    await advance(hass, 1)  # pending request honored → backwash phase
    assert coord.rt.state in BACKWASH_ACTIVE_STATES
    during = coord.queue_remaining_seconds()
    # Holds steady (paused z1 still counted) instead of collapsing to z2 only.
    assert during >= before - 1


async def test_manual_pump_refused_during_backwash(
    hass: HomeAssistant,
    setup_integration: MockConfigEntry,
    mock_switch_services: tuple[list, list],
) -> None:
    """Manual pump control (both directions) is refused during a backwash."""
    coord: IrrigationCoordinator = setup_integration.runtime_data
    await coord.async_backwash_now()
    assert coord.rt.state == STATE_BACKWASH_PRESSURE
    assert await coord.async_manual_pump(True) is False
    assert await coord.async_manual_pump(False) is False


async def test_pending_backwash_without_valve_resumes_paused_zones(
    hass: HomeAssistant,
    setup_integration: MockConfigEntry,
    mock_switch_services: tuple[list, list],
) -> None:
    """Un-configuring the backwash valve while a wash is pending resumes watering."""
    coord: IrrigationCoordinator = setup_integration.runtime_data
    await coord.async_toggle_zone("z1")
    await advance(hass, 3)
    assert coord.rt.state == STATE_WATERING
    await coord.async_backwash_now()
    assert "z1" in coord.rt.paused

    coord.update_config(make_config(backwash=None))
    await advance(hass, 1)  # pending honored, no valve → resume instead
    assert coord.rt.state == STATE_WATERING
    assert "z1" in coord.rt.active
    assert coord.rt.paused == {}


async def test_hass_stop_mid_run_closes_everything(
    hass: HomeAssistant,
    setup_integration: MockConfigEntry,
    mock_switch_services: tuple[list, list],
) -> None:
    """The HA-stop handler shuts all hardware off when a run is active."""
    turn_on, turn_off = mock_switch_services
    coord: IrrigationCoordinator = setup_integration.runtime_data
    await coord.async_toggle_zone("z1")
    await advance(hass, 3)
    assert coord.rt.state == STATE_WATERING

    await coord._on_hass_stop(None)
    assert coord.rt.state == STATE_IDLE
    assert _last_call(turn_off, ZONE1_ENTITY) is not None
    assert _last_call(turn_off, PUMP_ENTITY) is not None


async def test_startup_sweep_closes_stray_valves(
    hass: HomeAssistant,
    setup_integration: MockConfigEntry,
    mock_switch_services: tuple[list, list],
) -> None:
    """Valves (and then the pump) left 'on' by a crash are closed after startup."""
    turn_on, turn_off = mock_switch_services
    coord: IrrigationCoordinator = setup_integration.runtime_data
    hass.states.async_set(ZONE1_ENTITY, "on")
    hass.states.async_set(PUMP_ENTITY, "on")

    await coord._async_startup_sweep(hass)
    assert _last_call(turn_off, ZONE1_ENTITY) is not None
    assert _last_call(turn_off, PUMP_ENTITY) is not None


async def test_startup_sweep_leaves_manual_pump_alone(
    hass: HomeAssistant,
    setup_integration: MockConfigEntry,
    mock_switch_services: tuple[list, list],
) -> None:
    """Pump on with all valves closed looks like manual (hose) use — keep it."""
    turn_on, turn_off = mock_switch_services
    coord: IrrigationCoordinator = setup_integration.runtime_data
    hass.states.async_set(PUMP_ENTITY, "on")

    await coord._async_startup_sweep(hass)
    assert _last_call(turn_off, PUMP_ENTITY) is None


async def test_full_run_seconds_uses_configured_multiplier(
    hass: HomeAssistant,
    setup_integration: MockConfigEntry,
    mock_switch_services: tuple[list, list],
) -> None:
    """full_run_seconds estimates a complete run with the current multiplier."""
    coord: IrrigationCoordinator = setup_integration.runtime_data
    # Two serial zones of 5s each (max_parallel defaults to 1).
    assert coord.full_run_seconds() == 10
    await coord.async_set_multiplier(2.0)
    assert coord.full_run_seconds() == 20


async def test_manual_pump_backwash_interval(
    hass: HomeAssistant, underlying_switch_states: None
) -> None:
    """Manual pump use triggers an interval backwash and hands the pump back."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=make_config(
            backwash_interval=3, backwash_delay=2,
            backwash_runtime=3, backwash_flush_runtime=2,
        ),
        unique_id=DOMAIN,
        title="x",
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    turn_on, turn_off = register_switch_mocks(hass)
    coord: IrrigationCoordinator = entry.runtime_data

    await coord.async_set_manual_backwash_enabled(True)
    hass.states.async_set(PUMP_ENTITY, "on")  # manual (hose) use

    # 3 ticks of pumping reach the interval → the wash starts.
    await advance(hass, 3)
    assert coord.rt.state == STATE_BACKWASH_PRESSURE
    assert coord.rt.manual_pump_backwash is True

    # Drive the wash to completion: pressure(2) + reverse(3) + flush(2).
    await advance(hass, 7)
    assert coord.rt.state == STATE_IDLE
    # The pump was turned off exactly once — for the reverse-flow phase — and
    # NOT again at the end: manual operation continues.
    assert len(_calls_for(turn_off, PUMP_ENTITY)) == 1
    assert _last_call(turn_off, BACKWASH_ENTITY) is not None


async def test_manual_pump_no_backwash_when_disabled(
    hass: HomeAssistant, underlying_switch_states: None
) -> None:
    """With the toggle off (default), manual pumping never triggers a wash."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=make_config(backwash_interval=3),
        unique_id=DOMAIN,
        title="x",
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    register_switch_mocks(hass)
    coord: IrrigationCoordinator = entry.runtime_data

    hass.states.async_set(PUMP_ENTITY, "on")
    await advance(hass, 10)
    assert coord.rt.state == STATE_IDLE
    assert coord.rt.since_last_backwash == 0


async def test_backwash_button_hands_pump_back_when_enabled(
    hass: HomeAssistant,
    setup_integration: MockConfigEntry,
    mock_switch_services: tuple[list, list],
) -> None:
    """A manual backwash during hose use restores the pump when the toggle is on."""
    turn_on, turn_off = mock_switch_services
    coord: IrrigationCoordinator = setup_integration.runtime_data
    await coord.async_set_manual_backwash_enabled(True)
    hass.states.async_set(PUMP_ENTITY, "on")

    await coord.async_backwash_now()
    assert coord.rt.manual_pump_backwash is True
    # pressure(2) + reverse(3) + flush(2) with conftest timings.
    await advance(hass, 7)
    assert coord.rt.state == STATE_IDLE
    assert len(_calls_for(turn_off, PUMP_ENTITY)) == 1  # reverse flow only
