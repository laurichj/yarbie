"""Constants for the yarbie integration."""

from __future__ import annotations

DOMAIN = "yarbie"
MANUFACTURER = "Yarbo"

CONF_SERIAL = "serial"
CONF_SCAN = "scan"
CONF_SIMULATOR = "simulator"
# Firmware the user has checked by hand although the registry was not verified on it.
CONF_ACCEPTED_FIRMWARE = "accepted_firmware"

OPT_ALLOW_WRITES = "allow_writes"
OPT_OBSERVE_ONLY = "observe_only"
OPT_KEEP_AWAKE = "keep_awake"
OPT_GOOGLE_MAPS_KEY = "google_maps_key"
OPT_RUN_RETENTION_DAYS = "run_retention_days"

# Typed as a host in the config flow, this runs the built-in simulator (development).
SIMULATOR_HOST = "simulator"

STORAGE_VERSION = 1

# Telemetry arrives several times a second. Entities hear about it at most this often,
# unless something they show changes (activity, battery, a fault...).
PUSH_INTERVAL_S = 5.0
# While a plan runs its progress is read this often.
PROGRESS_INTERVAL_S = 30.0
# Plans can change in the phone app; read them again this often.
PLANS_INTERVAL_S = 600.0
KEEP_AWAKE_INTERVAL_S = 60.0

SIGNAL_PLAN_EVENT = f"{DOMAIN}_plan_event_{{}}"
ISSUE_UNTESTED_FIRMWARE = "untested_firmware_{}"
