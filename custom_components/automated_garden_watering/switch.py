"""Daily timer enable switch."""
from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_ON
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, STATE_IDLE
from .coordinator import IrrigationCoordinator
from .entity import IrrigationBaseEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: IrrigationCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[SwitchEntity] = [
        DailyTimerSwitch(coordinator),
        DetailsSwitch(coordinator),
    ]
    if coordinator.pump_switch:
        entities.append(ManualPumpSwitch(coordinator))
    async_add_entities(entities)


class DailyTimerSwitch(IrrigationBaseEntity, SwitchEntity):
    _attr_icon = "mdi:timer-outline"

    def __init__(self, coordinator: IrrigationCoordinator) -> None:
        super().__init__(coordinator, "daily_timer", "Daily timer")

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

    _attr_icon = "mdi:information-outline"

    def __init__(self, coordinator: IrrigationCoordinator) -> None:
        super().__init__(coordinator, "details_visible", "Show details")

    @property
    def is_on(self) -> bool:
        return self.coordinator.details_visible

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_details_visible(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_details_visible(False)


class ManualPumpSwitch(IrrigationBaseEntity, SwitchEntity):
    """Direct manual control of the well pump (e.g. to feed a garden hose).

    Mirrors the configured pump switch. Turning it OFF is blocked while an
    irrigation queue / backwash is running, so manual use can't break the
    'pump must be on before any valve' safety rule. Use 'Water all' a second
    time to stop an active run.
    """

    _attr_icon = "mdi:water-pump"

    def __init__(self, coordinator: IrrigationCoordinator) -> None:
        super().__init__(coordinator, "manual_pump", "Pump (manual)")

    @property
    def is_on(self) -> bool:
        state = self.hass.states.get(self.coordinator.pump_switch)
        return bool(state and state.state == STATE_ON)

    @property
    def extra_state_attributes(self) -> dict:
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
