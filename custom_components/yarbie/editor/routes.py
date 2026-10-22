"""Plan routes and run progress from the robot's own feedback, as GeoJSON-ready lon/lat."""

from __future__ import annotations

from typing import Any

from .._vendor.yarbo_local.feedback import PlanFeedback
from .._vendor.yarbo_local.models import local_to_wgs84
from .mapdoc import Ref


def _lonlat(ref: Ref, points: tuple[tuple[float, float], ...]) -> list[list[float]]:
    out = []
    for x, y in points:
        lat, lon = local_to_wgs84(ref, x, y)
        out.append([round(lon, 8), round(lat, 8)])
    return out


def finished(raw: dict[str, Any]) -> set[tuple[int | None, str]]:
    """Parts the robot reports done: move_type 1 ends an area's fill, 0 its edge pass.
    A bare id (older firmware) means the whole area."""
    out: set[tuple[int | None, str]] = set()
    for done in raw.get("finishIds") or []:
        if isinstance(done, dict) and isinstance(done.get("id"), int):
            out.add((done["id"], "fill" if done.get("move_type") == 1 else "edge"))
        elif isinstance(done, int):
            out |= {(done, "fill"), (done, "edge")}
    return out


def progress(
    feedback: PlanFeedback, raw: dict[str, Any], ref: Ref, plan_names: dict[int, str]
) -> dict[str, Any]:
    """The current (or last) run: each area's route split into the part mowed and the rest."""
    done_parts = finished(raw)
    paths = []
    for area in feedback.areas:
        kind = "edge" if area.path_type == 1 else "fill"
        done_all = (area.area_id, kind) in done_parts
        done = area.path if done_all else area.done
        left = () if done_all else area.remaining
        paths.append(
            {
                "area_id": area.area_id,
                "kind": kind,
                "done": _lonlat(ref, done) if len(done) > 1 else [],
                "left": _lonlat(ref, left) if len(left) > 1 else [],
                "fraction": 1.0
                if done_all
                else (area.clean_index / max(1, len(area.path) - 1) if area.path else 0.0),
            }
        )
    return {
        "running": feedback.state in (1, 2, 3),
        "state": feedback.state,
        "plan": {"id": feedback.plan_id, "name": plan_names.get(feedback.plan_id or -1)},
        "current_area": feedback.current_area_id,
        "progress": feedback.progress,
        "remaining_s": feedback.remaining_s,
        "total_s": feedback.total_s,
        "duration_s": feedback.duration_s,
        "started": feedback.start_time,
        "area_ids": list(feedback.area_ids),
        "paths": paths,
    }


def route(feedback: PlanFeedback | None, ref: Ref | None) -> dict[str, Any]:
    """The robot's own route for a plan as a FeatureCollection, with its time estimate."""
    features: list[dict[str, Any]] = []
    if feedback is not None and ref is not None:
        for area in feedback.areas:
            if len(area.path) < 2:
                continue
            features.append(
                {
                    "type": "Feature",
                    "geometry": {"type": "LineString", "coordinates": _lonlat(ref, area.path)},
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
