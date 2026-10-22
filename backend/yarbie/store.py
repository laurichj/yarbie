"""Backups of the robot's map, written before every change."""

from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path
from typing import Any


class BackupStore:
    def __init__(self, root: Path) -> None:
        self.dir = root / "backups"

    def save(self, raw: dict[str, Any], reason: str) -> Path:
        self.dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        path = self.dir / f"{stamp}-{reason}.json"
        path.write_text(json.dumps(raw, indent=2, sort_keys=True), encoding="utf-8")
        return path

    def entries(self) -> list[dict[str, Any]]:
        if not self.dir.exists():
            return []
        return [
            {"name": p.name, "bytes": p.stat().st_size}
            for p in sorted(self.dir.glob("*.json"), reverse=True)
        ]

    def load(self, name: str) -> dict[str, Any]:
        path = (self.dir / name).resolve()
        if path.parent != self.dir.resolve() or path.suffix != ".json":
            raise FileNotFoundError(name)
        return json.loads(path.read_text(encoding="utf-8"))
