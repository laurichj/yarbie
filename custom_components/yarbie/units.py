"""Short lengths in the user's units. HA has no unit for inches or centimetres, so the
unit system only decides which of the two to show; everything is stored in metres."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.util.unit_system import US_CUSTOMARY_SYSTEM

INCH_M = 0.0254
CENTIMETRE_M = 0.01


def short_length(hass: HomeAssistant) -> tuple[str, float]:
    """The unit for a few inches or centimetres, and metres per unit."""
    if hass.config.units is US_CUSTOMARY_SYSTEM:
        return "in", INCH_M
    return "cm", CENTIMETRE_M
