"""The robot itself, over its LAN MQTT broker (or the built-in simulator).

Every write goes through ``yarbo-local``, which only sends commands its registry marks
as verified against the phone app, so the robot stores what the app would have sent.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from yarbo_local import CommandError, FakeTransport, Simulator, YarboRobot
from yarbo_local.feedback import PlanFeedback
from yarbo_local.simulator import MOWER_AREA_PARAMS_DEFAULT

from .. import demo
from .base import RobotStatus

Point = tuple[float, float, float]


class _DemoSimulator(Simulator):
    """The packaged simulator, answering with the demo yard's own reference."""

    def cmd_read_gps_ref(self, value: Any) -> None:
        lat, lon = demo.REFERENCE
        self._feedback(
            "read_gps_ref",
            0,
            "",
            {"ref": {"latitude": lat, "longitude": lon}, "hgt": 30.0, "rtkFixType": 1},
        )


class RobotSource:
    writable = True

    def __init__(self, robot: YarboRobot, name: str, host: str | None = None) -> None:
        self.robot = robot
        self.name = name
        self.host = host

    @classmethod
    def lan(cls, host: str, serial: str | None = None) -> RobotSource:
        return cls(YarboRobot.for_host(host, serial=serial), "lan", host)

    @classmethod
    def simulated(cls) -> RobotSource:
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
        return cls(YarboRobot(transport, serial=demo.SERIAL), "sim")

    async def start(self) -> None:
        await self.robot.start()

    async def close(self) -> None:
        await self.robot.close()

    # -- reads

    async def raw_map(self) -> dict[str, Any]:
        return dict((await self.robot.site_map()).raw)

    async def reference(self) -> tuple[float, float] | None:
        ref = await self.robot.gps_reference()
        return (ref.latitude, ref.longitude) if ref else None

    async def plans(self) -> list[dict[str, Any]]:
        return [
            {"id": p.id, "name": p.name, "area_ids": list(p.area_ids), "self_order": p.self_order}
            for p in await self.robot.plans()
        ]

    async def status(self) -> RobotStatus:
        if not self.robot.session.connected:
            return RobotStatus(connected=False)
        state = await self.robot.snapshot()
        return RobotStatus(
            connected=True,
            activity=str(state.activity),
            battery=state.battery,
            position=state.position,
            busy=state.plan_running or state.paused_code > 0,
            firmware=state.firmware,
            detail={"host": self.host, "serial": self.robot.serial} if self.host else {},
        )

    async def mower_area_params(self, area_id: int) -> dict[str, Any] | None:
        return await self.robot.mower_area_params(area_id)

    async def preview_route(self, plan_id: int) -> PlanFeedback | None:
        """The robot computes this itself; a big map took it over a minute to answer (and
        then to refuse: "The map is too complex to support preview")."""
        await self.robot.wake()
        fb = await self.robot.raw_request(
            "preview_plan_path", {"id": plan_id, "percent": 0}, timeout=120.0
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
