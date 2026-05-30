"""Tests for parallel-zone execution (max_parallel)."""
from __future__ import annotations

from typing import Any

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

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
    CONF_ZONE_MAX_PARALLEL,
    CONF_ZONE_NAME,
    CONF_ZONE_ORDER,
    CONF_ZONES,
    DOMAIN,
    STATE_WATERING,
)
from custom_components.automated_garden_watering.coordinator import IrrigationCoordinator

from .conftest import (
    BACKWASH_ENTITY,
    PUMP_ENTITY,
    advance,
    register_switch_mocks,
)


def _config(zones: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        CONF_PUMP: PUMP_ENTITY,
        CONF_BACKWASH: BACKWASH_ENTITY,
        CONF_PUMP_DELAY: 1,
        CONF_BACKWASH_DELAY: 1,
        CONF_BACKWASH_RUNTIME: 1,
        CONF_BACKWASH_FLUSH_RUNTIME: 1,
        CONF_BACKWASH_INTERVAL: 0,
        CONF_BACKWASH_THRESHOLD: 9999,
        CONF_ZONES: zones,
    }


def _z(zid: str, *, duration: int, parallel: int, order: int) -> dict[str, Any]:
    return {
        CONF_ZONE_ID: zid,
        CONF_ZONE_ENTITY: f"switch.{zid}",
        CONF_ZONE_NAME: zid.upper(),
        CONF_ZONE_DURATION: duration,
        CONF_ZONE_ORDER: order,
        CONF_ZONE_MAX_PARALLEL: parallel,
    }


@pytest.fixture
async def setup_with(hass: HomeAssistant):
    """Setup helper that takes a zone list and yields the loaded coordinator."""
    created: list[MockConfigEntry] = []

    async def _setup(zones: list[dict[str, Any]]) -> IrrigationCoordinator:
        for z in zones:
            hass.states.async_set(z[CONF_ZONE_ENTITY], "off")
        hass.states.async_set(PUMP_ENTITY, "off")
        hass.states.async_set(BACKWASH_ENTITY, "off")
        entry = MockConfigEntry(
            domain=DOMAIN, data=_config(zones), unique_id=DOMAIN, title="x"
        )
        entry.add_to_hass(hass)
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        register_switch_mocks(hass)
        created.append(entry)
        return entry.runtime_data  # type: ignore[no-any-return]

    yield _setup


async def test_zone_with_parallel_one_runs_alone(
    hass: HomeAssistant, setup_with: Any
) -> None:
    """Default behavior: max_parallel=1 → no zone runs in parallel."""
    coord = await setup_with(
        [
            _z("a", duration=4, parallel=1, order=1),
            _z("b", duration=4, parallel=1, order=2),
        ]
    )
    await coord.async_water_all()
    await advance(hass, 2)  # past pump_delay=1 → first watering tick
    assert coord.rt.state == STATE_WATERING
    assert set(coord.rt.active.keys()) == {"a"}
    assert "b" in coord.rt.queue


async def test_two_parallel_zones_run_together(
    hass: HomeAssistant, setup_with: Any
) -> None:
    """Two zones, both max_parallel=2, queued back-to-back → run together."""
    coord = await setup_with(
        [
            _z("a", duration=4, parallel=2, order=1),
            _z("b", duration=4, parallel=2, order=2),
        ]
    )
    await coord.async_water_all()
    await advance(hass, 2)
    assert coord.rt.state == STATE_WATERING
    assert set(coord.rt.active.keys()) == {"a", "b"}
    assert coord.rt.queue == []


async def test_group_cap_is_min_max_parallel(
    hass: HomeAssistant, setup_with: Any
) -> None:
    """Mixed group: B=2, C=3 → group cap is 2, so only B+C run together."""
    coord = await setup_with(
        [
            _z("a", duration=2, parallel=1, order=1),  # alone
            _z("b", duration=4, parallel=2, order=2),  # joins next group
            _z("c", duration=4, parallel=3, order=3),  # joins B's group (cap=2)
            _z("d", duration=4, parallel=3, order=4),  # cannot join (size would be 3 > B's cap of 2)
        ]
    )
    await coord.async_water_all()
    await advance(hass, 2)
    # First group: just A (max_parallel=1).
    assert set(coord.rt.active.keys()) == {"a"}
    # A finishes after duration=2 ticks → second group forms with B+C.
    await advance(hass, 2)
    assert set(coord.rt.active.keys()) == {"b", "c"}
    # D is still queued (cap of B+C group is 2, D would push it to 3).
    assert "d" in coord.rt.queue


async def test_refill_when_zone_finishes_early(
    hass: HomeAssistant, setup_with: Any
) -> None:
    """When a zone in a parallel group finishes, the next queued zone joins if it fits."""
    coord = await setup_with(
        [
            _z("a", duration=2, parallel=2, order=1),  # shorter, finishes first
            _z("b", duration=8, parallel=2, order=2),
            _z("c", duration=4, parallel=2, order=3),  # should refill A's slot
        ]
    )
    await coord.async_water_all()
    await advance(hass, 2)
    assert set(coord.rt.active.keys()) == {"a", "b"}
    # A's duration was 2, so two more ticks finish it and refill with C.
    await advance(hass, 2)
    assert set(coord.rt.active.keys()) == {"b", "c"}


async def test_tap_joins_active_group_when_fits(
    hass: HomeAssistant, setup_with: Any
) -> None:
    """Tapping a zone with compatible max_parallel during a group adds it directly."""
    coord = await setup_with(
        [
            _z("a", duration=10, parallel=2, order=1),
            _z("b", duration=10, parallel=2, order=2),
        ]
    )
    await coord.async_toggle_zone("a")
    await advance(hass, 2)
    assert set(coord.rt.active.keys()) == {"a"}
    # Tap b while a is running — should join immediately.
    await coord.async_toggle_zone("b")
    assert set(coord.rt.active.keys()) == {"a", "b"}


async def test_tap_does_not_join_when_cap_blocks(
    hass: HomeAssistant, setup_with: Any
) -> None:
    """Tapping a zone whose max_parallel < current group size+1 → enqueued instead."""
    coord = await setup_with(
        [
            _z("a", duration=10, parallel=2, order=1),
            _z("b", duration=10, parallel=2, order=2),
            _z("c", duration=10, parallel=1, order=3),  # can never join a group
        ]
    )
    # Start a+b together.
    await coord.async_toggle_zone("a")
    await coord.async_toggle_zone("b")
    await advance(hass, 2)
    assert set(coord.rt.active.keys()) == {"a", "b"}
    # Tap c — its max_parallel=1 can't survive a group of 2 → enqueued.
    await coord.async_toggle_zone("c")
    assert "c" in coord.rt.queue
    assert "c" not in coord.rt.active


async def test_tap_does_not_bypass_queued_zone(
    hass: HomeAssistant, setup_with: Any
) -> None:
    """A later tap must not jump ahead of a zone already waiting in the queue.

    Scenario: A runs alone (cap 1). B (max=2) is enqueued. Then the user taps
    C (max=2). C would *individually* fit a future cap-2 group, but B is
    queued in front of it — so C must go to the queue tail, not join A's
    group or jump in front of B.
    """
    coord = await setup_with(
        [
            _z("a", duration=10, parallel=1, order=1),
            _z("b", duration=10, parallel=2, order=2),
            _z("c", duration=10, parallel=2, order=3),
        ]
    )
    await coord.async_toggle_zone("a")
    await advance(hass, 2)  # past pump_delay → A running alone
    assert set(coord.rt.active.keys()) == {"a"}

    await coord.async_toggle_zone("b")
    assert coord.rt.queue == ["b"]  # B waits — A's cap is 1
    await coord.async_toggle_zone("c")
    # Adjacency rule: C must go to the tail, NOT skip ahead of B.
    assert coord.rt.queue == ["b", "c"]
    assert "c" not in coord.rt.active


async def test_queue_remaining_with_parallel_groups(
    hass: HomeAssistant, setup_with: Any
) -> None:
    """queue_remaining counts parallel groups by their max duration, not sum."""
    coord = await setup_with(
        [
            _z("a", duration=5, parallel=2, order=1),
            _z("b", duration=8, parallel=2, order=2),  # group with A → 8s
            _z("c", duration=4, parallel=1, order=3),  # alone → 4s
        ]
    )
    await coord.async_water_all()
    await advance(hass, 2)  # past pump_delay; into watering
    # Group 1 running: max(5, 8) - 1 elapsed = 7s remaining.
    # Group 2 queued: 4s.
    # Total ~= 11s. Allow ±2s for tick timing slack.
    remaining = coord.queue_remaining_seconds()
    assert 9 <= remaining <= 13, remaining


async def test_backwash_pauses_and_resumes_all_parallel_zones(
    hass: HomeAssistant, setup_with: Any
) -> None:
    """Backwash pauses every running zone and resumes them all afterwards."""
    coord = await setup_with(
        [
            _z("a", duration=20, parallel=2, order=1),
            _z("b", duration=20, parallel=2, order=2),
        ]
    )
    await coord.async_water_all()
    await advance(hass, 3)  # past pump_delay + 2 watering ticks
    assert set(coord.rt.active.keys()) == {"a", "b"}
    remaining_before = dict(coord.rt.active)
    await coord.async_backwash_now()
    assert coord.rt.paused == remaining_before
    assert coord.rt.active == {}
    # Drive the backwash to completion (4 ticks: fire → pressure → reverse →
    # flush). After the 4th tick the state machine is back in WATERING with
    # the active set restored from `paused`.
    await advance(hass, 4)
    # Both zones must have resumed.
    assert set(coord.rt.active.keys()) == {"a", "b"}
    # And their remaining times should be preserved (no double-drain). Allow
    # the very first tick after resume to have already decremented (depends on
    # whether the resume happened mid-tick or at the end).
    for zid, before in remaining_before.items():
        assert before - 1 <= coord.rt.active[zid] <= before
