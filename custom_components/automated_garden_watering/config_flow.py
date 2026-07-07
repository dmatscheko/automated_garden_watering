"""Config & options flow for Automated Garden Watering."""
from __future__ import annotations

import uuid
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.config_entries import ConfigFlowResult
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    CONF_BACKWASH,
    CONF_BACKWASH_DELAY,
    CONF_BACKWASH_FLUSH_RUNTIME,
    CONF_BACKWASH_INTERVAL,
    CONF_BACKWASH_RUNTIME,
    CONF_BACKWASH_THRESHOLD,
    CONF_PUMP,
    CONF_PUMP_DELAY,
    CONF_ZONE_AUTO,
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
    DEFAULT_PUMP_DELAY,
    DEFAULT_ZONE_AUTO,
    DEFAULT_ZONE_DURATION,
    DEFAULT_ZONE_MAX_PARALLEL,
    DOMAIN,
)

# Transient form-only field (not persisted) used to delete a zone from its
# edit screen instead of from the zone list.
CONF_ZONE_DELETE = "delete_zone"

# Optional entity-selector fields: when the user clears them, the key is
# simply absent from user_input, so saving must write an explicit None or the
# old value survives the data/options merge.
OPTIONAL_SWITCH_KEYS = (CONF_PUMP, CONF_BACKWASH)

# Every key managed by the global-settings form (shared by the initial setup,
# the reconfigure flow, and the options flow).
GLOBAL_KEYS = (
    CONF_PUMP,
    CONF_BACKWASH,
    CONF_PUMP_DELAY,
    CONF_BACKWASH_DELAY,
    CONF_BACKWASH_RUNTIME,
    CONF_BACKWASH_FLUSH_RUNTIME,
    CONF_BACKWASH_INTERVAL,
    CONF_BACKWASH_THRESHOLD,
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
                CONF_PUMP_DELAY,
                default=defaults.get(CONF_PUMP_DELAY, DEFAULT_PUMP_DELAY),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=0, max=600, step=1, mode=selector.NumberSelectorMode.BOX, unit_of_measurement="s"
                )
            ),
            vol.Required(
                CONF_BACKWASH_DELAY,
                default=defaults.get(CONF_BACKWASH_DELAY, DEFAULT_BACKWASH_DELAY),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=0, max=600, step=1, mode=selector.NumberSelectorMode.BOX, unit_of_measurement="s"
                )
            ),
            vol.Required(
                CONF_BACKWASH_RUNTIME,
                default=defaults.get(CONF_BACKWASH_RUNTIME, DEFAULT_BACKWASH_RUNTIME),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=1, max=600, step=1, mode=selector.NumberSelectorMode.BOX, unit_of_measurement="s"
                )
            ),
            vol.Required(
                CONF_BACKWASH_FLUSH_RUNTIME,
                default=defaults.get(
                    CONF_BACKWASH_FLUSH_RUNTIME, DEFAULT_BACKWASH_FLUSH_RUNTIME
                ),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=1, max=600, step=1, mode=selector.NumberSelectorMode.BOX, unit_of_measurement="s"
                )
            ),
            vol.Required(
                CONF_BACKWASH_INTERVAL,
                default=defaults.get(CONF_BACKWASH_INTERVAL, DEFAULT_BACKWASH_INTERVAL),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=0, max=24 * 3600, step=30, mode=selector.NumberSelectorMode.BOX, unit_of_measurement="s"
                )
            ),
            vol.Required(
                CONF_BACKWASH_THRESHOLD,
                default=defaults.get(CONF_BACKWASH_THRESHOLD, DEFAULT_BACKWASH_THRESHOLD),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=0, max=24 * 3600, step=10, mode=selector.NumberSelectorMode.BOX, unit_of_measurement="s"
                )
            ),
            # NOTE: watering multiplier, daily start time and daily timer are
            # intentionally NOT here — they are editable as their own entities
            # (number / time / switch), so duplicating them in this dialog would
            # be confusing. Their values still persist via those entities.
        }
    )


def _zone_schema(
    zone: dict[str, Any] | None, next_order: int, allow_delete: bool = False
) -> vol.Schema:
    zone = zone or {}
    schema: dict[Any, Any] = {
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
                min=1, max=24 * 3600, step=1, mode=selector.NumberSelectorMode.BOX, unit_of_measurement="s"
            )
        ),
        vol.Required(
            CONF_ZONE_ORDER,
            default=zone.get(CONF_ZONE_ORDER, next_order),
        ): selector.NumberSelector(
            selector.NumberSelectorConfig(min=0, max=999, step=1, mode=selector.NumberSelectorMode.BOX)
        ),
        vol.Required(
            CONF_ZONE_MAX_PARALLEL,
            default=zone.get(CONF_ZONE_MAX_PARALLEL, DEFAULT_ZONE_MAX_PARALLEL),
        ): selector.NumberSelector(
            selector.NumberSelectorConfig(min=1, max=10, step=1, mode=selector.NumberSelectorMode.BOX)
        ),
        vol.Required(
            CONF_ZONE_AUTO,
            default=zone.get(CONF_ZONE_AUTO, DEFAULT_ZONE_AUTO),
        ): selector.BooleanSelector(),
    }
    if allow_delete:
        # Shown only when editing an existing zone. Tick + submit to remove it.
        schema[vol.Required(CONF_ZONE_DELETE, default=False)] = (
            selector.BooleanSelector()
        )
    return vol.Schema(schema)


class AutomatedGardenWateringConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Initial setup flow."""

    VERSION = 1

    def __init__(self) -> None:
        self._globals: dict[str, Any] = {}
        self._zones: list[dict[str, Any]] = []

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> "AutomatedGardenWateringOptionsFlow":
        return AutomatedGardenWateringOptionsFlow(config_entry)

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            self._globals = user_input
            return await self.async_step_zone()
        return self.async_show_form(
            step_id="user",
            data_schema=_global_schema({}),
        )

    async def async_step_zone(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            user_input[CONF_ZONE_ID] = uuid.uuid4().hex
            self._zones.append(user_input)
            # Ask whether to add more
            return await self.async_step_zone_more()
        return self.async_show_form(
            step_id="zone",
            data_schema=_zone_schema(None, next_order=len(self._zones) + 1),
        )

    async def async_step_zone_more(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            if user_input.get("add_another"):
                return await self.async_step_zone()
            # Finalize
            data = dict(self._globals)
            data[CONF_ZONES] = self._zones
            await self.async_set_unique_id(DOMAIN)
            self._abort_if_unique_id_configured()
            return self.async_create_entry(
                title="Automated Garden Watering", data=data
            )
        return self.async_show_form(
            step_id="zone_more",
            data_schema=vol.Schema(
                {vol.Required("add_another", default=False): selector.BooleanSelector()}
            ),
            description_placeholders={"count": str(len(self._zones))},
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Reconfigure the integration's global settings from the integration card.

        Zones stay in the options flow; this step only re-edits the pump /
        backwash switch selection and timing defaults. Submitting writes back
        into `entry.data` and triggers a reload so the new switches are picked
        up immediately.
        """
        entry = self._get_reconfigure_entry()
        defaults: dict[str, Any] = {**dict(entry.data), **(dict(entry.options) if entry.options else {})}
        if user_input is not None:
            new_data: dict[str, Any] = {**dict(entry.data), **user_input}
            for key in OPTIONAL_SWITCH_KEYS:
                # Cleared selector → key absent → store an explicit None.
                new_data[key] = user_input.get(key) or None
            # The options flow mirrors global keys into entry.options, which
            # would shadow this form's result via the data/options merge —
            # drop them from options so the reconfigured values apply.
            new_options = {
                k: v
                for k, v in dict(entry.options or {}).items()
                if k not in GLOBAL_KEYS
            }
            return self.async_update_reload_and_abort(
                entry, data=new_data, options=new_options
            )
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=_global_schema({k: v for k, v in defaults.items() if k != CONF_ZONES}),
        )


class AutomatedGardenWateringOptionsFlow(config_entries.OptionsFlow):
    """Options flow: edit globals, add/edit/remove zones."""

    def __init__(self, entry: config_entries.ConfigEntry) -> None:
        self.entry = entry
        merged: dict[str, Any] = dict(entry.data)
        if entry.options:
            merged.update(dict(entry.options))
        self._globals: dict[str, Any] = {
            k: v for k, v in merged.items() if k != CONF_ZONES
        }
        self._zones: list[dict[str, Any]] = [
            dict(z) for z in (merged.get(CONF_ZONES) or [])
        ]
        self._edit_zone_id: str | None = None

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return self.async_show_menu(
            step_id="init",
            menu_options=["globals", "zones_list"],
        )

    async def async_step_globals(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            self._globals.update(user_input)
            for key in OPTIONAL_SWITCH_KEYS:
                # Cleared selector → key absent from user_input → write an
                # explicit None so the merge with entry.data can't resurrect
                # the previously configured switch.
                self._globals[key] = user_input.get(key) or None
            return await self._save_and_exit()
        return self.async_show_form(
            step_id="globals", data_schema=_global_schema(self._globals)
        )

    async def async_step_zones_list(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            choice = user_input.get("action")
            if choice == "__add__":
                return await self.async_step_zone_edit()
            if choice and choice.startswith("edit:"):
                self._edit_zone_id = choice.split(":", 1)[1]
                return await self.async_step_zone_edit()
            return await self._save_and_exit()

        # Only edit selections are shown. Deleting a zone is done from inside its
        # edit screen, so an accidental tap here can never remove a zone.
        options: list[selector.SelectOptionDict] = [
            selector.SelectOptionDict(value="__add__", label="➕ Add a new zone")
        ]
        for z in sorted(
            self._zones,
            key=lambda x: (x.get(CONF_ZONE_ORDER, 0), x.get(CONF_ZONE_NAME, "")),
        ):
            label = (
                f"#{z.get(CONF_ZONE_ORDER, '?')} — {z.get(CONF_ZONE_NAME)} "
                f"({z.get(CONF_ZONE_ENTITY)})"
            )
            options.append(
                selector.SelectOptionDict(value=f"edit:{z[CONF_ZONE_ID]}", label=label)
            )

        return self.async_show_form(
            step_id="zones_list",
            data_schema=vol.Schema(
                {
                    vol.Required("action"): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=options, mode=selector.SelectSelectorMode.LIST, custom_value=False
                        )
                    )
                }
            ),
        )

    async def async_step_zone_edit(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        existing = None
        if self._edit_zone_id:
            for z in self._zones:
                if z[CONF_ZONE_ID] == self._edit_zone_id:
                    existing = z
                    break

        if user_input is not None:
            delete = user_input.pop(CONF_ZONE_DELETE, False)
            if existing and delete:
                self._zones = [
                    z for z in self._zones if z[CONF_ZONE_ID] != self._edit_zone_id
                ]
                self._edit_zone_id = None
                return await self._save_and_exit()
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
            data_schema=_zone_schema(
                existing, next_order=next_order, allow_delete=existing is not None
            ),
        )

    async def _save_and_exit(self) -> ConfigFlowResult:
        new_options: dict[str, Any] = dict(self._globals)
        new_options[CONF_ZONES] = self._zones
        return self.async_create_entry(title="", data=new_options)
