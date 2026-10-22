"""Imagery that needs a key, offered to the panel alongside the free sources.

Google's Map Tiles API hands out a session token that is good for about two weeks; it is
kept in HA's storage so a restart does not open a new one.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from aiohttp import ClientSession

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import DOMAIN, STORAGE_VERSION

_LOGGER = logging.getLogger(__name__)

GOOGLE = "https://tile.googleapis.com"
# Renew a day before Google says the session ends.
RENEW_EARLY_S = 24 * 3600


class GoogleImagery:
    def __init__(self, hass: HomeAssistant, key: str, session: ClientSession) -> None:
        self._key = key
        self._http = session
        self._store: Store[dict[str, Any]] = Store(hass, STORAGE_VERSION, f"{DOMAIN}.google_tiles")

    async def source(self, reference: tuple[float, float] | None) -> dict[str, Any]:
        """The tile source for the panel, with the attribution Google requires and the
        deepest zoom it has around the yard."""
        session = await self._session()
        attribution = "Imagery ©Google"
        maxzoom = 21
        if reference is not None:
            lat, lon = reference
            d = 0.003  # a few hundred metres around the robot's reference
            async with self._http.get(
                f"{GOOGLE}/tile/v1/viewport",
                params={
                    "session": session,
                    "key": self._key,
                    "zoom": 20,
                    "north": lat + d,
                    "south": lat - d,
                    "east": lon + d,
                    "west": lon - d,
                },
            ) as response:
                response.raise_for_status()
                info = await response.json()
            if info.get("copyright"):
                attribution = f"Imagery ©Google, {info['copyright']}"
            zooms = [r.get("maxZoom") for r in info.get("maxZoomRects") or []]
            zooms = [z for z in zooms if isinstance(z, int)]
            if zooms:
                maxzoom = max(zooms)
        return {
            "id": "google",
            "label": "Google satellite",
            "tiles": f"{GOOGLE}/v1/2dtiles/{{z}}/{{x}}/{{y}}?session={session}&key={self._key}",
            "maxzoom": maxzoom,
            "attribution": attribution,
        }

    async def _session(self) -> str:
        cached = await self._read()
        if cached is not None:
            return cached
        async with self._http.post(
            f"{GOOGLE}/v1/createSession",
            params={"key": self._key},
            json={
                "mapType": "satellite",
                "language": "en-US",
                "region": "US",
                # 512-pixel tiles, so Retina screens are not drawing stretched images.
                "scale": "scaleFactor2x",
                "highDpi": True,
            },
        ) as response:
            if response.status != 200:
                raise RuntimeError(f"Google refused the session: {(await response.text())[:300]}")
            body = await response.json()
        await self._store.async_save(
            {"session": body["session"], "expiry": int(body["expiry"]), "key_tail": self._key[-6:]}
        )
        _LOGGER.info("opened a Google Map Tiles session")
        return str(body["session"])

    async def _read(self) -> str | None:
        data = await self._store.async_load()
        try:
            if (
                data is not None
                and data["key_tail"] == self._key[-6:]
                and data["expiry"] - RENEW_EARLY_S > time.time()
            ):
                return str(data["session"])
        except (KeyError, TypeError):
            pass
        return None
