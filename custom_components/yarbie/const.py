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
# Stored in metres; the options form shows it in inches or centimetres under one of these.
OPT_SMOOTHING_TOLERANCE_M = "smoothing_tolerance_m"
OPT_SMOOTHING_TOLERANCE = {"in": "smoothing_tolerance_in", "cm": "smoothing_tolerance_cm"}
# What a tolerance may be, in metres (0.5 in to about 3 ft); each unit's form range is
# this rounded inward, so inches and centimetres always allow the same span.
SMOOTHING_LIMITS_M = (0.0127, 0.9)

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
