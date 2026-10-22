"""Read-only view through Yarbo's cloud, for when the robot is not on this network."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

from yarbo_local import codec
from yarbo_local.models import parse_gps_ref, parse_plans
from yarbo_robot_sdk import TokenExpiredError, YarboClient
from yarbo_robot_sdk.models import Device

from .base import RobotStatus


def _decoded(response: dict[str, Any]) -> Any:
    """The ``data`` of a feedback, which may be a JSON object or a base64 zlib blob."""
    data = response.get("data")
    blob = codec.decode_blob(data)
    return blob[0] if blob is not None else data


class CloudSource:
    name = "cloud"
    writable = False

    def __init__(
        self, email: str, password: str, token_file: Path, serial: str | None = None
    ) -> None:
        self._email = email
        self._password = password
        self._token_file = token_file
        self._serial = serial
        self._client: YarboClient | None = None
        self._device: Device | None = None

    # -- lifecycle (the SDK blocks, so it runs in a thread)

    async def start(self) -> None:
        await asyncio.to_thread(self._connect)

    async def close(self) -> None:
        if self._client is not None:
            await asyncio.to_thread(self._client.close)

    def _connect(self) -> None:
        client = YarboClient()
        if not self._restore(client):
            client.login(self._email, self._password)
        self._save_tokens(client)
        devices = client.get_devices()
        device = next((d for d in devices if self._serial in (None, d.sn)), None)
        if device is None:
            raise RuntimeError(f"no robot {self._serial or ''} on this Yarbo account".strip())
        client.mqtt_connect()
        self._client, self._device = client, device

    def _restore(self, client: YarboClient) -> bool:
        try:
            saved = json.loads(self._token_file.read_text(encoding="utf-8"))
            client.restore_session(self._email, saved["token"], saved["refresh_token"])
            client.get_devices()  # proves the tokens still work
        except (OSError, ValueError, KeyError, TokenExpiredError):
            return False
        return True

    def _save_tokens(self, client: YarboClient) -> None:
        if not client.token or not client.refresh_token:
            return
        self._token_file.parent.mkdir(parents=True, exist_ok=True)
        self._token_file.write_text(
            json.dumps({"token": client.token, "refresh_token": client.refresh_token}),
            encoding="utf-8",
        )
        os.chmod(self._token_file, 0o600)

    def _bound(self) -> tuple[YarboClient, Device]:
        if self._client is None or self._device is None:
            raise RuntimeError("cloud source not started")
        return self._client, self._device

    # -- reads

    async def raw_map(self) -> dict[str, Any]:
        client, device = self._bound()
        response = await asyncio.to_thread(client.get_map, device.sn, device.type_id)
        data = _decoded(response)
        return dict(data) if isinstance(data, dict) else {}

    async def reference(self) -> tuple[float, float] | None:
        client, device = self._bound()
        response = await asyncio.to_thread(client.read_gps_ref, device.sn, device.type_id)
        ref = parse_gps_ref(_decoded(response))
        return (ref.latitude, ref.longitude) if ref else None

    async def plans(self) -> list[dict[str, Any]]:
        client, device = self._bound()
        response = await asyncio.to_thread(client.read_all_plan, device.sn, device.type_id)
        return [
            {"id": p.id, "name": p.name, "area_ids": list(p.area_ids), "self_order": p.self_order}
            for p in parse_plans(_decoded(response))
        ]

    async def status(self) -> RobotStatus:
        if self._device is None:
            return RobotStatus(connected=False)
        return RobotStatus(
            connected=self._device.online,
            detail={"name": self._device.name, "model": self._device.model},
        )
