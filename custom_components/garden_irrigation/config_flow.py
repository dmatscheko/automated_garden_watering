"""Config & options flow for Garden Irrigation."""
from __future__ import annotations

import uuid
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    CONF_BACKWASH,
    CONF_BACKWASH_DELAY,
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
    DEFAULT_BACKWASH_INTERVAL,
    DEFAULT_BACKWASH_RUNTIME,
    DEFAULT_BACKWASH_THRESHOLD,
    DEFAULT_DAILY_START,
    DEFAULT_DAILY_TIMER_ENABLED,
    DEFAULT_MULTIPLIER,
    DEFAULT_PUMP_DELAY,
    DEFAULT_ZONE_DURATION,
    DOMAIN,
)

SWITCH_SELECTOR = selector.EntitySelector(
    selector.EntitySelectorConfig(domain="switch")
)

OPTIONAL_SWITCH_SELECTOR = selector.EntitySelector(
    selector.EntitySelectorConfig(domain="switch")
)


def _global_schema(defaults: dict[str, Any]) -> vol.Schema:
    return vol.Schema(
        {
            vol.Optional(
                CONF_PUMP, description={"suggested_value": defaults.get(CONF_PUMP)}
            ): OPTIONAL_SWITCH_SELECTOR,
            vol.Optional(
                CONF_BACKWASH, description={"suggested_value": defaults.get(CONF_BACKWASH)}
            ): OPTIONAL_SWITCH_SELECTOR,
            vol.Required(
                CONF_MULTIPLIER,
                default=defaults.get(CONF_MULTIPLIER, DEFAULT_MULTIPLIER),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(min=0.0, max=10.0, step=0.05, mode="box")
            ),
            vol.Required(
                CONF_PUMP_DELAY,
                default=defaults.get(CONF_PUMP_DELAY, DEFAULT_PUMP_DELAY),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=0, max=600, step=1, mode="box", unit_of_measurement="s"
                )
            ),
            vol.Required(
                CONF_BACKWASH_DELAY,
                default=defaults.get(CONF_BACKWASH_DELAY, DEFAULT_BACKWASH_DELAY),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=0, max=600, step=1, mode="box", unit_of_measurement="s"
                )
            ),
            vol.Required(
                CONF_BACKWASH_RUNTIME,
                default=defaults.get(CONF_BACKWASH_RUNTIME, DEFAULT_BACKWASH_RUNTIME),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=1, max=600, step=1, mode="box", unit_of_measurement="s"
                )
            ),
            vol.Required(
                CONF_BACKWASH_INTERVAL,
                default=defaults.get(CONF_BACKWASH_INTERVAL, DEFAULT_BACKWASH_INTERVAL),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=0, max=24 * 3600, step=30, mode="box", unit_of_measurement="s"
                )
            ),
            vol.Required(
                CONF_BACKWASH_THRESHOLD,
                default=defaults.get(CONF_BACKWASH_THRESHOLD, DEFAULT_BACKWASH_THRESHOLD),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=0, max=24 * 3600, step=10, mode="box", unit_of_measurement="s"
                )
            ),
            vol.Required(
                CONF_DAILY_START,
                default=defaults.get(CONF_DAILY_START, DEFAULT_DAILY_START),
            ): selector.TimeSelector(),
            vol.Required(
                CONF_DAILY_TIMER_ENABLED,
                default=defaults.get(
                    CONF_DAILY_TIMER_ENABLED, DEFAULT_DAILY_TIMER_ENABLED
                ),
            ): selector.BooleanSelector(),
        }
    )


def _zone_schema(zone: dict[str, Any] | None, next_order: int) -> vol.Schema:
    zone = zone or {}
    return vol.Schema(
        {
            vol.Required(
                CONF_ZONE_NAME,
                default=zone.get(CONF_ZONE_NAME, "Zone"),
            ): selector.TextSelector(),
            vol.Required(
                CONF_ZONE_ENTITY,
                description={"suggested_value": zone.get(CONF_ZONE_ENTITY)},
            ): SWITCH_SELECTOR,
            vol.Required(
                CONF_ZONE_DURATION,
                default=zone.get(CONF_ZONE_DURATION, DEFAULT_ZONE_DURATION),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=1, max=24 * 3600, step=1, mode="box", unit_of_measurement="s"
                )
            ),
            vol.Required(
                CONF_ZONE_ORDER,
                default=zone.get(CONF_ZONE_ORDER, next_order),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(min=0, max=999, step=1, mode="box")
            ),
        }
    )


class GardenIrrigationConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Initial setup flow."""

    VERSION = 1

    def __init__(self) -> None:
        self._globals: dict[str, Any] = {}
        self._zones: list[dict[str, Any]] = []

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> "GardenIrrigationOptionsFlow":
        return GardenIrrigationOptionsFlow(config_entry)

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        if user_input is not None:
            self._globals = user_input
            return await self.async_step_zone()
        return self.async_show_form(
            step_id="user",
            data_schema=_global_schema({}),
            description_placeholders={},
        )

    async def async_step_zone(self, user_input: dict[str, Any] | None = None):
        if user_input is not None:
            user_input[CONF_ZONE_ID] = uuid.uuid4().hex
            self._zones.append(user_input)
            # Ask whether to add more
            return await self.async_step_zone_more()
        return self.async_show_form(
            step_id="zone",
            data_schema=_zone_schema(None, next_order=len(self._zones) + 1),
        )

    async def async_step_zone_more(self, user_input: dict[str, Any] | None = None):
        if user_input is not None:
            if user_input.get("add_another"):
                return await self.async_step_zone()
            # Finalize
            data = dict(self._globals)
            data[CONF_ZONES] = self._zones
            await self.async_set_unique_id(DOMAIN)
            self._abort_if_unique_id_configured()
            return self.async_create_entry(title="Garden Irrigation", data=data)
        return self.async_show_form(
            step_id="zone_more",
            data_schema=vol.Schema(
                {vol.Required("add_another", default=False): selector.BooleanSelector()}
            ),
            description_placeholders={"count": str(len(self._zones))},
        )


class GardenIrrigationOptionsFlow(config_entries.OptionsFlow):
    """Options flow: edit globals, add/edit/remove zones."""

    def __init__(self, entry: config_entries.ConfigEntry) -> None:
        self.entry = entry
        merged = dict(entry.data)
        merged.update(entry.options or {})
        self._globals: dict[str, Any] = {
            k: v for k, v in merged.items() if k != CONF_ZONES
        }
        self._zones: list[dict[str, Any]] = [
            dict(z) for z in (merged.get(CONF_ZONES) or [])
        ]
        self._edit_zone_id: str | None = None

    async def async_step_init(self, user_input: dict[str, Any] | None = None):
        return self.async_show_menu(
            step_id="init",
            menu_options=["globals", "zones_list"],
        )

    async def async_step_globals(self, user_input: dict[str, Any] | None = None):
        if user_input is not None:
            self._globals.update(user_input)
            return await self._save_and_exit()
        return self.async_show_form(
            step_id="globals", data_schema=_global_schema(self._globals)
        )

    async def async_step_zones_list(self, user_input: dict[str, Any] | None = None):
        if user_input is not None:
            choice = user_input.get("action")
            if choice == "__add__":
                return await self.async_step_zone_edit()
            if choice and choice.startswith("delete:"):
                zid = choice.split(":", 1)[1]
                self._zones = [z for z in self._zones if z[CONF_ZONE_ID] != zid]
                return await self._save_and_exit()
            if choice and choice.startswith("edit:"):
                self._edit_zone_id = choice.split(":", 1)[1]
                return await self.async_step_zone_edit()
            return await self._save_and_exit()

        options = [{"value": "__add__", "label": "➕ Add a new zone"}]
        for z in sorted(self._zones, key=lambda x: (x.get(CONF_ZONE_ORDER, 0), x.get(CONF_ZONE_NAME, ""))):
            label = f"#{z.get(CONF_ZONE_ORDER, '?')} — {z.get(CONF_ZONE_NAME)} ({z.get(CONF_ZONE_ENTITY)})"
            options.append({"value": f"edit:{z[CONF_ZONE_ID]}", "label": f"✏️ Edit: {label}"})
            options.append({"value": f"delete:{z[CONF_ZONE_ID]}", "label": f"🗑 Delete: {label}"})

        return self.async_show_form(
            step_id="zones_list",
            data_schema=vol.Schema(
                {
                    vol.Required("action"): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=options, mode="list", custom_value=False
                        )
                    )
                }
            ),
        )

    async def async_step_zone_edit(self, user_input: dict[str, Any] | None = None):
        existing = None
        if self._edit_zone_id:
            for z in self._zones:
                if z[CONF_ZONE_ID] == self._edit_zone_id:
                    existing = z
                    break

        if user_input is not None:
            if existing:
                existing.update(user_input)
            else:
                new_zone = dict(user_input)
                new_zone[CONF_ZONE_ID] = uuid.uuid4().hex
                self._zones.append(new_zone)
            self._edit_zone_id = None
            return await self._save_and_exit()

        next_order = max([z.get(CONF_ZONE_ORDER, 0) for z in self._zones] or [0]) + 1
        return self.async_show_form(
            step_id="zone_edit",
            data_schema=_zone_schema(existing, next_order=next_order),
        )

    async def _save_and_exit(self):
        new_options: dict[str, Any] = dict(self._globals)
        new_options[CONF_ZONES] = self._zones
        return self.async_create_entry(title="", data=new_options)
