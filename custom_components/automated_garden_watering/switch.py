"""Daily timer, details toggle, and manual pump switch."""
from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import STATE_ON, EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import AutomatedGardenWateringConfigEntry
from .const import STATE_IDLE
from .coordinator import IrrigationCoordinator
from .entity import IrrigationBaseEntity

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: AutomatedGardenWateringConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    entities: list[SwitchEntity] = [
        DailyTimerSwitch(coordinator),
        DetailsSwitch(coordinator),
    ]
    if coordinator.pump_switch:
        entities.append(ManualPumpSwitch(coordinator))
    if coordinator.pump_switch and coordinator.backwash_switch:
        entities.append(ManualBackwashSwitch(coordinator))
    async_add_entities(entities)


class DailyTimerSwitch(IrrigationBaseEntity, SwitchEntity):
    _attr_translation_key = "daily_timer"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: IrrigationCoordinator) -> None:
        super().__init__(coordinator, "daily_timer")

    @property
    def is_on(self) -> bool:
        return self.coordinator.daily_timer_enabled

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_daily_timer_enabled(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_daily_timer_enabled(False)


class DetailsSwitch(IrrigationBaseEntity, SwitchEntity):
    """UI-only toggle: reveals the details card on the dashboard.

    It has no effect on irrigation; it only drives a `conditional` card so the
    status/timer/multiplier list can be hidden behind the 'Details' button.
    """

    _attr_translation_key = "show_details"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: IrrigationCoordinator) -> None:
        super().__init__(coordinator, "details_visible")

    @property
    def is_on(self) -> bool:
        return self.coordinator.details_visible

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_details_visible(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_details_visible(False)


class ManualBackwashSwitch(IrrigationBaseEntity, SwitchEntity):
    """Enable automatic backwashes during manual pump (hose) operation.

    While on, manual pump runtime counts toward the same `backwash_interval`
    as zone watering; when it elapses, the two-stage wash runs and the pump
    is handed back afterwards. Off (default) keeps backwash a queue-only
    feature. Persisted across restarts.
    """

    _attr_translation_key = "manual_backwash"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: IrrigationCoordinator) -> None:
        super().__init__(coordinator, "manual_backwash")

    @property
    def available(self) -> bool:
        return (
            self.coordinator.pump_switch is not None
            and self.coordinator.backwash_switch is not None
        )

    @property
    def is_on(self) -> bool:
        return self.coordinator.manual_backwash_enabled

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_manual_backwash_enabled(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_manual_backwash_enabled(False)


class ManualPumpSwitch(IrrigationBaseEntity, SwitchEntity):
    """Direct manual control of the well pump (e.g. to feed a garden hose).

    Mirrors the configured pump switch. Manual control is blocked while an
    irrigation queue / backwash is running — the state machine owns the pump
    then, and a manual ON would defeat the pump-off reverse-flow stage of a
    backwash. Use 'Water all' a second time to stop an active run.
    """

    _attr_translation_key = "manual_pump"

    def __init__(self, coordinator: IrrigationCoordinator) -> None:
        super().__init__(coordinator, "manual_pump")

    @property
    def available(self) -> bool:
        return (
            self.coordinator.pump_switch is not None
            and self.hass.states.get(self.coordinator.pump_switch) is not None
        )

    @property
    def is_on(self) -> bool:
        pump = self.coordinator.pump_switch
        if pump is None:
            return False
        state = self.hass.states.get(pump)
        return bool(state and state.state == STATE_ON)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        controlled_by = (
            "automation" if self.coordinator.rt.state != STATE_IDLE else "manual"
        )
        last_run = self.coordinator.pump_last_run
        return {
            "pump_entity": self.coordinator.pump_switch,
            "controlled_by": controlled_by,
            "status": "running" if self.is_on else "idle",
            "last_run": last_run.isoformat() if last_run else None,
            "last_run_friendly": self.coordinator.pump_last_run_friendly(),
        }

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.async_manual_pump(True)
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        ok = await self.coordinator.async_manual_pump(False)
        if not ok:
            # Refused because a run is active; reflect the unchanged state.
            self.async_write_ha_state()
