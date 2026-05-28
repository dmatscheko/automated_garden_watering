# Garden Irrigation — Home Assistant Custom Integration

A Home Assistant integration for garden irrigation with a queue-based watering controller, well pump and backwash valve coordination, automatic mid-cycle and end-of-cycle backwash, a daily timer, and a global watering multiplier.

## Features

- Works on top of any existing Home Assistant `switch` entities (pump, backwash valve, zone valves).
- UI config flow + options flow (everything is editable later).
- Queue model: one zone at a time, multiple zones queued.
- "Water all" runs every zone in your configured run order.
- Pump is always turned on first, with configurable pressure build-up delay.
- Backwash can be triggered manually at any time; automatically every N minutes of active watering; and at end-of-queue when the run was long enough or was started by the timer.
- Global watering duration multiplier (e.g. `2.5` = water 2.5× longer than the per-zone default).
- Daily start timer with on/off switch.
- Status sensor & queue sensor for dashboards.

## Installation (HACS)

1. In HACS → Integrations → ⋮ → *Custom repositories*, add this repo's URL with category *Integration*.
2. Install **Garden Irrigation**.
3. Restart Home Assistant.
4. *Settings → Devices & Services → Add Integration → Garden Irrigation*.

## Configuration

The setup flow asks for the pump and backwash switch (both optional), and at least one zone. After setup, use *Configure* on the integration card to:

- Add / remove / reorder zones (each: switch entity, display name, default duration, run order).
- Adjust the global multiplier, pump/backwash delays, backwash valve runtime, automatic backwash interval and end-of-queue threshold.
- Edit the daily start time and the timer enable switch.

## Entities

| Entity | Description |
|---|---|
| `button.garden_irrigation_water_all` | Run all zones in order; pressing again while queue is active = emergency stop. Attribute `running` for coloring. |
| `button.garden_irrigation_backwash` | Immediate backwash (never queued). Attribute `status` (`running`/`idle`) for coloring. |
| `button.garden_irrigation_zone_<order>` | Toggle that zone in the queue (press to add, press again to remove/stop). The `<order>` suffix is the zone's run order, so the buttons sort naturally in the UI. The friendly zone name set during setup is the entity's display name and is also in the `zone_name` attribute. |
| `button.garden_irrigation_generate_dashboard_yaml` | Config button: generates a complete dashboard YAML for your exact entities/zones and shows it in a notification (also writes `garden_irrigation_dashboard.yaml` to your config folder). |
| `number.garden_irrigation_multiplier` | Global watering multiplier. |
| `time.garden_irrigation_daily_start` | Daily start time. |
| `switch.garden_irrigation_daily_timer` | Enable/disable the daily timer. |
| `sensor.garden_irrigation_status` | `idle`, `pump_pressure`, `watering`, `backwash_pressure`, `backwash`. Attributes: queue, active zone, remaining seconds. |
| `sensor.garden_irrigation_active_zone` | Currently watering zone name (or `none`). |
| `sensor.garden_irrigation_queue` | Comma-separated upcoming zones. |
| `sensor.garden_irrigation_current_step_time_remaining` | Countdown (`H:MM:SS`) for the active zone, or the backwash while it runs. Attributes: `hours`, `minutes`, `seconds`, `total_seconds`, `phase`, `label`. |
| `sensor.garden_irrigation_queue_time_remaining` | Countdown (`H:MM:SS`) of all remaining watering in the queue. Pauses (holds steady) during backwash. Attributes: `hours`, `minutes`, `seconds`, `total_seconds`. |

## Safety

- The pump is always turned on before any valve opens.
- Only one zone valve is ever open at a time.
- No zone runs during backwash.
- On stop / completion, all valves and the pump are closed.

## License

MIT
