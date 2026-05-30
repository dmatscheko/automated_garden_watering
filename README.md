# Automated Garden Watering — Home Assistant Custom Integration

A Home Assistant integration for garden irrigation with a queue-based watering controller, well pump and backwash valve coordination, automatic mid-cycle and end-of-cycle backwash, a daily timer, and a global watering multiplier.

What sets it apart from other irrigation integrations: a **two-stage, interval-driven backwash** — a pump-off reverse-flow pass followed by a pump-on flush — that triggers automatically every N minutes of cumulative watering during long runs (and at end-of-queue when warranted). The reverse-flow stage lets stored pressure push water backwards through the filter to dislodge dirt, then the flush stage carries it away. The result is a filter that stays a lot cleaner than a single open-valve pulse.

## Features

- Works on top of any existing Home Assistant `switch` entities (pump, backwash valve, zone valves).
- UI config flow + options flow (everything is editable later).
- Queue model: one zone at a time, multiple zones queued.
- "Water all" runs every zone in your configured run order.
- Pump is always turned on first, with configurable pressure build-up delay.
- Two-stage backwash (pump-off reverse flow + pump-on flush) for a much deeper filter clean; triggered manually at any time, automatically every N minutes of active watering, and at end-of-queue when the run was long enough or was started by the timer.
- Global watering duration multiplier (e.g. `2.5` = water 2.5× longer than the per-zone default).
- Daily start timer with on/off switch.
- Status sensor & queue sensor for dashboards.
- UI available in English and German.

## Installation (HACS)

1. In HACS → Integrations → ⋮ → *Custom repositories*, add this repo's URL with category *Integration*.
2. Install **Automated Garden Watering**.
3. Restart Home Assistant.
4. *Settings → Devices & Services → Add Integration → Automated Garden Watering*.

## Configuration

The setup flow asks for the pump and backwash switch (both optional), and at least one zone. After setup, use *Configure* on the integration card to:

- Add / remove / reorder zones (each: switch entity, display name, default duration, run order). Delete a zone from inside its edit screen (tick *Delete this zone*).
- Adjust the pump/backwash delays, the backwash reverse-flow and flush times, automatic backwash interval and end-of-queue threshold.

The **watering multiplier**, **daily start time** and **daily timer** are *not*
in the config dialog — they have their own entities (`number` / `time` /
`switch`) so you set them directly from the dashboard. Their values persist
across restarts. This keeps each setting in exactly one place.

### Backwash sequence

Backwash runs a pump-off reverse-flow stage followed by a pump-on flush, which
cleans the filter far better than just opening the valve:

1. Close all zone valves.
2. **Pressure build-up** — pump on, wait *Backwash pressure build-up delay*.
3. **Reverse flow** — turn the pump **off**, open the backwash valve, wait
   *Backwash reverse-flow time*. With the pump off, the stored pressure pushes
   water backwards through the filter and dislodges the dirt (the pump would
   otherwise fight that reverse flow).
4. **Flush** — turn the pump back **on** (valve still open), wait *Backwash
   flush time* to flush the dislodged dirt out.
5. Close the backwash valve, then resume watering or, if nothing remains, turn
   the pump off.

## Entities

| Entity | Description |
|---|---|
| `button.automated_garden_watering_water_all` | Run all zones in order; pressing again while queue is active = emergency stop. Attribute `running` for coloring. |
| `button.automated_garden_watering_backwash` | Immediate backwash (never queued). Attributes `status` (`running`/`idle`) for coloring and `last_run` / `last_run_friendly` for showing when it last ran (persisted across restarts). |
| `button.automated_garden_watering_zone_<order>` | Toggle that zone in the queue (press to add, press again to remove/stop). The `<order>` suffix is the zone's run order, so the buttons sort naturally in the UI. The friendly zone name set during setup is the entity's display name and is also in the `zone_name` attribute. Attributes `last_run` (ISO) and `last_run_friendly` (e.g. `Today 14:30`, `Yesterday 09:00`, `Mon 09:00`, `May 03`) record when the zone last watered (persisted across restarts). |
| `switch.automated_garden_watering_pump_manual` | Manual well-pump control for e.g. a garden hose (only created if a pump is configured). Turning it **off** is blocked while a queue/backwash is active so the pump-first safety rule can't be broken manually. Attributes `status`, `last_run`, `last_run_friendly`, `controlled_by` (`manual`/`automation`) — so it can be shown as a button with a last-run line like the zones. |
| `button.automated_garden_watering_dashboard_yaml` | Config button (display name *Dashboard YAML*): generates a complete dashboard YAML for your exact entities/zones and shows it in a notification you can copy from. |
| `number.automated_garden_watering_watering_multiplier` | Global watering multiplier. |
| `time.automated_garden_watering_daily_start_time` | Daily start time. |
| `switch.automated_garden_watering_daily_timer` | Enable/disable the daily timer. |
| `switch.automated_garden_watering_show_details` | UI-only toggle (no effect on irrigation). The dashboard's *Details* button toggles it, and a `conditional` card reveals the status/timer/multiplier list while it's on. State is remembered across restarts. |
| `sensor.automated_garden_watering_status` | `idle`, `pump_pressure`, `watering`, `backwash_pressure`, `backwash` (reverse flow, pump off), `backwash_flush` (pump on). Slow-changing attributes only (queue, active zone, multiplier) so it doesn't flood the recorder. |
| `sensor.automated_garden_watering_active_zone` | Currently watering zone name (or `none`). |
| `sensor.automated_garden_watering_queue` | Comma-separated upcoming zones. |
| `sensor.automated_garden_watering_current_step_time_remaining` | Countdown (`H:MM:SS`) for the active zone, or the backwash while it runs. Attributes: `hours`, `minutes`, `seconds`, `total_seconds`, `phase`, `label`. Updates every second — see *Database / recorder* below. |
| `sensor.automated_garden_watering_queue_time_remaining` | Countdown (`H:MM:SS`) of all remaining watering in the queue. Pauses (holds steady) during backwash. Attributes: `hours`, `minutes`, `seconds`, `total_seconds`. Updates every second — see *Database / recorder* below. |

## Database / recorder

The two `*_time_remaining` sensors are live countdowns: their state changes every
second while irrigation is running, which would write a lot of rows to the
recorder database. (The other sensors only change on real transitions, so they
are fine to keep.)

Recording these countdowns has little value, so exclude them. Add this to your
`configuration.yaml` (merge with any existing `recorder:` block) and restart:

```yaml
recorder:
  exclude:
    entity_globs:
      # Live irrigation countdowns — change every second, no value in history.
      - sensor.*_time_remaining
```

If you renamed the integration's device, the entity_id prefix differs; the glob
above still matches because it keys off the `_time_remaining` suffix. To be more
specific instead, use `sensor.automated_garden_watering_*_time_remaining` (adjust the
prefix to your device name).

## Safety

- The pump is always turned on before any **zone** valve opens.
- Only one zone valve is ever open at a time.
- No zone runs during backwash.
- On stop / completion, all valves and the pump are closed.

> Note: the backwash *reverse-flow* stage intentionally runs with the pump
> **off** while the backwash valve is open — this is required so water can flow
> backwards through the filter. This is the one deliberate exception to
> "pump on before a valve opens", and it only applies to the backwash valve,
> never to zone valves.

## License

Apache License, Version 2.0 — see [LICENSE](LICENSE).
