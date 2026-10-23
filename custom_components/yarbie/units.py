"""Short lengths in the user's units. HA has no unit for inches or centimetres, so the
unit system only decides which of the two to show; everything is stored in metres."""

from __future__ import annotations

import math

from homeassistant.core import HomeAssistant
from homeassistant.util.unit_system import US_CUSTOMARY_SYSTEM

INCH_M = 0.0254
CENTIMETRE_M = 0.01


def short_length(hass: HomeAssistant) -> tuple[str, float]:
    """The unit for a few inches or centimetres, and metres per unit."""
    if hass.config.units is US_CUSTOMARY_SYSTEM:
        return "in", INCH_M
    return "cm", CENTIMETRE_M


def limits(limits_m: tuple[float, float], unit_m: float) -> tuple[float, float]:
    """A range in metres in a unit, to one decimal, rounded inward so it stays inside."""
    low, high = limits_m
    return math.ceil(low / unit_m * 10) / 10, math.floor(high / unit_m * 10) / 10


def shown(value_m: float, limits_m: tuple[float, float], unit_m: float) -> float:
    """A length in metres as the form shows it: one decimal, inside the unit's range."""
    low, high = limits(limits_m, unit_m)
    return min(max(round(value_m / unit_m, 1), low), high)
