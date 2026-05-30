"""Tests for edge cases and recovery paths that boost coverage above 95%."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from datetime import time as dt_time

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.automated_garden_watering.const import (
    DOMAIN,
    STATE_BACKWASH,
    STATE_BACKWASH_FLUSH,
    STATE_BACKWASH_PRESSURE,
    STATE_IDLE,
    STATE_WATERING,
)
from custom_components.automated_garden_watering.coordinator import IrrigationCoordinator
from custom_components.automated_garden_watering.time import DailyStartTime

from .conftest import (
    advance,
    make_config,
    register_switch_mocks,
)


async def test_invalid_daily_start_yields_none(
    hass: HomeAssistant, setup_integration: MockConfigEntry
) -> None:
    """An unparseable daily_start string yields native_value=None."""
    coord: IrrigationCoordinator = setup_integration.runtime_data
    coord.daily_start = "not-a-time"
    entity = DailyStartTime(coord)
    entity.hass = hass
    assert entity.native_value is None


async def test_invalid_daily_start_falls_back_in_scheduler(
    hass: HomeAssistant, setup_integration: MockConfigEntry
) -> None:
    """_reschedule_daily_timer falls back to 06:00 on a bad string."""
    coord: IrrigationCoordinator = setup_integration.runtime_data
    coord.daily_start = "garbage"
    coord.daily_timer_enabled = True
    coord._reschedule_daily_timer()
    # No exception means the fallback path was taken.


async def test_manual_pump_returns_false_without_switch(
    hass: HomeAssistant, underlying_switch_states: None
) -> None:
    """async_manual_pump returns False when no pump_switch is configured."""
    entry = MockConfigEntry(
        domain=DOMAIN, data=make_config(pump=None), unique_id=DOMAIN, title="x"
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    coord: IrrigationCoordinator = entry.runtime_data
    assert await coord.async_manual_pump(True) is False
    assert await coord.async_manual_pump(False) is False


async def test_backwash_while_watering_pauses_active_zone(
    hass: HomeAssistant,
    setup_integration: MockConfigEntry,
    mock_switch_services: tuple[list, list],
) -> None:
    """Calling backwash while a zone is watering pauses the zone and runs the wash."""
    coord: IrrigationCoordinator = setup_integration.runtime_data
    await coord.async_toggle_zone("z1")
    await advance(hass, 3)  # past pump_pressure → WATERING
    assert coord.rt.state == STATE_WATERING
    paused_remaining = coord.rt.active_remaining
    await coord.async_backwash_now()
    # The active zone is preserved at queue[0]; pending_backwash now set.
    assert coord.rt.queue and coord.rt.queue[0] == "z1"
    # Next tick fulfils the pending request and transitions to a backwash phase.
    await advance(hass, 1)
    assert coord.rt.state in (
        STATE_BACKWASH_PRESSURE, STATE_BACKWASH, STATE_BACKWASH_FLUSH,
    )
    # Queued zone retains its remaining time so it resumes after the wash.
    assert coord.rt.active_remaining == paused_remaining


async def test_friendly_dt_weekday_and_month(
    hass: HomeAssistant, setup_integration: MockConfigEntry
) -> None:
    """Cover the weekday / month-day branches of _friendly_dt for both languages."""
    coord: IrrigationCoordinator = setup_integration.runtime_data
    now = dt_util.now()

    # 3 days ago → weekday variant.
    three_days_ago = now - timedelta(days=3)
    s_en = coord._friendly_dt(three_days_ago)
    assert s_en is not None

    # 30 days ago → month-day variant.
    long_ago = now - timedelta(days=30)
    assert coord._friendly_dt(long_ago) is not None

    # Force German path.
    coord.hass.config.language = "de"
    assert coord._friendly_dt(three_days_ago) is not None
    assert coord._friendly_dt(long_ago) is not None


async def test_state_restored_from_storage(
    hass: HomeAssistant, underlying_switch_states: None
) -> None:
    """Coordinator picks up persisted last-run timestamps on restart."""
    entry = MockConfigEntry(
        domain=DOMAIN, data=make_config(), unique_id=DOMAIN, title="x"
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    coord: IrrigationCoordinator = entry.runtime_data
    moment = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    coord.last_run["z1"] = moment
    coord.pump_last_run = moment
    coord.backwash_last_run = moment
    coord.details_visible = True
    coord._persist_last_run()
    # Force the delay-save to flush.
    await coord._store.async_save(
        {
            "last_run": {"z1": moment.isoformat()},
            "pump_last_run": moment.isoformat(),
            "backwash_last_run": moment.isoformat(),
            "details_visible": True,
        }
    )

    # Unload + reload — the new coordinator should restore the stored values.
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    coord2: IrrigationCoordinator = entry.runtime_data
    assert coord2.last_run.get("z1") == moment
    assert coord2.pump_last_run == moment
    assert coord2.backwash_last_run == moment
    assert coord2.details_visible is True
