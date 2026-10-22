"""Settings read from the environment (and ``.env``)."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv

SourceKind = Literal["sim", "lan", "cloud"]


@dataclass(frozen=True, slots=True)
class Settings:
    source: SourceKind
    host: str | None
    serial: str | None
    email: str | None
    password: str | None
    data_dir: Path
    google_maps_key: str | None = None

    @classmethod
    def from_env(cls) -> Settings:
        load_dotenv()
        settings = cls(
            source=_source(os.environ.get("YARBIE_SOURCE", "sim")),
            host=os.environ.get("YARBO_HOST") or None,
            serial=os.environ.get("YARBO_SERIAL") or None,
            email=os.environ.get("YARBO_EMAIL") or None,
            password=os.environ.get("YARBO_PASSWORD") or None,
            data_dir=Path(os.environ.get("YARBIE_DATA_DIR", "data")),
            google_maps_key=os.environ.get("GOOGLE_MAPS_API_KEY") or None,
        )
        if settings.source == "cloud" and not (settings.email and settings.password):
            raise ValueError("YARBIE_SOURCE=cloud needs YARBO_EMAIL and YARBO_PASSWORD")
        return settings


def _source(value: str) -> SourceKind:
    value = value.strip().lower()
    if value == "sim":
        return "sim"
    if value == "lan":
        return "lan"
    if value == "cloud":
        return "cloud"
    raise ValueError(f"YARBIE_SOURCE must be sim, lan or cloud, not {value!r}")
