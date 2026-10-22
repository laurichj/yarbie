"""What the API needs from wherever the map comes from."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True, slots=True)
class RobotStatus:
    connected: bool
    activity: str | None = None
    battery: int | None = None
    position: tuple[float, float, float] | None = None  # local metres and heading
    busy: bool = False  # a plan is running or paused: map writes are refused
    firmware: str | None = None
    detail: dict[str, Any] = field(default_factory=dict)


class ReadOnlyError(RuntimeError):
    """The current source cannot change the robot."""


class MapSource(Protocol):
    name: str
    writable: bool

    async def start(self) -> None: ...

    async def close(self) -> None: ...

    async def raw_map(self) -> dict[str, Any]:
        """The decoded ``get_map`` payload."""
        ...

    async def reference(self) -> tuple[float, float] | None:
        """The robot's GPS reference ``(latitude, longitude)``."""
        ...

    async def plans(self) -> list[dict[str, Any]]: ...

    async def status(self) -> RobotStatus: ...
