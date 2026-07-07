"""Irrigation coordinator: queue, pump, and backwash state machine.

Supports parallel zones via per-zone `max_parallel`. The currently-running set
is `rt.active` (zone_id → remaining seconds). A zone joins the active set if
*both* the current group cap (= min max_parallel across active zones) and the
candidate's own max_parallel allow one more member. When a zone finishes its
slot is refilled from the queue head as long as the next queued zone fits the
shrunken group; otherwise we wait for the rest of the group to drain, then a
fresh group is formed from a clean cap.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import time as dt_time, datetime, timedelta
from typing import Any

from homeassistant.const import (
    EVENT_HOMEASSISTANT_STOP,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
    STATE_ON,
)
from homeassistant.core import CALLBACK_TYPE, Event, HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import (
    async_track_time_change,
    async_track_time_interval,
)
from homeassistant.helpers.start import async_at_started
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import (
    CONF_BACKWASH,
    BACKWASH_ACTIVE_STATES,
    CONF_BACKWASH_DELAY,
    CONF_BACKWASH_FLUSH_RUNTIME,
    CONF_BACKWASH_INTERVAL,
    CONF_BACKWASH_RUNTIME,
    CONF_BACKWASH_THRESHOLD,
    CONF_DAILY_START,
    CONF_DAILY_TIMER_ENABLED,
    CONF_MULTIPLIER,
    CONF_PUMP,
    CONF_PUMP_DELAY,
    CONF_ZONE_DURATION,
    CONF_ZONE_ENTITY,
    CONF_ZONE_ID,
    CONF_ZONE_MAX_PARALLEL,
    CONF_ZONE_NAME,
    CONF_ZONE_ORDER,
    CONF_ZONES,
    DEFAULT_BACKWASH_DELAY,
    DEFAULT_BACKWASH_FLUSH_RUNTIME,
    DEFAULT_BACKWASH_INTERVAL,
    DEFAULT_BACKWASH_RUNTIME,
    DEFAULT_BACKWASH_THRESHOLD,
    DEFAULT_DAILY_START,
    DEFAULT_DAILY_TIMER_ENABLED,
    DEFAULT_MULTIPLIER,
    DEFAULT_PUMP_DELAY,
    DEFAULT_ZONE_MAX_PARALLEL,
    DOMAIN,
    SIGNAL_UPDATE,
    STATE_BACKWASH,
    STATE_BACKWASH_FLUSH,
    STATE_BACKWASH_PRESSURE,
    STATE_IDLE,
    STATE_PUMP_PRESSURE,
    STATE_WATERING,
)

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1


@dataclass
class Zone:
    id: str
    entity_id: str
    name: str
    duration: int  # seconds
    order: int
    max_parallel: int = DEFAULT_ZONE_MAX_PARALLEL


@dataclass
class RuntimeState:
    state: str = STATE_IDLE
    queue: list[str] = field(default_factory=list)
    # zone_id -> remaining seconds. Zones in `active` have their valve open
    # (unless we're paused for a backwash — then valves are closed but the
    # remaining counter is preserved).
    active: dict[str, int] = field(default_factory=dict)
    # When a backwash interrupts watering, this is the snapshot of remaining
    # seconds we restore on `_after_backwash`.
    paused: dict[str, int] = field(default_factory=dict)
    phase_remaining: int = 0  # seconds left in current pump/backwash phase
    accumulated_watering: int = 0  # watering seconds (≥1 zone running) this queue
    since_last_backwash: int = 0   # watering seconds since the last backwash
    started_by_timer: bool = False
    pending_backwash: bool = False
    multiplier: float = DEFAULT_MULTIPLIER
    pump_pressure_done: bool = False  # True once initial pump_delay has elapsed
    # True while a backwash that was triggered during manual pump (hose) use
    # is running: afterwards the pump is left ON and we return to idle.
    manual_pump_backwash: bool = False


class IrrigationCoordinator:
    """Owns the irrigation state machine."""

    def __init__(self, hass: HomeAssistant, entry_id: str, data: dict[str, Any]) -> None:
        self.hass = hass
        self.entry_id = entry_id
        self._data = data
        self._lock = asyncio.Lock()
        self._tick_unsub: CALLBACK_TYPE | None = None
        self._daily_unsub: CALLBACK_TYPE | None = None
        self._stop_unsub: CALLBACK_TYPE | None = None
        self._started_unsub: CALLBACK_TYPE | None = None
        self.rt = RuntimeState()
        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, f"{DOMAIN}.{entry_id}"
        )
        self.last_run: dict[str, datetime] = {}
        self.pump_last_run: datetime | None = None
        self.backwash_last_run: datetime | None = None
        self.details_visible: bool = False
        # Automatically backwash every `backwash_interval` seconds of manual
        # pump (hose) use, too. User preference, persisted like details_visible.
        self.manual_backwash_enabled: bool = False
        self._reload_config(data)

    # ---------- configuration ----------

    def _reload_config(self, data: dict[str, Any]) -> None:
        self._data = data
        zones_raw = data.get(CONF_ZONES, []) or []
        zones: dict[str, Zone] = {}
        for z in zones_raw:
            zid = z[CONF_ZONE_ID]
            zones[zid] = Zone(
                id=zid,
                entity_id=z[CONF_ZONE_ENTITY],
                name=z[CONF_ZONE_NAME],
                duration=int(z.get(CONF_ZONE_DURATION) or 0),
                order=int(z.get(CONF_ZONE_ORDER) or 0),
                max_parallel=max(
                    1,
                    int(z.get(CONF_ZONE_MAX_PARALLEL) or DEFAULT_ZONE_MAX_PARALLEL),
                ),
            )
        self.zones = zones
        self.pump_switch: str | None = data.get(CONF_PUMP) or None
        self.backwash_switch: str | None = data.get(CONF_BACKWASH) or None
        self.multiplier: float = float(data.get(CONF_MULTIPLIER, DEFAULT_MULTIPLIER))
        self.pump_delay: int = int(data.get(CONF_PUMP_DELAY, DEFAULT_PUMP_DELAY))
        self.backwash_delay: int = int(data.get(CONF_BACKWASH_DELAY, DEFAULT_BACKWASH_DELAY))
        self.backwash_runtime: int = int(data.get(CONF_BACKWASH_RUNTIME, DEFAULT_BACKWASH_RUNTIME))
        self.backwash_flush_runtime: int = int(
            data.get(CONF_BACKWASH_FLUSH_RUNTIME, DEFAULT_BACKWASH_FLUSH_RUNTIME)
        )
        self.backwash_interval: int = int(data.get(CONF_BACKWASH_INTERVAL, DEFAULT_BACKWASH_INTERVAL))
        self.backwash_threshold: int = int(data.get(CONF_BACKWASH_THRESHOLD, DEFAULT_BACKWASH_THRESHOLD))
        self.daily_start: str = data.get(CONF_DAILY_START, DEFAULT_DAILY_START) or DEFAULT_DAILY_START
        self.daily_timer_enabled: bool = bool(data.get(CONF_DAILY_TIMER_ENABLED, DEFAULT_DAILY_TIMER_ENABLED))

    def update_config(self, data: dict[str, Any]) -> None:
        self._reload_config(data)
        self._reschedule_daily_timer()
        self._notify()

    # ---------- helpers ----------

    def ordered_zone_ids(self) -> list[str]:
        return [z.id for z in sorted(self.zones.values(), key=lambda z: (z.order, z.name))]

    def zone_position_in_queue(self, zone_id: str) -> int | None:
        try:
            return self.rt.queue.index(zone_id) + 1
        except ValueError:
            return None

    def active_zone_id(self) -> str | None:
        """Legacy accessor: a single representative running zone or None.

        Returns the running zone with the most remaining seconds — the one that
        the group ends on — so single-zone consumers still see a sensible value
        when multiple zones run in parallel. New code should use
        `active_zone_ids()` instead.
        """
        if self.rt.state != STATE_WATERING or not self.rt.active:
            return None
        return max(self.rt.active.items(), key=lambda kv: kv[1])[0]

    def active_zone_ids(self) -> list[str]:
        """All zones currently being watered, in queue order."""
        return [zid for zid in self.rt.queue if zid in self.rt.active] + [
            zid for zid in self.rt.active if zid not in self.rt.queue
        ]

    def _group_cap(self) -> int:
        """Lowest max_parallel across currently-active zones (∞ if empty)."""
        return min(
            (
                self.zones[zid].max_parallel
                for zid in self.rt.active
                if zid in self.zones
            ),
            default=999,
        )

    def _zone_full_seconds(self, zone_id: str, multiplier: float | None = None) -> int:
        zone = self.zones.get(zone_id)
        if not zone:
            return 0
        mult = self.rt.multiplier if multiplier is None else multiplier
        return max(1, int(round(zone.duration * mult)))

    def current_step_remaining_seconds(self) -> int:
        """Seconds left in the current step.

        Watering -> max remaining of any active zone (i.e., when the running
        set will be empty if nothing refills).
        Backwash / pressure phases -> remaining of that phase.
        Idle -> 0.
        """
        rt = self.rt
        if rt.state == STATE_WATERING and rt.active:
            return max(rt.active.values())
        if rt.state == STATE_PUMP_PRESSURE or rt.state in BACKWASH_ACTIVE_STATES:
            return max(0, rt.phase_remaining)
        return 0

    def queue_remaining_seconds(self) -> int:
        """Total watering seconds left for the whole queue.

        Excludes backwash time. Models parallel groups by simulating group
        formation from the queue head: each group's contribution is the max
        zone duration in that group (they water concurrently). Zones paused
        for a backwash count with their preserved remaining seconds, so the
        countdown holds steady while the wash runs.
        """
        rt = self.rt
        if rt.state == STATE_IDLE:
            return 0

        # Current group: running zones, or the paused snapshot during a
        # backwash. Its parallel runtime is the longest remaining counter.
        current = rt.active or rt.paused
        if current:
            queued = [zid for zid in rt.queue if zid not in current]
            return max(0, max(current.values())) + self._simulate_remaining_groups(
                queued, 0
            )

        # No group has started yet (pump-pressure phase): simulate all groups
        # from the queue head.
        return self._simulate_remaining_groups(rt.queue, 0)

    def _simulate_group_duration(
        self,
        queue: list[str],
        start: int,
        initial_cap: int,
        multiplier: float | None = None,
    ) -> tuple[int, int]:
        """Return (group_max_duration_seconds, next_index_after_group)."""
        if start >= len(queue):
            return 0, start
        first = self.zones.get(queue[start])
        if not first:
            return 0, start + 1
        cap = min(initial_cap, first.max_parallel)
        group_size = 1
        durations = [self._zone_full_seconds(first.id, multiplier)]
        j = start + 1
        while j < len(queue) and group_size < cap:
            cand = self.zones.get(queue[j])
            if cand is None:
                j += 1
                continue
            if group_size + 1 > cand.max_parallel:
                break
            durations.append(self._zone_full_seconds(cand.id, multiplier))
            cap = min(cap, cand.max_parallel)
            group_size += 1
            j += 1
        return max(durations), j

    def _simulate_remaining_groups(
        self, queue: list[str], start: int, multiplier: float | None = None
    ) -> int:
        total = 0
        i = start
        while i < len(queue):
            first = self.zones.get(queue[i])
            if not first:
                i += 1
                continue
            dur, i = self._simulate_group_duration(
                queue, i, first.max_parallel, multiplier
            )
            total += dur
        return total

    def full_run_seconds(self) -> int:
        """Duration of a complete 'Water all' run at the current multiplier.

        Simulates the parallel groups the full zone list would form, so the
        estimate already includes the time saved by zones watering together.
        Excludes the pump-pressure delay and any backwash time.
        """
        return self._simulate_remaining_groups(
            self.ordered_zone_ids(), 0, multiplier=self.multiplier
        )

    @callback
    def _notify(self) -> None:
        async_dispatcher_send(self.hass, f"{SIGNAL_UPDATE}_{self.entry_id}")

    async def _switch(self, entity_id: str | None, on: bool) -> None:
        if not entity_id:
            return
        service = SERVICE_TURN_ON if on else SERVICE_TURN_OFF
        try:
            await self.hass.services.async_call(
                "switch", service, {"entity_id": entity_id}, blocking=False
            )
        except Exception as err:  # noqa: BLE001
            _LOGGER.error("Failed to %s %s: %s", service, entity_id, err)

    async def _close_all_zone_valves(self) -> None:
        for z in self.zones.values():
            await self._switch(z.entity_id, False)

    async def _close_active_valves(self) -> None:
        for zid in list(self.rt.active):
            zone = self.zones.get(zid)
            if zone:
                await self._switch(zone.entity_id, False)

    async def _pump_on(self) -> None:
        await self._switch(self.pump_switch, True)
        self.pump_last_run = dt_util.now()
        self._persist_last_run()

    def _pump_is_on(self) -> bool:
        """Whether the configured pump switch currently reports 'on'."""
        if not self.pump_switch:
            return False
        state = self.hass.states.get(self.pump_switch)
        return bool(state and state.state == STATE_ON)

    # ---------- lifecycle ----------

    async def async_start(self) -> None:
        stored = await self._store.async_load()
        if stored:
            if isinstance(stored.get("last_run"), dict):
                for zid, iso in stored["last_run"].items():
                    parsed = dt_util.parse_datetime(iso)
                    if parsed:
                        self.last_run[zid] = parsed
            if stored.get("pump_last_run"):
                self.pump_last_run = dt_util.parse_datetime(stored["pump_last_run"])
            if stored.get("backwash_last_run"):
                self.backwash_last_run = dt_util.parse_datetime(
                    stored["backwash_last_run"]
                )
            self.details_visible = bool(stored.get("details_visible", False))
            self.manual_backwash_enabled = bool(
                stored.get("manual_backwash_enabled", False)
            )

        self._tick_unsub = async_track_time_interval(
            self.hass, self._on_tick, timedelta(seconds=1)
        )
        self._reschedule_daily_timer()
        # Runtime state is not persisted: if HA stops (or previously crashed)
        # mid-run, nothing would ever close the hardware again. Shut everything
        # off on HA stop, and sweep for stray "on" switches once HA is started.
        self._stop_unsub = self.hass.bus.async_listen_once(
            EVENT_HOMEASSISTANT_STOP, self._on_hass_stop
        )
        self._started_unsub = async_at_started(self.hass, self._async_startup_sweep)

    async def _on_hass_stop(self, _event: Event) -> None:
        """Close all watering hardware if Home Assistant stops mid-run."""
        self._stop_unsub = None  # one-time listener has consumed itself
        async with self._lock:
            rt = self.rt
            if rt.state == STATE_IDLE and not rt.active and not rt.queue:
                return
            _LOGGER.warning(
                "Home Assistant is stopping during irrigation (state=%s); "
                "closing all valves and turning the pump off",
                rt.state,
            )
            await self._finish_queue()

    async def _async_startup_sweep(self, _hass: HomeAssistant) -> None:
        """Close watering hardware left on by a crash / unclean restart.

        After a restart the coordinator is idle, so any zone or backwash valve
        that still reports 'on' was left behind by an interrupted run. The
        pump is only turned off when such a stray valve was found — a pump
        running on its own may be deliberate manual (hose) use.
        """
        async with self._lock:
            rt = self.rt
            if rt.state != STATE_IDLE or rt.active or rt.queue:
                return
            valve_ids = [z.entity_id for z in self.zones.values()]
            if self.backwash_switch:
                valve_ids.append(self.backwash_switch)
            stray = False
            for entity_id in valve_ids:
                state = self.hass.states.get(entity_id)
                if state and state.state == STATE_ON:
                    stray = True
                    _LOGGER.warning(
                        "Closing %s — it was left on by an interrupted run", entity_id
                    )
                    await self._switch(entity_id, False)
            if stray and self.pump_switch:
                pump_state = self.hass.states.get(self.pump_switch)
                if pump_state and pump_state.state == STATE_ON:
                    _LOGGER.warning(
                        "Turning off pump %s — it was left on by an interrupted run",
                        self.pump_switch,
                    )
                    await self._switch(self.pump_switch, False)

    @callback
    def _persist_last_run(self) -> None:
        self._store.async_delay_save(
            lambda: {
                "last_run": {
                    zid: dt.isoformat() for zid, dt in self.last_run.items()
                },
                "pump_last_run": (
                    self.pump_last_run.isoformat() if self.pump_last_run else None
                ),
                "backwash_last_run": (
                    self.backwash_last_run.isoformat()
                    if self.backwash_last_run
                    else None
                ),
                "details_visible": self.details_visible,
                "manual_backwash_enabled": self.manual_backwash_enabled,
            },
            2,
        )

    async def async_set_details_visible(self, value: bool) -> None:
        self.details_visible = bool(value)
        self._persist_last_run()
        self._notify()

    async def async_set_manual_backwash_enabled(self, value: bool) -> None:
        self.manual_backwash_enabled = bool(value)
        self._persist_last_run()
        self._notify()

    def _friendly_dt(self, dt: datetime | None) -> str | None:
        if not dt:
            return None
        local = dt_util.as_local(dt)
        delta_days = (dt_util.now().date() - local.date()).days
        hm = local.strftime("%H:%M")
        de = (self.hass.config.language or "en").lower().startswith("de")
        if delta_days <= 0:
            return f"{'Heute' if de else 'Today'} {hm}"
        if delta_days == 1:
            return f"{'Gestern' if de else 'Yesterday'} {hm}"
        if delta_days < 7:
            if de:
                weekday = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"][local.weekday()]
                return f"{weekday} {hm}"
            return local.strftime("%a %H:%M")
        if de:
            months = [
                "Jan", "Feb", "Mär", "Apr", "Mai", "Jun",
                "Jul", "Aug", "Sep", "Okt", "Nov", "Dez",
            ]
            return f"{local.day:02d}. {months[local.month - 1]}"
        return local.strftime("%b %d")

    def zone_last_run(self, zone_id: str) -> datetime | None:
        return self.last_run.get(zone_id)

    def zone_last_run_friendly(self, zone_id: str) -> str | None:
        return self._friendly_dt(self.last_run.get(zone_id))

    def pump_last_run_friendly(self) -> str | None:
        return self._friendly_dt(self.pump_last_run)

    def backwash_last_run_friendly(self) -> str | None:
        return self._friendly_dt(self.backwash_last_run)

    async def async_stop(self) -> None:
        if self._tick_unsub:
            self._tick_unsub()
            self._tick_unsub = None
        if self._daily_unsub:
            self._daily_unsub()
            self._daily_unsub = None
        if self._stop_unsub:
            self._stop_unsub()
            self._stop_unsub = None
        if self._started_unsub:
            self._started_unsub()
            self._started_unsub = None
        await self._close_all_zone_valves()
        await self._switch(self.backwash_switch, False)
        await self._switch(self.pump_switch, False)

    # ---------- daily timer ----------

    def _reschedule_daily_timer(self) -> None:
        if self._daily_unsub:
            self._daily_unsub()
            self._daily_unsub = None
        if not self.daily_timer_enabled:
            return
        try:
            parts = [int(p) for p in self.daily_start.split(":")]
            while len(parts) < 3:
                parts.append(0)
            hh, mm, ss = parts[0], parts[1], parts[2]
            # dt_time() range-checks for us; out-of-range values (e.g. "25:00")
            # must hit the same fallback as unparseable ones.
            dt_time(hh, mm, ss)
        except (ValueError, IndexError):
            _LOGGER.warning("Invalid daily_start %s, using default", self.daily_start)
            hh, mm, ss = 6, 0, 0
        self._daily_unsub = async_track_time_change(
            self.hass, self._on_daily_fire, hour=hh, minute=mm, second=ss
        )

    async def _on_daily_fire(self, _now: datetime) -> None:
        _LOGGER.info("Daily irrigation timer fired")
        await self.async_water_all(from_timer=True)

    # ---------- tick ----------

    async def _on_tick(self, _now: datetime) -> None:
        async with self._lock:
            await self._tick_locked()
        self._notify()

    async def _tick_locked(self) -> None:
        rt = self.rt
        # Honor pending backwash request when safe.
        if rt.pending_backwash and rt.state in (STATE_WATERING, STATE_PUMP_PRESSURE, STATE_IDLE):
            rt.pending_backwash = False
            await self._enter_backwash_pressure(triggered_during_run=(rt.state != STATE_IDLE))
            return

        if rt.state == STATE_IDLE:
            if rt.queue:
                if not rt.pump_pressure_done:
                    await self._pump_on()
                    rt.state = STATE_PUMP_PRESSURE
                    rt.phase_remaining = max(0, self.pump_delay)
                    if rt.phase_remaining == 0:
                        rt.pump_pressure_done = True
                        await self._start_group_from_queue()
                else:
                    # Subsequent groups within the same queue start without pump delay.
                    await self._start_group_from_queue()
            elif (
                self.manual_backwash_enabled
                and self.backwash_switch
                and self.backwash_interval > 0
                and self._pump_is_on()
            ):
                # Manual pump (hose) use: the filter sees the same water flow
                # as a zone run, so count it toward the backwash interval and
                # wash automatically. The pump is restored afterwards.
                rt.since_last_backwash += 1
                if rt.since_last_backwash >= self.backwash_interval:
                    rt.manual_pump_backwash = True
                    await self._enter_backwash_pressure(triggered_during_run=False)
            return

        if rt.state == STATE_PUMP_PRESSURE:
            rt.phase_remaining -= 1
            if rt.phase_remaining <= 0:
                rt.pump_pressure_done = True
                if rt.queue:
                    await self._start_group_from_queue()
                else:
                    await self._finish_queue()
            return

        if rt.state == STATE_WATERING:
            # Decrement each active zone, finish those that hit 0.
            rt.accumulated_watering += 1
            rt.since_last_backwash += 1
            finished: list[str] = []
            for zid in list(rt.active):
                rt.active[zid] -= 1
                if rt.active[zid] <= 0:
                    finished.append(zid)
            for zid in finished:
                zone = self.zones.get(zid)
                if zone:
                    await self._switch(zone.entity_id, False)
                del rt.active[zid]
                if zid in rt.queue:
                    rt.queue.remove(zid)

            # Mid-cycle auto-backwash trigger (only when at least one zone is
            # still running so we have something to resume).
            if (
                self.backwash_interval > 0
                and self.backwash_switch
                and rt.since_last_backwash >= self.backwash_interval
                and rt.active
            ):
                await self._close_active_valves()
                rt.paused = dict(rt.active)
                rt.active.clear()
                await self._enter_backwash_pressure(triggered_during_run=True)
                return

            # Refill any free slots from the queue head, respecting the cap.
            await self._refill_active_set()

            if not rt.active and not rt.queue:
                await self._maybe_end_of_queue_backwash()
                return
            if not rt.active and rt.queue:
                # Active group ended but queued zones remain (none could join
                # the previous cap). Form a fresh group.
                await self._start_group_from_queue()
            return

        if rt.state == STATE_BACKWASH_PRESSURE:
            rt.phase_remaining -= 1
            if rt.phase_remaining <= 0:
                await self._begin_reverse_flow()
            return

        if rt.state == STATE_BACKWASH:
            rt.phase_remaining -= 1
            if rt.phase_remaining <= 0:
                await self._pump_on()
                rt.state = STATE_BACKWASH_FLUSH
                rt.phase_remaining = max(1, self.backwash_flush_runtime)
            return

        if rt.state == STATE_BACKWASH_FLUSH:
            rt.phase_remaining -= 1
            if rt.phase_remaining <= 0:
                await self._after_backwash()
            return

    async def _start_group_from_queue(self) -> None:
        """Pull zones from the queue head into rt.active, forming a new group."""
        rt = self.rt
        if not rt.queue:
            await self._finish_queue()
            return
        # Snapshot the queue order — `_open_zone` removes opened zones from
        # rt.queue, so iterating by index against the live queue would skip
        # entries.
        queue_snapshot = list(rt.queue)
        # First zone in queue seeds the group.
        head = self.zones.get(queue_snapshot[0])
        if not head:
            if queue_snapshot[0] in rt.queue:
                rt.queue.remove(queue_snapshot[0])
            await self._start_group_from_queue()
            return
        cap = head.max_parallel
        await self._open_zone(head)
        # Add followers while they fit.
        for zid in queue_snapshot[1:]:
            if len(rt.active) >= cap:
                break
            cand = self.zones.get(zid)
            if cand is None:
                if zid in rt.queue:
                    rt.queue.remove(zid)
                continue
            if len(rt.active) + 1 > cand.max_parallel:
                break
            cap = min(cap, cand.max_parallel)
            await self._open_zone(cand)
        rt.state = STATE_WATERING

    async def _open_zone(self, zone: Zone) -> None:
        """Open one zone's valve, register it as active, and record last_run."""
        rt = self.rt
        if zone.id not in rt.active:
            rt.active[zone.id] = max(1, int(round(zone.duration * rt.multiplier)))
            self.last_run[zone.id] = dt_util.now()
            self._persist_last_run()
        await self._switch(zone.entity_id, True)
        # Remove from queue (where queueing model lives) — the active dict is
        # the source of truth while running.
        if zone.id in rt.queue:
            rt.queue.remove(zone.id)

    async def _refill_active_set(self) -> None:
        """After a zone finishes, try to pull queued zones into the running set.

        Respects the current group cap (min max_parallel among still-running
        zones) and the candidate's own max_parallel.
        """
        rt = self.rt
        while rt.queue and rt.active:
            cap = self._group_cap()
            if len(rt.active) >= cap:
                return
            cand = self.zones.get(rt.queue[0])
            if cand is None:
                rt.queue.pop(0)
                continue
            if len(rt.active) + 1 > cand.max_parallel:
                return
            await self._open_zone(cand)

    async def _enter_backwash_pressure(self, triggered_during_run: bool) -> None:
        """Start a backwash. Sequence:

        1. Close all zone valves, pump ON, wait `backwash_delay`.
        2. Pump OFF + open backwash valve, wait `backwash_runtime` (reverse).
        3. Pump ON, wait `backwash_flush_runtime` (flush).
        4. Close backwash valve, resume any paused zones or finish.
        """
        rt = self.rt
        if not self.backwash_switch:
            # Backwash valve no longer configured (e.g. cleared in options
            # mid-run): skip the wash but keep watering — resume any paused
            # zones or continue with the queue.
            if rt.paused:
                await self._resume_paused()
            elif rt.queue:
                await self._start_group_from_queue()
            else:
                await self._finish_queue()
            return
        self.backwash_last_run = dt_util.now()
        self._persist_last_run()
        await self._pump_on()
        await self._close_all_zone_valves()
        rt.state = STATE_BACKWASH_PRESSURE
        rt.phase_remaining = max(0, self.backwash_delay)
        if rt.phase_remaining == 0:
            await self._begin_reverse_flow()

    async def _begin_reverse_flow(self) -> None:
        rt = self.rt
        await self._switch(self.pump_switch, False)
        await self._switch(self.backwash_switch, True)
        rt.state = STATE_BACKWASH
        rt.phase_remaining = max(1, self.backwash_runtime)

    async def _resume_paused(self) -> None:
        """Re-open every paused zone and continue watering where it left off."""
        rt = self.rt
        for zid, remaining in rt.paused.items():
            zone = self.zones.get(zid)
            if zone:
                rt.active[zid] = remaining
                await self._switch(zone.entity_id, True)
        rt.paused.clear()
        if rt.active:
            rt.state = STATE_WATERING
            await self._refill_active_set()
        elif rt.queue:
            # All paused zones vanished from the config — carry on with the queue.
            await self._start_group_from_queue()
        else:
            await self._finish_queue()

    async def _after_backwash(self) -> None:
        rt = self.rt
        await self._switch(self.backwash_switch, False)
        rt.since_last_backwash = 0
        if rt.paused:
            rt.manual_pump_backwash = False
            await self._resume_paused()
        elif rt.queue:
            # A queue was started during the wash — it takes over from here.
            rt.manual_pump_backwash = False
            await self._start_group_from_queue()
        elif rt.manual_pump_backwash:
            # Wash was triggered during manual pump (hose) use: the flush
            # phase already turned the pump back on — leave it running and
            # return to idle so the manual session continues seamlessly.
            self.rt = RuntimeState()
        else:
            await self._finish_queue()

    async def _maybe_end_of_queue_backwash(self) -> None:
        rt = self.rt
        should = self.backwash_switch and (
            rt.started_by_timer or rt.accumulated_watering >= self.backwash_threshold
        )
        if should:
            await self._enter_backwash_pressure(triggered_during_run=False)
        else:
            await self._finish_queue()

    async def _finish_queue(self) -> None:
        await self._close_all_zone_valves()
        await self._switch(self.backwash_switch, False)
        await self._switch(self.pump_switch, False)
        self.rt = RuntimeState()

    # ---------- public actions ----------

    async def async_toggle_zone(self, zone_id: str) -> None:
        async with self._lock:
            if zone_id not in self.zones:
                return
            rt = self.rt
            zone = self.zones[zone_id]
            # Currently running? Turn it off and remove from active set.
            if zone_id in rt.active:
                await self._switch(zone.entity_id, False)
                del rt.active[zone_id]
                if not rt.active and not rt.queue:
                    await self._maybe_end_of_queue_backwash()
                elif not rt.active and rt.queue:
                    await self._start_group_from_queue()
                else:
                    # Other zones still running; try to refill.
                    await self._refill_active_set()
            # Paused for a backwash? Cancel its scheduled resume (the valve is
            # already closed). The backwash itself carries on.
            elif zone_id in rt.paused:
                del rt.paused[zone_id]
            # Queued (but not active)? Dequeue.
            elif zone_id in rt.queue:
                rt.queue.remove(zone_id)
                if not rt.queue and not rt.active and rt.state == STATE_IDLE:
                    await self._finish_queue()
            else:
                # Newly added — always append to the queue. The unified
                # `_refill_active_set` then promotes from the queue head if and
                # only if (a) the running group has slack and (b) the queue
                # head fits. That keeps tap-to-add adjacency-honoring: a zone
                # already waiting in the queue is never bypassed by a later
                # tap, even if the tap on its own would fit the running group.
                if rt.state == STATE_IDLE:
                    rt.multiplier = self.multiplier
                    rt.started_by_timer = False
                rt.queue.append(zone_id)
                if rt.state == STATE_WATERING and rt.active:
                    await self._refill_active_set()
        self._notify()

    async def async_water_all(self, from_timer: bool = False) -> None:
        async with self._lock:
            rt = self.rt
            busy = bool(rt.queue or rt.active or rt.paused or rt.state != STATE_IDLE)
            if not busy:
                rt.multiplier = self.multiplier
                rt.started_by_timer = from_timer
                rt.queue = self.ordered_zone_ids()
            elif from_timer:
                # The daily timer must never act as an emergency stop. Top up
                # the queue with every zone not already running / queued /
                # paused, so the scheduled watering still happens after the
                # current run.
                rt.started_by_timer = True
                skip = set(rt.queue) | set(rt.active) | set(rt.paused)
                rt.queue.extend(
                    zid for zid in self.ordered_zone_ids() if zid not in skip
                )
                if rt.state == STATE_WATERING and rt.active:
                    await self._refill_active_set()
            else:
                # Emergency stop (no backwash).
                await self._close_all_zone_valves()
                await self._switch(self.backwash_switch, False)
                await self._switch(self.pump_switch, False)
                self.rt = RuntimeState()
        self._notify()

    async def async_backwash_now(self) -> None:
        async with self._lock:
            if not self.backwash_switch:
                return
            rt = self.rt
            if rt.state in BACKWASH_ACTIVE_STATES:
                return
            # If currently watering, pause every active valve.
            if rt.state == STATE_WATERING and rt.active:
                await self._close_active_valves()
                rt.paused = dict(rt.active)
                rt.active.clear()
            rt.pending_backwash = True
            if rt.state == STATE_IDLE:
                rt.pending_backwash = False
                # With the manual-pump-backwash option on, a wash started
                # while the pump is in manual (hose) use hands the pump back
                # afterwards instead of shutting everything off.
                rt.manual_pump_backwash = (
                    self.manual_backwash_enabled and self._pump_is_on()
                )
                await self._enter_backwash_pressure(triggered_during_run=False)
        self._notify()

    async def async_manual_pump(self, on: bool) -> bool:
        if not self.pump_switch:
            return False
        if self.rt.state != STATE_IDLE:
            # The state machine owns the pump while a run or backwash is
            # active. Turning it off would starve open valves; turning it on
            # would defeat the pump-off reverse-flow phase of a backwash.
            _LOGGER.warning(
                "Refusing manual pump control while irrigation is active (state=%s)",
                self.rt.state,
            )
            return False
        if on:
            await self._pump_on()
        else:
            await self._switch(self.pump_switch, False)
        self._notify()
        return True

    async def async_set_multiplier(self, value: float) -> None:
        self.multiplier = max(0.0, float(value))
        entries = self.hass.config_entries.async_entries(DOMAIN)
        for entry in entries:
            if entry.entry_id == self.entry_id:
                new_opts = dict(entry.options or {})
                new_opts[CONF_MULTIPLIER] = self.multiplier
                self.hass.config_entries.async_update_entry(entry, options=new_opts)
                break
        self._notify()

    async def async_set_daily_start(self, value: dt_time) -> None:
        self.daily_start = value.strftime("%H:%M:%S")
        entries = self.hass.config_entries.async_entries(DOMAIN)
        for entry in entries:
            if entry.entry_id == self.entry_id:
                new_opts = dict(entry.options or {})
                new_opts[CONF_DAILY_START] = self.daily_start
                self.hass.config_entries.async_update_entry(entry, options=new_opts)
                break
        self._reschedule_daily_timer()
        self._notify()

    async def async_set_daily_timer_enabled(self, enabled: bool) -> None:
        self.daily_timer_enabled = bool(enabled)
        entries = self.hass.config_entries.async_entries(DOMAIN)
        for entry in entries:
            if entry.entry_id == self.entry_id:
                new_opts = dict(entry.options or {})
                new_opts[CONF_DAILY_TIMER_ENABLED] = self.daily_timer_enabled
                self.hass.config_entries.async_update_entry(entry, options=new_opts)
                break
        self._reschedule_daily_timer()
        self._notify()
