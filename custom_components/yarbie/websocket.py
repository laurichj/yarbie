"""The Yard editor's API: websocket commands on HA's own connection, admins only.

Geometry crosses this boundary as GeoJSON in WGS84 only.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
import functools
from typing import Any

import probatio

from homeassistant.components import persistent_notification, websocket_api
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from ._vendor.yarbo_local.exceptions import YarboError
from ._vendor.yarbo_local.payloads import APP_MOWER_AREA_KEYS
from .const import DOMAIN, OPT_GOOGLE_MAPS_KEY
from .coordinator import YarbieCoordinator
from .editor import mapdoc, routes
from .editor.service import ConflictError, ReadOnlyError
from .imagery import GoogleImagery
from .jobs import Job

Handler = Callable[[HomeAssistant, websocket_api.ActiveConnection, dict[str, Any]], Awaitable[None]]
Command = websocket_api.WebSocketCommandHandler
ENTRY = probatio.Optional("entry_id")


def _coordinator(hass: HomeAssistant, msg: dict[str, Any]) -> YarbieCoordinator:
    entries = [
        e
        for e in hass.config_entries.async_entries(DOMAIN)
        if e.state is ConfigEntryState.LOADED
        and (msg.get("entry_id") is None or e.entry_id == msg["entry_id"])
    ]
    if not entries:
        raise LookupError("no yarbie robot is set up")
    return entries[0].runtime_data


def _code(err: Exception) -> str:
    if isinstance(err, LookupError):
        return "not_found"
    if isinstance(err, ConflictError):
        return "conflict"
    if isinstance(err, ReadOnlyError):
        return "read_only"
    if isinstance(err, ValueError):
        return "invalid"
    if isinstance(err, YarboError | TimeoutError):
        return "robot"
    return "unknown_error"


def _command(schema: dict[Any, Any]) -> Callable[[Handler], Command]:
    """An admin-only async command whose exceptions become error replies."""

    def wrap(fn: Handler) -> Command:
        @websocket_api.websocket_command({**schema, ENTRY: str})
        @websocket_api.require_admin
        @websocket_api.async_response
        @functools.wraps(fn)
        async def handler(
            hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
        ) -> None:
            try:
                await fn(hass, connection, msg)
            except (LookupError, ConflictError, ReadOnlyError, ValueError, YarboError, TimeoutError) as err:
                connection.send_error(msg["id"], _code(err), str(err) or type(err).__name__)

        return handler

    return wrap


def _actor(connection: websocket_api.ActiveConnection) -> str:
    user = connection.user
    return (user.name if user and user.name else None) or "unknown user"


# -- robot and map


@_command({probatio.Required("type"): "yarbie/info"})
async def ws_info(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    c = _coordinator(hass, msg)
    reason = None
    try:
        c.check_writes()
    except ReadOnlyError as err:
        reason = str(err)
    connection.send_result(
        msg["id"],
        {
            "entry_id": c.entry.entry_id,
            "serial": c.serial,
            "source": c.robot.name,
            "connected": c.robot.connected,
            "writable": c.writable,
            "read_only_reason": reason,
            "busy": c.robot.busy,
            "firmware": c.robot.state.firmware,
            "map_stale": c.map_stale,
        },
    )


@_command({probatio.Required("type"): "yarbie/map"})
async def ws_map(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    c = _coordinator(hass, msg)
    view = await c.service.load()
    c.map_stale = False
    connection.send_result(msg["id"], view.public())


@_command({probatio.Required("type"): "yarbie/preview", probatio.Required("features"): dict})
async def ws_preview(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    c = _coordinator(hass, msg)
    ops = await c.service.preview(msg["features"])
    connection.send_result(msg["id"], {"ops": [op.public() for op in ops], "writable": c.writable})


@_command(
    {
        probatio.Required("type"): "yarbie/apply",
        probatio.Required("features"): dict,
        probatio.Required("revision"): str,
    }
)
async def ws_apply(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    c = _coordinator(hass, msg)
    actor = _actor(connection)
    result = await c.service.apply(msg["features"], msg["revision"], actor)
    c.map_stale = False
    if result["applied"]:
        _announce(hass, c, actor, result)
    connection.send_result(msg["id"], result)


def _announce(hass: HomeAssistant, c: YarbieCoordinator, actor: str, result: dict[str, Any]) -> None:
    """Tell HA's users the robot's map changed, by whom, and where the backup is."""
    count = len(result["applied"])
    names = ", ".join(f"{op['kind']} {op['name'] or op['family']}" for op in result["applied"][:5])
    more = "" if count <= 5 else f" and {count - 5} more"
    error = f"\n\nStopped early: {result['error']}" if result.get("error") else ""
    message = (
        f"{actor} sent {count} change{'s' if count != 1 else ''} from the Yard editor: "
        f"{names}{more}.\n\nBackup before the change: `{result['backup']}`{error}"
    )
    persistent_notification.async_create(
        hass, message, title="Yarbo map changed", notification_id=f"{DOMAIN}_map_changed"
    )
    hass.bus.async_fire(
        f"{DOMAIN}_map_changed",
        {"serial": c.serial, "actor": actor, "changes": count, "backup": result["backup"]},
    )
    c.async_update_listeners()


@_command({probatio.Required("type"): "yarbie/export"})
async def ws_export(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    view = await _coordinator(hass, msg).service.load()
    connection.send_result(msg["id"], view.public()["features"])


@_command({probatio.Required("type"): "yarbie/backups/list"})
async def ws_backups(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    c = _coordinator(hass, msg)
    connection.send_result(msg["id"], await hass.async_add_executor_job(c.service.backups.entries))


@_command({probatio.Required("type"): "yarbie/backups/get", probatio.Required("name"): str})
async def ws_backup(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    c = _coordinator(hass, msg)
    try:
        raw = await hass.async_add_executor_job(c.service.backups.load, msg["name"])
    except FileNotFoundError as err:
        raise LookupError("no such backup") from err
    features = mapdoc.to_feature_collection(raw, c.service.last_reference)
    connection.send_result(msg["id"], {"raw": raw, "features": features})


@_command({probatio.Required("type"): "yarbie/imagery"})
async def ws_imagery(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    """Imagery sources that need a key (the free ones are built into the panel)."""
    c = _coordinator(hass, msg)
    key = (c.options.get(OPT_GOOGLE_MAPS_KEY) or "").strip()
    if not key:
        connection.send_result(
            msg["id"],
            {"sources": [], "hint": "Add a Google Maps key in the yarbie options for Google satellite."},
        )
        return
    google = GoogleImagery(hass, key, async_get_clientsession(hass))
    try:
        sources = [await google.source(c.service.last_reference)]
    except Exception as err:  # a bad key must not break the map
        connection.send_result(msg["id"], {"sources": [], "error": f"Google imagery unavailable: {err}"})
        return
    connection.send_result(msg["id"], {"sources": sources})


# -- runs, plans and area settings


@_command({probatio.Required("type"): "yarbie/progress"})
async def ws_progress(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    """The current (or last) run: each area's route split into the part mowed and the rest."""
    c = _coordinator(hass, msg)
    got = await c.robot.plan_progress()
    ref = c.service.last_reference or (await c.service.load()).reference
    if got is None or ref is None:
        connection.send_result(msg["id"], {"running": False, "plan": None})
        return
    feedback, raw = got
    names = {p.id: p.name for p in c.plans}
    connection.send_result(msg["id"], routes.progress(feedback, raw, ref, names))


@_command(
    {probatio.Required("type"): "yarbie/heatmap", probatio.Optional("days", default=0): int, probatio.Optional("refresh", default=False): bool}
)
async def ws_heatmap(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    """Estimated wear: run history spread over today's map (no tracks are recorded)."""
    c = _coordinator(hass, msg)
    connection.send_result(msg["id"], await c.service.heatmap(msg["days"] or None, msg["refresh"]))


def _plan_dict(p: Any) -> dict[str, Any]:
    return {"id": p.id, "name": p.name, "area_ids": list(p.area_ids), "self_order": p.self_order}


@_command({probatio.Required("type"): "yarbie/plans/list"})
async def ws_plans(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    c = _coordinator(hass, msg)
    c.plans = await c.robot.plans()
    connection.send_result(msg["id"], [_plan_dict(p) for p in c.plans])


@_command(
    {
        probatio.Required("type"): "yarbie/plans/save",
        probatio.Optional("plan_id"): probatio.Any(int, None),
        probatio.Required("name"): probatio.All(str, probatio.Length(min=1)),
        probatio.Required("area_ids"): probatio.All([int], probatio.Length(min=1)),
    }
)
async def ws_plan_save(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    c = _coordinator(hass, msg)
    plan_id = await c.service.save_plan(msg["name"], msg["area_ids"], msg.get("plan_id"))
    await c._refresh_plans()
    connection.send_result(msg["id"], {"id": plan_id})


@_command({probatio.Required("type"): "yarbie/plans/delete", probatio.Required("plan_id"): int})
async def ws_plan_delete(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    c = _coordinator(hass, msg)
    await c.service.delete_plan(msg["plan_id"])
    await c._refresh_plans()
    connection.send_result(msg["id"], {"deleted": msg["plan_id"]})


@_command({probatio.Required("type"): "yarbie/area_params/get", probatio.Required("area_id"): int})
async def ws_area_params(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    c = _coordinator(hass, msg)
    params = await c.robot.mower_area_params(msg["area_id"])
    if params is None:
        raise LookupError(f"the robot has no area {msg['area_id']}")
    connection.send_result(
        msg["id"], {"params": params, "editable": [k for k in APP_MOWER_AREA_KEYS if k != "id"]}
    )


@_command(
    {probatio.Required("type"): "yarbie/area_params/save", probatio.Required("area_id"): int, probatio.Required("changes"): dict}
)
async def ws_area_params_save(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    c = _coordinator(hass, msg)
    sent = await c.service.update_mower_area_params(msg["area_id"], msg["changes"])
    connection.send_result(msg["id"], {"sent": sent})


# -- jobs


@_command({probatio.Required("type"): "yarbie/route/start", probatio.Required("plan_id"): int})
async def ws_route_start(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    """Start (or join) the robot's route preview for a plan on the current map."""
    c = _coordinator(hass, msg)
    c.check_controls()  # the preview takes the controller
    view = c.service.last if c.service.last and not c.map_stale else await c.service.load()
    plan_id = msg["plan_id"]

    async def work() -> dict[str, Any]:
        feedback = await c.robot.preview_route(plan_id)
        return routes.route(feedback, c.service.last_reference)

    job = c.jobs.start("route", f"{plan_id}:{view.revision}", work)
    connection.send_result(msg["id"], job.public())


@_command({probatio.Required("type"): "yarbie/job/get", probatio.Required("job_id"): str})
async def ws_job_get(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    job = _coordinator(hass, msg).jobs.get(msg["job_id"])
    if job is None:
        raise LookupError("no such job")
    connection.send_result(msg["id"], job.public())


@websocket_api.websocket_command(
    {probatio.Required("type"): "yarbie/job/subscribe", probatio.Required("job_id"): str, ENTRY: str}
)
@websocket_api.require_admin
@callback
def ws_job_subscribe(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """The job now, then once more when it ends. Reconnecting panels subscribe again."""
    try:
        job = _coordinator(hass, msg).jobs.get(msg["job_id"])
    except LookupError as err:
        connection.send_error(msg["id"], "not_found", str(err))
        return
    if job is None:
        connection.send_error(msg["id"], "not_found", "no such job")
        return

    @callback
    def finished(done: Job) -> None:
        connection.send_message(websocket_api.event_message(msg["id"], done.public()))

    connection.subscriptions[msg["id"]] = job.listen(finished)
    connection.send_result(msg["id"])
    connection.send_message(websocket_api.event_message(msg["id"], job.public()))


COMMANDS = (
    ws_info, ws_map, ws_preview, ws_apply, ws_export, ws_backups, ws_backup, ws_imagery,
    ws_progress, ws_heatmap, ws_plans, ws_plan_save, ws_plan_delete, ws_area_params,
    ws_area_params_save, ws_route_start, ws_job_get, ws_job_subscribe,
)


@callback
def async_register(hass: HomeAssistant) -> None:
    for command in COMMANDS:
        websocket_api.async_register_command(hass, command)
