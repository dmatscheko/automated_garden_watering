"""Config & options flow for Automated Garden Watering."""
from __future__ import annotations

import json
import logging
import os
import uuid
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
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
    DEFAULT_PUMP_DELAY,
    DEFAULT_ZONE_DURATION,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

# The export button writes one of these filenames to <config>/. The restore
# step looks for both, so users who exported from the v0.x "Garden Irrigation"
# integration can import their backup directly.
DEFAULT_EXPORT_FILENAMES = (
    "automated_garden_watering_export.json",
    "garden_irrigation_export.json",
)
EXPORT_SCHEMA_VERSION = 1
# Marker the restore step writes into entry.data; __init__.py consumes it on
# setup to seed the integration's Store with the migrated last-run history.
IMPORT_STATE_KEY = "__import_state__"

# Transient form-only field (not persisted) used to delete a zone from its
# edit screen instead of from the zone list.
CONF_ZONE_DELETE = "delete_zone"

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
                CONF_BACKWASH_FLUSH_RUNTIME,
                default=defaults.get(
                    CONF_BACKWASH_FLUSH_RUNTIME, DEFAULT_BACKWASH_FLUSH_RUNTIME
                ),
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
        self._import_payload: dict[str, Any] | None = None

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> "AutomatedGardenWateringOptionsFlow":
        return AutomatedGardenWateringOptionsFlow(config_entry)

    # ---------- entry point: pick "new setup" or "import from backup" ----------

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        return self.async_show_menu(
            step_id="user",
            menu_options=["new_setup", "restore"],
        )

    # ---------- new setup ----------

    async def async_step_new_setup(self, user_input: dict[str, Any] | None = None):
        if user_input is not None:
            self._globals = user_input
            return await self.async_step_zone()
        return self.async_show_form(
            step_id="new_setup",
            data_schema=_global_schema({}),
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

    # ---------- import from backup ----------

    async def async_step_restore(self, user_input: dict[str, Any] | None = None):
        """Ask the user where the backup file lives, then validate it."""
        default_path = self._guess_export_path()
        errors: dict[str, str] = {}

        if user_input is not None:
            path = user_input["path"]
            payload, err = await self.hass.async_add_executor_job(
                _read_export_file, path
            )
            if err:
                errors["base"] = err
            elif payload is None:
                errors["base"] = "invalid_format"
            else:
                self._import_payload = payload
                self._import_payload["__source_path__"] = path
                return await self.async_step_restore_confirm()

        return self.async_show_form(
            step_id="restore",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        "path",
                        default=default_path,
                    ): selector.TextSelector(),
                }
            ),
            errors=errors,
            description_placeholders={
                "default_path": default_path or "(none found)",
            },
        )

    async def async_step_restore_confirm(
        self, user_input: dict[str, Any] | None = None
    ):
        """Show a summary of the imported file and create the entry on confirm."""
        if self._import_payload is None:
            return await self.async_step_restore()

        config = self._import_payload.get("config") or {}
        state = self._import_payload.get("state") or {}
        zones = config.get(CONF_ZONES) or []

        if user_input is not None:
            await self.async_set_unique_id(DOMAIN)
            self._abort_if_unique_id_configured()
            data = dict(config)
            data[IMPORT_STATE_KEY] = state  # __init__.py consumes this on setup
            return self.async_create_entry(
                title="Automated Garden Watering", data=data
            )

        zone_lines = "\n".join(
            f"  • #{int(z.get(CONF_ZONE_ORDER, 0))} {z.get(CONF_ZONE_NAME, '?')} "
            f"({z.get(CONF_ZONE_ENTITY, '?')})"
            for z in sorted(
                zones,
                key=lambda z: (
                    z.get(CONF_ZONE_ORDER, 0),
                    z.get(CONF_ZONE_NAME, ""),
                ),
            )
        ) or "  (none)"
        last_run_count = len(state.get("last_run") or {})
        return self.async_show_form(
            step_id="restore_confirm",
            data_schema=vol.Schema(
                {vol.Required("confirm", default=True): selector.BooleanSelector()}
            ),
            description_placeholders={
                "source_domain": str(
                    self._import_payload.get("source_domain", "?")
                ),
                "exported_at": str(
                    self._import_payload.get("exported_at", "?")
                ),
                "source_path": str(
                    self._import_payload.get("__source_path__", "?")
                ),
                "zone_count": str(len(zones)),
                "zones": zone_lines,
                "last_run_count": str(last_run_count),
            },
        )

    def _guess_export_path(self) -> str:
        """Return the most plausible existing backup file path, else the default."""
        for name in DEFAULT_EXPORT_FILENAMES:
            candidate = self.hass.config.path(name)
            if os.path.isfile(candidate):
                return candidate
        return self.hass.config.path(DEFAULT_EXPORT_FILENAMES[0])


def _read_export_file(path: str) -> tuple[dict[str, Any] | None, str | None]:
    """Load + minimally validate an export file. Runs in the executor.

    Returns (payload, error_key). On success error_key is None. On failure the
    error key matches one of the config flow's translated error strings.
    """
    try:
        with open(path, "r", encoding="utf-8") as fh:
            payload = json.load(fh)
    except FileNotFoundError:
        return None, "file_not_found"
    except (OSError, json.JSONDecodeError) as err:
        _LOGGER.warning("Could not read backup %s: %s", path, err)
        return None, "invalid_format"

    if not isinstance(payload, dict):
        return None, "invalid_format"
    if payload.get("schema_version") != EXPORT_SCHEMA_VERSION:
        return None, "unsupported_version"
    if not isinstance(payload.get("config"), dict):
        return None, "invalid_format"
    return payload, None


class AutomatedGardenWateringOptionsFlow(config_entries.OptionsFlow):
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
            if choice and choice.startswith("edit:"):
                self._edit_zone_id = choice.split(":", 1)[1]
                return await self.async_step_zone_edit()
            return await self._save_and_exit()

        # Only edit selections are shown. Deleting a zone is done from inside its
        # edit screen, so an accidental tap here can never remove a zone.
        options = [{"value": "__add__", "label": "➕ Add a new zone"}]
        for z in sorted(
            self._zones,
            key=lambda x: (x.get(CONF_ZONE_ORDER, 0), x.get(CONF_ZONE_NAME, "")),
        ):
            label = (
                f"#{z.get(CONF_ZONE_ORDER, '?')} — {z.get(CONF_ZONE_NAME)} "
                f"({z.get(CONF_ZONE_ENTITY)})"
            )
            options.append({"value": f"edit:{z[CONF_ZONE_ID]}", "label": label})

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

    async def _save_and_exit(self):
        new_options: dict[str, Any] = dict(self._globals)
        new_options[CONF_ZONES] = self._zones
        return self.async_create_entry(title="", data=new_options)
