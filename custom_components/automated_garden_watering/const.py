"""Constants for the Automated Garden Watering integration."""
from __future__ import annotations

from typing import TypedDict

DOMAIN = "automated_garden_watering"


class ZoneConfig(TypedDict, total=False):
    """Per-zone config entry payload."""

    id: str
    entity_id: str
    name: str
    duration: int
    order: int

# Config / options keys
CONF_PUMP = "pump_switch"
CONF_BACKWASH = "backwash_switch"
CONF_ZONES = "zones"

CONF_ZONE_ENTITY = "entity_id"
CONF_ZONE_NAME = "name"
CONF_ZONE_DURATION = "duration"  # seconds
CONF_ZONE_ORDER = "order"
CONF_ZONE_ID = "id"

CONF_MULTIPLIER = "multiplier"
CONF_PUMP_DELAY = "pump_pressure_delay"        # seconds
CONF_BACKWASH_DELAY = "backwash_pressure_delay"  # seconds
CONF_BACKWASH_RUNTIME = "backwash_runtime"     # seconds (reverse-flow phase, pump off)
CONF_BACKWASH_FLUSH_RUNTIME = "backwash_flush_runtime"  # seconds (flush phase, pump on)
CONF_BACKWASH_INTERVAL = "backwash_interval"   # seconds of active watering between auto-backwashes (0 = off)
CONF_BACKWASH_THRESHOLD = "backwash_threshold"  # seconds of cumulative watering after which end-of-queue backwash runs
CONF_DAILY_START = "daily_start"               # "HH:MM:SS"
CONF_DAILY_TIMER_ENABLED = "daily_timer_enabled"

# Defaults — values informed by real-world testing, except the per-zone
# duration which is intentionally short (5 min) so a first-time user with just
# a hose attached (no sprinkler) can try the integration without flooding the
# garden by accident.
DEFAULT_MULTIPLIER = 1.0
DEFAULT_PUMP_DELAY = 10
DEFAULT_BACKWASH_DELAY = 10
DEFAULT_BACKWASH_RUNTIME = 15
DEFAULT_BACKWASH_FLUSH_RUNTIME = 10
DEFAULT_BACKWASH_INTERVAL = 10 * 60
DEFAULT_BACKWASH_THRESHOLD = 3 * 60
DEFAULT_ZONE_DURATION = 5 * 60
DEFAULT_DAILY_START = "06:00:00"
DEFAULT_DAILY_TIMER_ENABLED = False

# Runtime state values
STATE_IDLE = "idle"
STATE_PUMP_PRESSURE = "pump_pressure"
STATE_WATERING = "watering"
STATE_BACKWASH_PRESSURE = "backwash_pressure"  # pump on, building pressure, valves closed
STATE_BACKWASH = "backwash"                    # reverse-flow phase: pump OFF, backwash valve open
STATE_BACKWASH_FLUSH = "backwash_flush"        # flush phase: pump ON, backwash valve still open

# States during which a backwash is in progress (button stays "running",
# queue countdown is paused, no zone may run).
BACKWASH_ACTIVE_STATES = (
    STATE_BACKWASH_PRESSURE,
    STATE_BACKWASH,
    STATE_BACKWASH_FLUSH,
)

SIGNAL_UPDATE = f"{DOMAIN}_update"
