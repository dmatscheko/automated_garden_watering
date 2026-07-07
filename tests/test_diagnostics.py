"""Coverage for diagnostics.py and the dashboard YAML button."""
from __future__ import annotations

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.automated_garden_watering.const import DOMAIN
from custom_components.automated_garden_watering.diagnostics import (
    async_get_config_entry_diagnostics,
)

from .conftest import (
    BACKWASH_ENTITY,
    PUMP_ENTITY,
    ZONE1_ENTITY,
    ZONE2_ENTITY,
)


async def test_diagnostics_dump_shape(
    hass: HomeAssistant, setup_integration: MockConfigEntry
) -> None:
    """Diagnostics returns coordinator state and redacts switch entity_ids."""
    diag = await async_get_config_entry_diagnostics(hass, setup_integration)
    assert diag["entry"]["title"] == "Automated Garden Watering"
    # Sensitive-ish fields (pump_switch, backwash_switch, zone entity_id) redacted.
    assert PUMP_ENTITY not in repr(diag["entry"]["data"])
    assert BACKWASH_ENTITY not in repr(diag["entry"]["data"])
    assert ZONE1_ENTITY not in repr(diag["entry"]["data"])

    coord_dump = diag["coordinator"]
    assert coord_dump["state"] == "idle"
    assert coord_dump["pump_configured"] is True
    assert coord_dump["backwash_configured"] is True
    zone_ids = {z["id"] for z in coord_dump["zones"]}
    assert zone_ids == {"z1", "z2"}


async def test_dashboard_yaml_button_press(
    hass: HomeAssistant, setup_integration: MockConfigEntry
) -> None:
    """Pressing the dashboard YAML button runs build_dashboard end-to-end."""
    from homeassistant.setup import async_setup_component

    # persistent_notification isn't loaded by default in tests; load it so the
    # button's notification call doesn't error.
    assert await async_setup_component(hass, "persistent_notification", {})

    button_states = [
        s for s in hass.states.async_all("button") if "dashboard_yaml" in s.entity_id
    ]
    assert button_states, "dashboard yaml button should exist"
    # The press should not raise; covers button.GenerateDashboardButton and the
    # dashboard.build_dashboard pipeline.
    await hass.services.async_call(
        "button",
        "press",
        {"entity_id": button_states[0].entity_id},
        blocking=True,
    )


async def test_dashboard_yaml_content(
    hass: HomeAssistant, setup_integration: MockConfigEntry
) -> None:
    """Generated YAML: water-all tint plus the new details-panel rows."""
    from custom_components.automated_garden_watering.dashboard import build_dashboard

    yaml_text = build_dashboard(hass, setup_integration.runtime_data)
    # Water all stands out with a primary-color tint at rest.
    assert "color-mix(in srgb, var(--primary-color)" in yaml_text
    # Total watering time and the manual-pump backwash toggle are listed.
    assert "total_watering_time" in yaml_text
    assert "manual_pump_backwash" in yaml_text
