"""Constants for the Garden Irrigation integration."""
from __future__ import annotations

DOMAIN = "garden_irrigation"

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
CONF_BACKWASH_RUNTIME = "backwash_runtime"     # seconds
CONF_BACKWASH_INTERVAL = "backwash_interval"   # seconds of active watering between auto-backwashes (0 = off)
CONF_BACKWASH_THRESHOLD = "backwash_threshold"  # seconds of cumulative watering after which end-of-queue backwash runs
CONF_DAILY_START = "daily_start"               # "HH:MM:SS"
CONF_DAILY_TIMER_ENABLED = "daily_timer_enabled"

# Defaults
DEFAULT_MULTIPLIER = 1.0
DEFAULT_PUMP_DELAY = 15
DEFAULT_BACKWASH_DELAY = 10
DEFAULT_BACKWASH_RUNTIME = 15
DEFAULT_BACKWASH_INTERVAL = 15 * 60
DEFAULT_BACKWASH_THRESHOLD = 3 * 60
DEFAULT_ZONE_DURATION = 10 * 60
DEFAULT_DAILY_START = "06:00:00"
DEFAULT_DAILY_TIMER_ENABLED = False

# Runtime state values
STATE_IDLE = "idle"
STATE_PUMP_PRESSURE = "pump_pressure"
STATE_WATERING = "watering"
STATE_BACKWASH_PRESSURE = "backwash_pressure"
STATE_BACKWASH = "backwash"

SIGNAL_UPDATE = f"{DOMAIN}_update"
