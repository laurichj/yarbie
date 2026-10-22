"""Imagery that needs a key, offered to the browser alongside the free sources.

Google's Map Tiles API hands out a session token that is good for about two weeks; it is
kept in the data directory so a restart does not open a new one.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
import time
from typing import Any

import httpx

log = logging.getLogger("yarbie.imagery")

GOOGLE = "https://tile.googleapis.com"
# Renew a day before Google says the session ends.
RENEW_EARLY_S = 24 * 3600


class GoogleImagery:
    def __init__(
        self, key: str, cache: Path, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self._key = key
        self._cache = cache
        self._transport = transport  # tests stand in for Google here

    async def source(self, reference: tuple[float, float] | None) -> dict[str, Any]:
        """The tile source for the browser, with the attribution Google requires and the
        deepest zoom it has around the yard."""
        async with httpx.AsyncClient(timeout=15.0, transport=self._transport) as client:
            session = await self._session(client)
            attribution = "Imagery ©Google"
            maxzoom = 21
            if reference is not None:
                lat, lon = reference
                d = 0.003  # a few hundred metres around the robot's reference
                response = await client.get(
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
                )
                response.raise_for_status()
                info = response.json()
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

    async def _session(self, client: httpx.AsyncClient) -> str:
        cached = self._read()
        if cached is not None:
            return cached
        response = await client.post(
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
        )
        if response.status_code != 200:
            raise RuntimeError(f"Google refused the session: {response.text[:300]}")
        body = response.json()
        self._write(body["session"], int(body["expiry"]))
        log.info("opened a Google Map Tiles session")
        return str(body["session"])

    def _read(self) -> str | None:
        try:
            data = json.loads(self._cache.read_text(encoding="utf-8"))
            if data["key_tail"] == self._key[-6:] and data["expiry"] - RENEW_EARLY_S > time.time():
                return str(data["session"])
        except (OSError, ValueError, KeyError, TypeError):
            pass
        return None

    def _write(self, session: str, expiry: int) -> None:
        try:
            self._cache.parent.mkdir(parents=True, exist_ok=True)
            self._cache.write_text(
                json.dumps({"session": session, "expiry": expiry, "key_tail": self._key[-6:]}),
                encoding="utf-8",
            )
        except OSError as err:
            log.warning("could not keep the Google session: %s", err)
