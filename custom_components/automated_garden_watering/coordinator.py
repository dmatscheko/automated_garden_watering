"""Irrigation coordinator: queue, pump, and backwash state machine."""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import time as dt_time, datetime, timedelta
from typing import Any

from homeassistant.const import SERVICE_TURN_OFF, SERVICE_TURN_ON
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_track_time_change
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


@dataclass
class RuntimeState:
    state: str = STATE_IDLE
    queue: list[str] = field(default_factory=list)  # list of zone ids; queue[0] is active when watering
    active_remaining: int = 0
    phase_remaining: int = 0  # seconds left in current pump/backwash phase
    accumulated_watering: int = 0  # total seconds of watering since queue start
    since_last_backwash: int = 0  # seconds of watering since last backwash
    started_by_timer: bool = False
    pending_backwash: bool = False
    multiplier: float = DEFAULT_MULTIPLIER  # multiplier captured at queue start


class IrrigationCoordinator:
    """Owns the irrigation state machine."""

    def __init__(self, hass: HomeAssistant, entry_id: str, data: dict[str, Any]) -> None:
        self.hass = hass
        self.entry_id = entry_id
        self._data = data
        self._lock = asyncio.Lock()
        self._tick_unsub: CALLBACK_TYPE | None = None
        self._daily_unsub: CALLBACK_TYPE | None = None
        self.rt = RuntimeState()
        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, f"{DOMAIN}.{entry_id}"
        )
        self.last_run: dict[str, datetime] = {}
        self.pump_last_run: datetime | None = None
        self.backwash_last_run: datetime | None = None
        self.details_visible: bool = False
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
        if self.rt.state == STATE_WATERING and self.rt.queue:
            return self.rt.queue[0]
        return None

    def _zone_full_seconds(self, zone_id: str) -> int:
        zone = self.zones.get(zone_id)
        if not zone:
            return 0
        return max(1, int(round(zone.duration * self.rt.multiplier)))

    def current_step_remaining_seconds(self) -> int:
        """Seconds left in the current step.

        Watering -> remaining of the active zone.
        Backwash / pressure phases -> remaining of that phase.
        Idle -> 0.
        """
        rt = self.rt
        if rt.state == STATE_WATERING:
            return max(0, rt.active_remaining)
        if rt.state == STATE_PUMP_PRESSURE or rt.state in BACKWASH_ACTIVE_STATES:
            return max(0, rt.phase_remaining)
        return 0

    def queue_remaining_seconds(self) -> int:
        """Total watering seconds left for the whole queue.

        Excludes backwash time, so the value naturally pauses (stays constant)
        while a backwash is running because the active zone's remaining time is
        frozen during the pause.
        """
        rt = self.rt
        if rt.state == STATE_IDLE or not rt.queue:
            return 0
        total = 0
        # Active / first zone.
        if rt.state == STATE_WATERING:
            total += max(0, rt.active_remaining)
        elif rt.active_remaining > 0:
            # Paused mid-zone (during a backwash) — keep its remaining time.
            total += rt.active_remaining
        else:
            # Not started yet (pump pressure build-up) — full duration.
            total += self._zone_full_seconds(rt.queue[0])
        # Remaining queued zones run their full duration.
        for zid in rt.queue[1:]:
            total += self._zone_full_seconds(zid)
        return total

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

    async def _pump_on(self) -> None:
        """Turn the pump on and record the run time."""
        await self._switch(self.pump_switch, True)
        self.pump_last_run = dt_util.now()
        self._persist_last_run()

    # ---------- lifecycle ----------

    async def async_start(self) -> None:
        from homeassistant.helpers.event import async_track_time_interval

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

        self._tick_unsub = async_track_time_interval(
            self.hass, self._on_tick, timedelta(seconds=1)
        )
        self._reschedule_daily_timer()

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
            },
            2,
        )

    async def async_set_details_visible(self, value: bool) -> None:
        """UI-only toggle for showing the details card on the dashboard."""
        self.details_visible = bool(value)
        self._persist_last_run()
        self._notify()

    def _friendly_dt(self, dt: datetime | None) -> str | None:
        """'Today 14:30' / 'Yesterday 09:00' / 'Mon 09:00' / 'May 03'.

        Localized to German when Home Assistant's language is German.
        """
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
        # Best-effort safe shutdown
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
        # Honor pending backwash request only when safe (not in a backwash phase already)
        if rt.pending_backwash and rt.state in (STATE_WATERING, STATE_PUMP_PRESSURE, STATE_IDLE):
            rt.pending_backwash = False
            await self._enter_backwash_pressure(triggered_during_run=(rt.state != STATE_IDLE))
            return

        if rt.state == STATE_IDLE:
            if rt.queue:
                await self._pump_on()
                rt.state = STATE_PUMP_PRESSURE
                rt.phase_remaining = max(0, self.pump_delay)
                if rt.phase_remaining == 0:
                    await self._start_active_zone()
            return

        if rt.state == STATE_PUMP_PRESSURE:
            rt.phase_remaining -= 1
            if rt.phase_remaining <= 0:
                if rt.queue:
                    await self._start_active_zone()
                else:
                    await self._finish_queue()
            return

        if rt.state == STATE_WATERING:
            rt.active_remaining -= 1
            rt.accumulated_watering += 1
            rt.since_last_backwash += 1
            # Mid-cycle auto backwash
            if (
                self.backwash_interval > 0
                and self.backwash_switch
                and rt.since_last_backwash >= self.backwash_interval
                and rt.active_remaining > 0
            ):
                # Pause active zone, keep it at queue[0]
                if rt.queue:
                    zone = self.zones.get(rt.queue[0])
                    if zone:
                        await self._switch(zone.entity_id, False)
                await self._enter_backwash_pressure(triggered_during_run=True)
                return
            if rt.active_remaining <= 0:
                # Zone finished
                if rt.queue:
                    zone = self.zones.get(rt.queue[0])
                    if zone:
                        await self._switch(zone.entity_id, False)
                    rt.queue.pop(0)
                if rt.queue:
                    await self._start_active_zone()
                else:
                    await self._maybe_end_of_queue_backwash()
            return

        if rt.state == STATE_BACKWASH_PRESSURE:
            # Pump on, valves closed, pressure building up.
            rt.phase_remaining -= 1
            if rt.phase_remaining <= 0:
                await self._begin_reverse_flow()
            return

        if rt.state == STATE_BACKWASH:
            # Reverse-flow phase: pump OFF, backwash valve open. The built-up
            # pressure pushes water backwards through the filter, dislodging dirt.
            rt.phase_remaining -= 1
            if rt.phase_remaining <= 0:
                # Pump back on to flush the dislodged dirt out.
                await self._pump_on()
                rt.state = STATE_BACKWASH_FLUSH
                rt.phase_remaining = max(1, self.backwash_flush_runtime)
            return

        if rt.state == STATE_BACKWASH_FLUSH:
            # Pump ON, backwash valve still open: flush dislodged dirt away.
            rt.phase_remaining -= 1
            if rt.phase_remaining <= 0:
                await self._after_backwash()
            return

    async def _start_active_zone(self) -> None:
        rt = self.rt
        if not rt.queue:
            await self._finish_queue()
            return
        zone = self.zones.get(rt.queue[0])
        if not zone:
            rt.queue.pop(0)
            await self._start_active_zone()
            return
        # If we're resuming a paused zone, active_remaining is already > 0 — keep it.
        if rt.active_remaining <= 0:
            rt.active_remaining = max(1, int(round(zone.duration * rt.multiplier)))
            # Fresh start of this zone (not a backwash resume) -> record last run.
            self.last_run[zone.id] = dt_util.now()
            self._persist_last_run()
        await self._switch(zone.entity_id, True)
        rt.state = STATE_WATERING

    async def _enter_backwash_pressure(self, triggered_during_run: bool) -> None:
        """Start a backwash. Sequence:

        1. Close all zone valves, pump ON, wait `backwash_delay` (build pressure).
        2. Pump OFF + open backwash valve, wait `backwash_runtime` (reverse flow).
        3. Pump ON, wait `backwash_flush_runtime` (flush out dirt).
        4. Close backwash valve, resume watering or finish.
        """
        rt = self.rt
        if not self.backwash_switch:
            # No backwash configured — skip straight back to watering/finish.
            if rt.queue:
                await self._start_active_zone()
            else:
                await self._finish_queue()
            return
        # Build pressure with the pump on and all zone valves closed.
        self.backwash_last_run = dt_util.now()
        self._persist_last_run()
        await self._pump_on()
        await self._close_all_zone_valves()
        rt.state = STATE_BACKWASH_PRESSURE
        rt.phase_remaining = max(0, self.backwash_delay)
        if rt.phase_remaining == 0:
            await self._begin_reverse_flow()

    async def _begin_reverse_flow(self) -> None:
        """Pressure is built: turn the pump OFF, then open the backwash valve.

        With the pump off, the built-up pressure flows backwards through the
        filter (the pump would otherwise fight against that reverse flow).
        """
        rt = self.rt
        await self._switch(self.pump_switch, False)
        await self._switch(self.backwash_switch, True)
        rt.state = STATE_BACKWASH
        rt.phase_remaining = max(1, self.backwash_runtime)

    async def _after_backwash(self) -> None:
        """Close the backwash valve and resume watering, or finish."""
        rt = self.rt
        await self._switch(self.backwash_switch, False)
        rt.since_last_backwash = 0
        if rt.queue:
            # Resumes a paused zone (active_remaining > 0) or starts the next one.
            await self._start_active_zone()
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

    # ---------- public actions (called by entities/services) ----------

    async def async_toggle_zone(self, zone_id: str) -> None:
        async with self._lock:
            if zone_id not in self.zones:
                return
            rt = self.rt
            if zone_id in rt.queue:
                position = rt.queue.index(zone_id)
                if position == 0 and rt.state == STATE_WATERING:
                    # Currently active — stop and advance
                    zone = self.zones.get(zone_id)
                    if zone:
                        await self._switch(zone.entity_id, False)
                    rt.queue.pop(0)
                    rt.active_remaining = 0
                    if rt.queue:
                        await self._start_active_zone()
                    else:
                        await self._maybe_end_of_queue_backwash()
                else:
                    rt.queue.pop(position)
                    if not rt.queue and rt.state == STATE_IDLE:
                        await self._finish_queue()
            else:
                if rt.state == STATE_IDLE:
                    rt.multiplier = self.multiplier
                    rt.started_by_timer = False
                rt.queue.append(zone_id)
        self._notify()

    async def async_water_all(self, from_timer: bool = False) -> None:
        async with self._lock:
            rt = self.rt
            if rt.queue or rt.state != STATE_IDLE:
                # Emergency stop (no backwash)
                await self._close_all_zone_valves()
                await self._switch(self.backwash_switch, False)
                await self._switch(self.pump_switch, False)
                self.rt = RuntimeState()
            else:
                rt.multiplier = self.multiplier
                rt.started_by_timer = from_timer
                rt.queue = self.ordered_zone_ids()
        self._notify()

    async def async_backwash_now(self) -> None:
        async with self._lock:
            if not self.backwash_switch:
                return
            rt = self.rt
            if rt.state in BACKWASH_ACTIVE_STATES:
                return
            # If currently watering, close the active valve and pause
            if rt.state == STATE_WATERING and rt.queue:
                zone = self.zones.get(rt.queue[0])
                if zone:
                    await self._switch(zone.entity_id, False)
            rt.pending_backwash = True
            # If idle, we still need to spin up the pump first; handled in tick via pending flag
            if rt.state == STATE_IDLE:
                # Kick: turn pump on, enter backwash pressure
                rt.pending_backwash = False
                await self._enter_backwash_pressure(triggered_during_run=False)
        self._notify()

    async def async_manual_pump(self, on: bool) -> bool:
        """Manually drive the pump. Returns False if the request was refused.

        Turning the pump off is refused while a queue/backwash is active so the
        safety invariant (pump on before any valve) cannot be broken manually.
        """
        if not self.pump_switch:
            return False
        if not on and self.rt.state != STATE_IDLE:
            _LOGGER.warning(
                "Refusing manual pump-off while irrigation is active (state=%s)",
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
        # Persist in entry data
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
