"""Backups of the robot's map, and of area settings, written before every change.
Blocking: run in an executor."""

from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path
import re
from typing import Any

# The reason in the name of a backup of area settings rather than of the map.
AREA_SETTINGS = "before-area-settings"


class BackupStore:
    def __init__(self, root: Path) -> None:
        self.dir = root / "backups"

    def save(self, raw: dict[str, Any], reason: str, actor: str | None = None) -> Path:
        return self._write({"reason": reason, "actor": actor, "map": raw}, reason, actor)

    def save_area_settings(self, areas: list[dict[str, Any]], actor: str | None = None) -> Path:
        """Each area's record and mowing settings as the robot held them before a change."""
        record = {"reason": AREA_SETTINGS, "actor": actor, "areas": areas}
        return self._write(record, AREA_SETTINGS, actor)

    def _write(self, record: dict[str, Any], reason: str, actor: str | None) -> Path:
        self.dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        who = f"-{_slug(actor)}" if actor else ""
        path = self.dir / f"{stamp}-{reason}{who}.json"
        path.write_text(json.dumps(record, indent=2, sort_keys=True), encoding="utf-8")
        return path

    def entries(self) -> list[dict[str, Any]]:
        if not self.dir.exists():
            return []
        return [
            {
                "name": p.name,
                "bytes": p.stat().st_size,
                "kind": "area_settings" if f"-{AREA_SETTINGS}" in p.name else "map",
            }
            for p in sorted(self.dir.glob("*.json"), reverse=True)
        ]

    def load(self, name: str) -> dict[str, Any]:
        """The map a backup holds. Backups from before the actor was recorded are the bare map."""
        path = (self.dir / name).resolve()
        if path.parent != self.dir.resolve() or path.suffix != ".json":
            raise FileNotFoundError(name)
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and data.get("reason") == AREA_SETTINGS:
            raise FileNotFoundError(f"{name} holds area settings, not a map")
        if isinstance(data, dict) and isinstance(data.get("map"), dict) and "reason" in data:
            return data["map"]
        return data


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:32] or "user"
