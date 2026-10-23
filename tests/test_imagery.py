from __future__ import annotations

import asyncio
import json
from pathlib import Path
import time

import httpx

from yarbie.imagery import GoogleImagery


def fake_google(calls: list[str]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path == "/v1/createSession":
            body = json.loads(request.content)
            assert body["mapType"] == "satellite" and body["highDpi"] is True
            return httpx.Response(
                200, json={"session": "S1", "expiry": str(int(time.time()) + 14 * 86400)}
            )
        if request.url.path == "/tile/v1/viewport":
            assert request.url.params["session"] == "S1"
            return httpx.Response(
                200,
                json={
                    "copyright": "Imagery ©2026 Airbus, Maxar Technologies",
                    "maxZoomRects": [{"maxZoom": 21}, {"maxZoom": 22}],
                },
            )
        return httpx.Response(404)

    return httpx.MockTransport(handler)


def test_session_is_opened_once_and_reused(tmp_path: Path) -> None:
    calls: list[str] = []
    google = GoogleImagery("KEY123456", tmp_path / "google.json", fake_google(calls))
    source = asyncio.run(google.source((47.87, -121.71)))
    assert source["maxzoom"] == 22
    assert "Maxar" in source["attribution"]
    assert "session=S1" in source["tiles"] and "{z}/{x}/{y}" in source["tiles"]
    asyncio.run(google.source((47.87, -121.71)))
    assert calls.count("/v1/createSession") == 1


def test_a_new_key_gets_a_new_session(tmp_path: Path) -> None:
    calls: list[str] = []
    asyncio.run(GoogleImagery("KEY111111", tmp_path / "g.json", fake_google(calls)).source(None))
    asyncio.run(GoogleImagery("KEY222222", tmp_path / "g.json", fake_google(calls)).source(None))
    assert calls.count("/v1/createSession") == 2
