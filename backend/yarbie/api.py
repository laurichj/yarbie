"""HTTP routes. Geometry crosses this boundary as GeoJSON in WGS84 only."""

from __future__ import annotations

import json
import time
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from yarbo_local.exceptions import YarboError
from yarbo_local.models import local_to_wgs84
from yarbo_local.payloads import APP_MOWER_AREA_KEYS

from . import mapdoc
from .service import ConflictError, MapService
from .sources.base import ReadOnlyError

router = APIRouter(prefix="/api")


def service(request: Request) -> MapService:
    return request.app.state.service


Service = Annotated[MapService, Depends(service)]


class FeatureCollection(BaseModel):
    type: str = "FeatureCollection"
    features: list[dict[str, Any]]


class ApplyBody(BaseModel):
    features: FeatureCollection
    revision: str


class PlanBody(BaseModel):
    name: str = Field(min_length=1)
    area_ids: list[int] = Field(min_length=1)


def _fail(err: Exception) -> HTTPException:
    if isinstance(err, ConflictError):
        return HTTPException(409, str(err))
    if isinstance(err, ReadOnlyError):
        return HTTPException(403, str(err))
    if isinstance(err, ValueError):
        return HTTPException(400, str(err))
    if isinstance(err, YarboError | TimeoutError):
        return HTTPException(502, f"robot: {err}")
    raise err


# -- map


@router.get("/status")
async def status(svc: Service) -> dict[str, Any]:
    try:
        st = await svc.source.status()
    except (YarboError, TimeoutError) as err:
        return {"source": svc.source.name, "writable": False, "connected": False, "error": str(err)}
    position = None
    ref = svc.last_reference
    if st.position and ref:
        lat, lon = local_to_wgs84(ref, st.position[0], st.position[1])
        position = {"lon": lon, "lat": lat, "phi": st.position[2]}
    return {
        "source": svc.source.name,
        "writable": svc.source.writable and st.connected,
        "connected": st.connected,
        "activity": st.activity,
        "battery": st.battery,
        "busy": st.busy,
        "firmware": st.firmware,
        "position": position,
        **st.detail,
    }


@router.post("/client-log")
async def client_log(entry: dict[str, Any], svc: Service) -> dict[str, bool]:
    """What the page reports when opened with ?debug: errors and click diagnostics."""
    path = svc.backups.dir.parent / "client.log"
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists() or path.stat().st_size < 2_000_000:
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"at": time.time(), **entry}, default=str)[:20_000] + "\n")
    return {"ok": True}


@router.get("/imagery")
async def imagery(request: Request, svc: Service) -> dict[str, Any]:
    """Imagery sources that need a key (the free ones are built into the page)."""
    google = request.app.state.google
    if google is None:
        return {"sources": [], "hint": "Set GOOGLE_MAPS_API_KEY in .env for Google satellite."}
    try:
        return {"sources": [await google.source(svc.last_reference)]}
    except Exception as err:  # a bad key must not break the map
        return {"sources": [], "error": f"Google imagery unavailable: {err}"}


@router.get("/map")
async def get_map(svc: Service) -> dict[str, Any]:
    try:
        return (await svc.load()).public()
    except Exception as err:
        raise _fail(err) from err


@router.get("/export.geojson")
async def export_map(svc: Service) -> JSONResponse:
    view = await svc.load()
    return JSONResponse(
        view.public()["features"],
        headers={"Content-Disposition": 'attachment; filename="yarbo-map.geojson"'},
    )


@router.post("/map/preview")
async def preview(body: FeatureCollection, svc: Service) -> dict[str, Any]:
    try:
        ops = await svc.preview(body.model_dump())
    except Exception as err:
        raise _fail(err) from err
    return {"ops": [op.public() for op in ops], "writable": svc.source.writable}


@router.post("/map/apply")
async def apply(body: ApplyBody, svc: Service) -> dict[str, Any]:
    try:
        return await svc.apply(body.features.model_dump(), body.revision)
    except Exception as err:
        raise _fail(err) from err


@router.get("/backups")
async def backups(svc: Service) -> list[dict[str, Any]]:
    return svc.backups.entries()


@router.get("/backups/{name}")
async def backup(name: str, svc: Service) -> dict[str, Any]:
    try:
        raw = svc.backups.load(name)
    except FileNotFoundError as err:
        raise HTTPException(404, "no such backup") from err
    return {"raw": raw, "features": mapdoc.to_feature_collection(raw, svc.last_reference)}


# -- plans and area settings


@router.get("/progress")
async def progress(svc: Service) -> dict[str, Any]:
    """The current (or last) run: each area's route split into the part mowed and the rest."""
    try:
        got = await svc.robot.plan_progress()
        ref = svc.last_reference or (await svc.load()).reference
        plans = {p["id"]: p["name"] for p in await svc.source.plans()}
    except Exception as err:
        raise _fail(err) from err
    if got is None or ref is None:
        return {"running": False, "plan": None}
    feedback, raw = got

    def lonlat(points: tuple[tuple[float, float], ...]) -> list[list[float]]:
        out = []
        for x, y in points:
            lat, lon = local_to_wgs84(ref, x, y)
            out.append([round(lon, 8), round(lat, 8)])
        return out

    finished = _finished(raw)
    paths = []
    for area in feedback.areas:
        kind = "edge" if area.path_type == 1 else "fill"
        done_all = (area.area_id, kind) in finished
        done = area.path if done_all else area.done
        left = () if done_all else area.remaining
        paths.append(
            {
                "area_id": area.area_id,
                "kind": kind,
                "done": lonlat(done) if len(done) > 1 else [],
                "left": lonlat(left) if len(left) > 1 else [],
                "fraction": 1.0 if done_all else (area.clean_index / max(1, len(area.path) - 1) if area.path else 0.0),
            }
        )
    return {
        "running": feedback.state in (1, 2, 3),
        "state": feedback.state,
        "plan": {"id": feedback.plan_id, "name": plans.get(feedback.plan_id or -1)},
        "current_area": feedback.current_area_id,
        "progress": feedback.progress,
        "remaining_s": feedback.remaining_s,
        "total_s": feedback.total_s,
        "duration_s": feedback.duration_s,
        "started": feedback.start_time,
        "area_ids": list(feedback.area_ids),
        "paths": paths,
    }


def _finished(raw: dict[str, Any]) -> set[tuple[int | None, str]]:
    """Parts the robot reports done: move_type 1 ends an area's fill, 0 its edge pass.
    A bare id (older firmware) means the whole area."""
    out: set[tuple[int | None, str]] = set()
    for done in raw.get("finishIds") or []:
        if isinstance(done, dict) and isinstance(done.get("id"), int):
            out.add((done["id"], "fill" if done.get("move_type") == 1 else "edge"))
        elif isinstance(done, int):
            out |= {(done, "fill"), (done, "edge")}
    return out


@router.get("/heatmap")
async def wear_heatmap(svc: Service, days: int = 0, refresh: bool = False) -> dict[str, Any]:
    """Estimated wear: run history spread over today's map (no tracks are recorded)."""
    try:
        return await svc.heatmap(days or None, refresh)
    except Exception as err:
        raise _fail(err) from err


@router.get("/plans")
async def plans(svc: Service) -> list[dict[str, Any]]:
    try:
        return await svc.source.plans()
    except Exception as err:
        raise _fail(err) from err


@router.post("/plans")
async def create_plan(body: PlanBody, svc: Service) -> dict[str, Any]:
    try:
        plan_id = await svc.robot.save_plan(body.name, body.area_ids, None)
    except Exception as err:
        raise _fail(err) from err
    return {"id": plan_id}


@router.put("/plans/{plan_id}")
async def update_plan(plan_id: int, body: PlanBody, svc: Service) -> dict[str, Any]:
    try:
        await svc.robot.save_plan(body.name, body.area_ids, plan_id)
    except Exception as err:
        raise _fail(err) from err
    return {"id": plan_id}


@router.delete("/plans/{plan_id}")
async def delete_plan(plan_id: int, svc: Service) -> dict[str, Any]:
    try:
        await svc.robot.delete_plan(plan_id)
    except Exception as err:
        raise _fail(err) from err
    return {"deleted": plan_id}


@router.get("/plans/{plan_id}/route")
async def route(plan_id: int, svc: Service) -> dict[str, Any]:
    """The robot's own route for a plan, computed without moving it."""
    try:
        feedback = await svc.robot.preview_route(plan_id)
        ref = svc.last_reference or (await svc.load()).reference
    except Exception as err:
        raise _fail(err) from err
    features: list[dict[str, Any]] = []
    if feedback is not None and ref is not None:
        for area in feedback.areas:
            if len(area.path) < 2:
                continue
            coords = []
            for x, y in area.path:
                lat, lon = local_to_wgs84(ref, x, y)
                coords.append([lon, lat])
            features.append(
                {
                    "type": "Feature",
                    "geometry": {"type": "LineString", "coordinates": coords},
                    "properties": {
                        "area_id": area.area_id,
                        "kind": "edge" if area.path_type == 1 else "fill",
                    },
                }
            )
    return {
        "type": "FeatureCollection",
        "features": features,
        "total_s": feedback.total_s if feedback else None,
    }


@router.get("/areas/{area_id}/mower-params")
async def mower_params(area_id: int, svc: Service) -> dict[str, Any]:
    try:
        params = await svc.robot.mower_area_params(area_id)
    except Exception as err:
        raise _fail(err) from err
    if params is None:
        raise HTTPException(404, f"the robot has no area {area_id}")
    return {"params": params, "editable": [k for k in APP_MOWER_AREA_KEYS if k != "id"]}


@router.patch("/areas/{area_id}/mower-params")
async def update_mower_params(
    area_id: int, changes: dict[str, Any], svc: Service
) -> dict[str, Any]:
    try:
        status = await svc.robot.status()
        if status.busy:
            raise ConflictError("The robot is running or paused in a plan. Stop it first.")
        sent = await svc.robot.update_mower_area_params(area_id, changes)
    except Exception as err:
        raise _fail(err) from err
    return {"sent": sent}
