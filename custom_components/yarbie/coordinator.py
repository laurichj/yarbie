"""One robot session shared by the entities and the editor."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import timedelta
import logging
from pathlib import Path
import time
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, Context, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_call_later, async_track_time_interval
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from ._vendor.yarbo_local.exceptions import YarboError
from ._vendor.yarbo_local.feedback import PlanFeedback
from ._vendor.yarbo_local.lifecycle import LifecycleEvent, PlanTracker
from ._vendor.yarbo_local.models import PlanSummary, RobotState, local_to_wgs84
from .const import (
    CONF_ACCEPTED_FIRMWARE,
    DOMAIN,
    ISSUE_UNTESTED_FIRMWARE,
    KEEP_AWAKE_INTERVAL_S,
    OPT_ALLOW_WRITES,
    OPT_KEEP_AWAKE,
    OPT_OBSERVE_ONLY,
    OPT_RUN_RETENTION_DAYS,
    OPT_SMOOTHING_TOLERANCE_M,
    PLANS_INTERVAL_S,
    PUSH_INTERVAL_S,
    SMOOTHING_LIMITS_M,
    SIGNAL_PLAN_EVENT,
    STORAGE_VERSION,
)
from .editor import runs, smooth
from .editor.backups import BackupStore
from .editor.service import MapService, ReadOnlyError
from .jobs import Jobs
from .robot import Robot
from .runlog import DEFAULT_RETENTION_DAYS, RunLog, RunRecorder
from .units import limits, short_length

_LOGGER = logging.getLogger(__name__)

# Commands the phone app sends that change the stored map: hearing one means the
# editor's copy is stale.
MAP_COMMANDS = frozenset(
    {
        "save_nogozone", "del_nogozone", "save_pathway", "del_pathway", "save_sidewalk",
        "del_sidewalk", "save_clean_area", "del_clean_area", "save_charging_point",
    }
)
PLAN_COMMANDS = frozenset({"save_plan", "del_plan", "sort_plan"})

type YarbieConfigEntry = ConfigEntry[YarbieCoordinator]


def _signature(state: RobotState) -> tuple[Any, ...]:
    """What entities show that should reach them at once rather than on the next push."""
    return (
        state.activity,
        state.battery,
        state.error_code,
        state.charging,
        state.plan_running,
        state.paused_code,
        state.stop_button_engaged,
        state.rtk_usable,
        state.sound_enabled,
        state.person_detection,
        state.child_lock,
        state.head_type,
        state.awake,
        state.get("mower_head_info01.rain_sensor"),
    )


class YarbieCoordinator(DataUpdateCoordinator[RobotState]):
    def __init__(self, hass: HomeAssistant, entry: YarbieConfigEntry, robot: Robot) -> None:
        super().__init__(hass, _LOGGER, config_entry=entry, name=DOMAIN)
        self.entry = entry  # config_entry, typed as always present
        self.robot = robot
        self.lock = asyncio.Lock()
        self.jobs = Jobs()
        self.service = MapService(
            robot, BackupStore(self.storage_dir), self.lock, self.check_writes
        )
        self.plans: list[PlanSummary] = []
        self.selected_plan: str | None = None  # the plan select's choice, by name
        self.feedback: PlanFeedback | None = None
        self.map_stale = True
        self.tracker = PlanTracker()
        self._tracker_store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}.runs"
        )
        self.runlog = RunLog(
            self.storage_dir,
            int(entry.options.get(OPT_RUN_RETENTION_DAYS, DEFAULT_RETENTION_DAYS)),
        )
        self.recorder = RunRecorder(self._save_run, self._plan_info, self._map_revision)
        self._run_writes = asyncio.Lock()  # FIFO: run log writes land in the order made
        self.last_run: dict[str, Any] | None = None  # newest run, robot and log joined
        self._unsubs: list[Callable[[], None]] = []
        self._pending_push: CALLBACK_TYPE | None = None
        self._last_push = 0.0
        self._last_signature: tuple[Any, ...] | None = None

    # -- paths and policy

    @property
    def storage_dir(self) -> Path:
        return Path(self.hass.config.path(DOMAIN))

    @property
    def serial(self) -> str:
        return self.robot.serial or self.entry.unique_id or "yarbo"

    @property
    def options(self) -> dict[str, Any]:
        return dict(self.entry.options)

    @property
    def smoothing(self) -> dict[str, Any]:
        """The smoothing tolerance (metres), the unit the panel shows it in, and the range
        the panel accepts in that unit (the options form's)."""
        unit, unit_m = short_length(self.hass)
        tolerance = float(self.options.get(OPT_SMOOTHING_TOLERANCE_M, smooth.DEFAULT_TOLERANCE_M))
        low_m, high_m = SMOOTHING_LIMITS_M
        return {
            "tolerance_m": min(max(tolerance, low_m), high_m),
            "unit": unit,
            "unit_m": unit_m,
            "limits": list(limits(SMOOTHING_LIMITS_M, unit_m)),
        }

    @property
    def observe_only(self) -> bool:
        return bool(self.options.get(OPT_OBSERVE_ONLY, False))

    @property
    def firmware_tested(self) -> bool:
        firmware = self.robot.state.firmware
        if firmware is None:
            return True  # not heard yet; checked again when it is
        verified = self.robot.robot.session.registry.verified_firmware
        return firmware in verified or firmware == self.entry.data.get(
            CONF_ACCEPTED_FIRMWARE
        )

    def check_controls(self) -> None:
        """Start, pause, dock, lights...: refused only in observe-only mode."""
        if self.observe_only:
            raise ReadOnlyError("yarbie is set to observe only; it sends the robot nothing")

    def check_writes(self) -> None:
        """Map, plan and settings writes."""
        self.check_controls()
        if not self.options.get(OPT_ALLOW_WRITES, True):
            raise ReadOnlyError("map writes are turned off in the yarbie options")
        if not self.firmware_tested:
            raise ReadOnlyError(
                f"firmware {self.robot.state.firmware} has not been tested with yarbie; "
                "see Settings > Repairs"
            )

    @property
    def writable(self) -> bool:
        try:
            self.check_writes()
        except ReadOnlyError:
            return False
        return self.robot.connected

    # -- lifecycle

    async def async_start(self) -> None:
        stored = await self._tracker_store.async_load()
        self.tracker = PlanTracker.from_dict(stored)
        await self.robot.start(
            spawn=lambda coro: self.entry.async_create_background_task(
                self.hass, coro, f"{DOMAIN} session"
            )
        )
        self._unsubs.append(self.robot.on_state(self._on_state))
        self._unsubs.append(self.robot.robot.session.add_topic_listener(self._on_topic))
        self._unsubs.append(
            self.robot.robot.session.add_connection_listener(lambda _up: self._push_now())
        )
        self._unsubs.append(
            async_track_time_interval(
                self.hass, self._refresh_plans, timedelta(seconds=PLANS_INTERVAL_S)
            )
        )
        if self.options.get(OPT_KEEP_AWAKE) and not self.observe_only:
            self._unsubs.append(
                async_track_time_interval(
                    self.hass, self._keep_awake, timedelta(seconds=KEEP_AWAKE_INTERVAL_S)
                )
            )
        try:
            await self.robot.snapshot()
            self.plans = await self.robot.plans()
        except (YarboError, TimeoutError) as err:
            _LOGGER.warning("first read from the robot failed: %s", err)
        self.check_firmware()
        self.async_set_updated_data(self.robot.state)
        # The map and the run history can take the robot many seconds to send; entities
        # do not wait for them.
        self.entry.async_create_background_task(
            self.hass, self._first_reads(), f"{DOMAIN} first map and history read"
        )

    async def _first_reads(self) -> None:
        await self.async_load_map()
        await self.hass.async_add_executor_job(self.runlog.prune)
        await self.async_refresh_runs()

    async def async_load_map(self) -> None:
        try:
            await self.service.load()
        except (YarboError, TimeoutError) as err:
            _LOGGER.warning("reading the map failed: %s", err)
            return
        self.map_stale = False
        self.async_update_listeners()

    async def async_stop(self) -> None:
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()
        if self._pending_push is not None:
            self._pending_push()
            self._pending_push = None
        await self.jobs.cancel_all()
        await self._tracker_store.async_save(self.tracker.to_dict())
        await self.robot.close()

    async def _async_update_data(self) -> RobotState:
        # Push only: data arrives from the session. A manual refresh reads a snapshot.
        try:
            return await self.robot.snapshot()
        except (YarboError, TimeoutError) as err:
            raise HomeAssistantError(f"robot: {err}") from err

    # -- telemetry

    @callback
    def _on_state(self, state: RobotState) -> None:
        now = time.time()
        for event in self.tracker.on_state(state, now):
            self._plan_event(event)
        self.recorder.on_state(state)
        signature = _signature(state)
        if signature != self._last_signature:
            self._last_signature = signature
            self._push_now()
            return
        if self._pending_push is None:
            delay = max(0.0, PUSH_INTERVAL_S - (time.monotonic() - self._last_push))
            self._pending_push = async_call_later(self.hass, delay, self._scheduled_push)

    @callback
    def _scheduled_push(self, _now: Any) -> None:
        self._pending_push = None
        self._push_now()

    @callback
    def _push_now(self) -> None:
        if self._pending_push is not None:
            self._pending_push()
            self._pending_push = None
        self._last_push = time.monotonic()
        self.check_firmware()
        self.async_set_updated_data(self.robot.state)

    @callback
    def _on_topic(self, leaf: str, value: Any) -> None:
        if leaf == "plan_feedback":
            feedback = PlanFeedback.from_wire(value)
            if feedback is None:
                return
            self.feedback = feedback
            for event in self.tracker.on_plan_feedback(feedback, time.time()):
                self._plan_event(event)
            return
        if leaf.startswith("app/"):
            command = leaf[4:]
            if command in MAP_COMMANDS:
                self.map_stale = True
            elif command in PLAN_COMMANDS:
                self.hass.async_create_task(self._refresh_plans())

    def _plan_event(self, event: LifecycleEvent) -> None:
        self.recorder.on_event(event, self.robot.state)
        async_dispatcher_send(self.hass, SIGNAL_PLAN_EVENT.format(self.entry.entry_id), event)
        if event.kind == "plan_finished":
            self.hass.async_create_task(self._tracker_store.async_save(self.tracker.to_dict()))
            # The robot adds its own record of the run once it has ended.
            self.entry.async_create_background_task(
                self.hass, self.async_refresh_runs(refresh=True), f"{DOMAIN} run history"
            )

    # -- run history

    @callback
    def _save_run(self, entry: dict[str, Any]) -> None:
        """Queue a write. Saves of one run come in bursts (rain, then a fault, in one
        frame); one at a time and in order, the last state always lands last. A tracked
        task, not a background one: HA waits for these when it stops."""
        self.hass.async_create_task(self._write_run(dict(entry)), f"{DOMAIN} run log write")

    async def _write_run(self, entry: dict[str, Any]) -> None:
        async with self._run_writes:
            try:
                await self.hass.async_add_executor_job(self.runlog.save, entry)
            except OSError as err:
                _LOGGER.warning("could not save the run record: %s", err)

    def _plan_info(self, plan_id: int | None) -> tuple[str | None, list[int]]:
        plan = next((p for p in self.plans if p.id == plan_id), None)
        return (plan.name, list(plan.area_ids)) if plan else (None, [])

    def _map_revision(self) -> str | None:
        return self.service.last.revision if self.service.last else None

    async def async_runs(self, refresh: bool = False) -> list[dict[str, Any]]:
        """Every run: the robot's history joined with yarbie's log, newest first."""
        try:
            history = await self.service.history(refresh)
        except (YarboError, TimeoutError) as err:
            _LOGGER.warning("reading the run history failed: %s", err)
            history = []
        log = await self.hass.async_add_executor_job(self.runlog.entries)
        if self.recorder.current is not None:
            log = [e for e in log if e.get("started") != self.recorder.current["started"]]
            log.append(self.recorder.current)
        return runs.merge(history, log)

    async def async_refresh_runs(self, refresh: bool = False) -> None:
        merged = await self.async_runs(refresh)
        self.last_run = next((r for r in merged if r["outcome"] != "running"), None)
        self.async_update_listeners()

    async def async_start_plan(self, plan_id: int, context: Context | None) -> None:
        """Start a plan, noting who asked so the run log can say. Refused while a run is
        under way (running, paused or waiting): a start would replace it."""
        if self.robot.busy:
            raise HomeAssistantError(
                "a plan is already running or paused; resume, stop or dock it first"
            )
        # Noted before sending: the robot can report the run as started while start_plan
        # still waits for it to confirm.
        self.recorder.note_start(await self._who(context))
        try:
            await self.run_control(lambda: self.robot.start_plan(plan_id))
        except HomeAssistantError:
            self.recorder.clear_start()
            raise

    async def _who(self, context: Context | None) -> str:
        if context is None:
            return "Home Assistant"
        if context.user_id and (user := await self.hass.auth.async_get_user(context.user_id)):
            return user.name or "a Home Assistant user"
        return "a Home Assistant automation" if context.parent_id else "Home Assistant"

    async def _refresh_plans(self, _now: Any = None) -> None:
        try:
            self.plans = await self.robot.plans()
        except (YarboError, TimeoutError) as err:
            _LOGGER.debug("reading plans failed: %s", err)
            return
        self.async_update_listeners()

    async def _keep_awake(self, _now: Any) -> None:
        try:
            await self.robot.wake()
        except (YarboError, TimeoutError) as err:
            _LOGGER.debug("wake failed: %s", err)

    # -- firmware rail

    @callback
    def check_firmware(self) -> None:
        firmware = self.robot.state.firmware
        issue_id = ISSUE_UNTESTED_FIRMWARE.format(self.entry.entry_id)
        if firmware is None or self.firmware_tested:
            ir.async_delete_issue(self.hass, DOMAIN, issue_id)
            return
        verified = sorted(self.robot.robot.session.registry.verified_firmware)
        ir.async_create_issue(
            self.hass,
            DOMAIN,
            issue_id,
            is_fixable=True,
            is_persistent=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key="untested_firmware",
            translation_placeholders={"firmware": firmware, "tested": ", ".join(verified)},
            data={"entry_id": self.entry.entry_id, "firmware": firmware, "tested": ", ".join(verified)},
        )

    # -- helpers for entities

    def selected_plan_id(self) -> int | None:
        """The plan the lawn mower entity starts: the select's choice, else the first."""
        chosen = next((p for p in self.plans if p.name == self.selected_plan), None)
        plan = chosen or (self.plans[0] if self.plans else None)
        return plan.id if plan else None

    def plan_name(self, plan_id: int | None) -> str | None:
        return next((p.name for p in self.plans if p.id == plan_id), None)

    def position_wgs84(self) -> tuple[float, float] | None:
        position = self.robot.state.position
        ref = self.service.last_reference
        if position is None or ref is None:
            return None
        return local_to_wgs84(ref, position[0], position[1])

    async def run_control(self, coro_fn: Callable[[], Any]) -> None:
        """A control (start, pause, lights...) with errors as HA errors."""
        try:
            self.check_controls()
            await coro_fn()
        except ReadOnlyError as err:
            raise HomeAssistantError(str(err)) from err
        except (YarboError, TimeoutError) as err:
            raise HomeAssistantError(f"robot: {err}") from err
