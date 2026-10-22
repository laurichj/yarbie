"""The robot itself, over its LAN MQTT broker (or the built-in simulator).

Every command goes through yarbo-local, which only sends what its registry marks as
verified against the phone app, so the robot stores what the app would have sent.
"""

from __future__ import annotations

import asyncio
import math
from collections.abc import Callable, Coroutine, Sequence
from typing import Any

from ._vendor.yarbo_local import FakeTransport, Simulator, YarboRobot
from ._vendor.yarbo_local.exceptions import CommandError
from ._vendor.yarbo_local.feedback import PlanFeedback
from ._vendor.yarbo_local.models import PlanSummary, RobotState
from ._vendor.yarbo_local.simulator import MOWER_AREA_PARAMS_DEFAULT
from .editor import demo

Point = tuple[float, float, float]
Spawn = Callable[[Coroutine[Any, Any, None]], asyncio.Task[None]]

# light_ctrl channels, all 0..255 (verified on 3.14.11: 255 everywhere reads back as the
# robot's own maximum per channel).
LIGHT_CHANNELS = (
    "led_head",
    "led_left_w",
    "led_right_w",
    "body_left_r",
    "body_right_r",
    "tail_left_r",
    "tail_right_r",
)


class _DemoSimulator(Simulator):
    """The packaged simulator, answering with the demo yard's own reference, and with
    routes that cover its areas (the packaged one returns a 1 m stub at the origin)."""

    def cmd_preview_plan_path(self, value: Any) -> None:
        wanted = value.get("id") if isinstance(value, dict) else None
        plan = next((p for p in self.plans if p.get("id") == wanted), None)
        if plan is None:
            self._feedback("preview_plan_path", -1, "plan not found", "")
            return
        areas = {a.get("id"): a for a in self.site_map.get("areas") or []}
        paths = []
        total_m = 0.0
        for area_id in plan.get("areaIds") or []:
            outline = [(float(p["x"]), float(p["y"])) for p in areas.get(area_id, {}).get("range") or []]
            if len(outline) < 3:
                continue
            fill, edge = demo.route(outline)
            for kind, line in ((1, edge), (0, fill)):
                total_m += sum(math.dist(a, b) for a, b in zip(line, line[1:]))
                paths.append(
                    {
                        "clean_index": 0,
                        "clean_times": 0,
                        "id": area_id,
                        "type": kind,
                        "path_slope": [],
                        "path": [{"x": round(x, 3), "y": round(y, 3)} for x, y in line],
                    }
                )
        seconds = round(total_m / 0.4)  # about the robot's mowing speed
        self._feedback(
            "preview_plan_path",
            0,
            "",
            {
                "planId": wanted,
                "areaIds": list(plan.get("areaIds") or []),
                "cleanAreaId": -1,
                "cleanPathProgress": paths,
                "finishIds": [],
                "state": 0,
                "startTime": 0,
                "duration": 0,
                "leftTime": float(seconds),
                "totalTime": float(seconds),
            },
        )

    def cmd_read_gps_ref(self, value: Any) -> None:
        lat, lon = demo.REFERENCE
        self._feedback(
            "read_gps_ref",
            0,
            "",
            {"ref": {"latitude": lat, "longitude": lon}, "hgt": 30.0, "rtkFixType": 1},
        )


class Robot:
    def __init__(self, robot: YarboRobot, name: str, host: str | None = None) -> None:
        self.robot = robot
        self.name = name
        self.host = host
        self.simulator: Simulator | None = None

    @classmethod
    def lan(cls, host: str, serial: str | None = None) -> Robot:
        return cls(YarboRobot.for_host(host, serial=serial), "lan", host)

    @classmethod
    def simulated(cls) -> Robot:
        sim = _DemoSimulator(
            serial=demo.SERIAL,
            snapshot=demo.snapshot(),
            plans=demo.plans(),
            site_map=demo.site_map(),
        )
        # Keep new ids clear of the demo's own (the real robot counts on from its last).
        sim.next_object_id = 100
        for area in demo.site_map()["areas"]:
            sim.mower_area_params[area["id"]] = {**MOWER_AREA_PARAMS_DEFAULT, "id": area["id"]}
        transport = FakeTransport()
        sim.attach(transport)
        robot = cls(YarboRobot(transport, serial=demo.SERIAL), "sim")
        robot.simulator = sim
        return robot

    async def start(self, spawn: Spawn | None = None, ready_timeout: float = 15.0) -> None:
        await self.robot.start(ready_timeout, spawn=spawn)

    async def close(self) -> None:
        await self.robot.close()

    # -- state

    @property
    def state(self) -> RobotState:
        return self.robot.state

    @property
    def serial(self) -> str | None:
        return self.robot.serial

    @property
    def connected(self) -> bool:
        return self.robot.session.connected

    @property
    def busy(self) -> bool:
        """A plan is running or paused: map writes are refused."""
        state = self.robot.state
        return state.plan_running or state.paused_code > 0

    def on_state(self, cb: Callable[[RobotState], None]) -> Callable[[], None]:
        return self.robot.on_state(cb)

    # -- reads

    async def snapshot(self) -> RobotState:
        return await self.robot.snapshot()

    async def raw_map(self) -> dict[str, Any]:
        return dict((await self.robot.site_map()).raw)

    async def reference(self) -> tuple[float, float] | None:
        ref = await self.robot.gps_reference()
        return (ref.latitude, ref.longitude) if ref else None

    async def plans(self) -> list[PlanSummary]:
        return await self.robot.plans()

    async def mower_area_params(self, area_id: int) -> dict[str, Any] | None:
        return await self.robot.mower_area_params(area_id)

    async def preview_route(self, plan_id: int) -> PlanFeedback | None:
        """The robot computes this itself; a big map took it over a minute to answer (and
        then to refuse: "The map is too complex to support preview")."""
        await self.robot.wake()
        fb = await self.robot.raw_request(
            "preview_plan_path", {"id": plan_id, "percent": 0}, timeout=150.0
        )
        return PlanFeedback.from_wire(fb.payload)

    async def plan_progress(self) -> tuple[PlanFeedback, dict[str, Any]] | None:
        """The run in progress, or the last one, parsed and raw; None when there is none.

        The raw reply is kept because on 3.14.20 ``finishIds`` holds records
        ({id, move_type, clean_times}) that the library reads as plain ids and drops.
        """
        try:
            fb = await self.robot.raw_request("get_plan_feedback", {}, timeout=15.0)
        except CommandError as err:
            if err.state == -1:  # "no plan working"
                return None
            raise
        parsed = PlanFeedback.from_wire(fb.payload)
        if parsed is None or not isinstance(fb.payload, dict):
            return None
        return parsed, fb.payload

    async def plan_history(self) -> list[dict[str, Any]]:
        return await self.robot.plan_history()

    # -- controls

    async def start_plan(self, plan_id: int) -> None:
        await self.robot.start_plan(plan_id)

    async def pause(self) -> None:
        await self.robot.pause()

    async def resume(self) -> None:
        await self.robot.resume()

    async def stop(self) -> None:
        await self.robot.stop()

    async def dock(self) -> None:
        await self.robot.dock()

    async def wake(self) -> bool:
        return await self.robot.wake()

    async def beep(self) -> None:
        """Three buzzes of about 5 s ("find yarbo"). Takes the controller."""
        await self.robot.session.send("song_cmd", {"song_name": "find yarbo"})

    async def set_lights(self, levels: dict[str, int]) -> None:
        """Every channel at once; missing channels go off. Takes the controller, so
        during a plan the session refuses rather than pause it."""
        body = {ch: max(0, min(255, int(levels.get(ch, 0)))) for ch in LIGHT_CHANNELS}
        await self.robot.session.send("light_ctrl", body)

    async def set_sound(self, enabled: bool) -> None:
        volume = self.robot.state.volume
        await self.robot.set_sound(enabled=enabled, volume=volume if volume is not None else 1.0)

    async def set_person_detection(self, enabled: bool) -> None:
        await self.robot.set_person_detection(enabled)

    async def set_child_lock(self, enabled: bool) -> None:
        await self.robot.set_child_lock(enabled)

    # -- map writes

    async def save_nogozone(
        self, name: str, outline: Sequence[Point], ref: tuple[float, float], *,
        zone_id: int | None, enabled: bool,
    ) -> dict[str, Any]:
        return await self.robot.save_nogozone(
            name, outline, ref, zone_id=zone_id, enabled=enabled
        )

    async def delete_nogozone(self, zone_id: int) -> None:
        await self.robot.delete_nogozone(zone_id)

    async def save_pathway(
        self, name: str, line: Sequence[Point], ref: tuple[float, float], *, pathway_id: int | None
    ) -> dict[str, Any]:
        return await self.robot.save_pathway(name, line, ref, pathway_id=pathway_id)

    async def delete_pathway(self, pathway_id: int) -> None:
        await self.robot.delete_pathway(pathway_id)

    async def save_sidewalk(
        self, name: str, line: Sequence[Point], ref: tuple[float, float], *, sidewalk_id: int | None
    ) -> dict[str, Any]:
        return await self.robot.save_sidewalk(name, line, ref, sidewalk_id=sidewalk_id)

    async def delete_sidewalk(self, sidewalk_id: int) -> None:
        await self.robot.delete_sidewalk(sidewalk_id)

    async def update_area(
        self, area_id: int, *, name: str | None, outline: Sequence[Point] | None,
        algorithm_type: int | None,
    ) -> dict[str, Any]:
        return await self.robot.update_area(
            area_id, name=name, outline=outline, algorithm_type=algorithm_type
        )

    # -- plans and settings

    async def save_plan(self, name: str, area_ids: Sequence[int], plan_id: int | None) -> int:
        return await self.robot.save_plan(name, area_ids, plan_id=plan_id)

    async def delete_plan(self, plan_id: int) -> None:
        await self.robot.delete_plan(plan_id)

    async def update_mower_area_params(self, area_id: int, changes: dict[str, Any]) -> dict[str, Any]:
        return await self.robot.update_mower_area_params(area_id, **changes)
