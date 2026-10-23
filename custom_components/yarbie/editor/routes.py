"""Plan routes and run progress from the robot's own feedback, as GeoJSON-ready lon/lat."""

from __future__ import annotations

from typing import Any

from .._vendor.yarbo_local.feedback import PlanFeedback
from .._vendor.yarbo_local.models import local_to_wgs84
from .mapdoc import Ref
from .runs import finished_parts


def _lonlat(ref: Ref, points: tuple[tuple[float, float], ...]) -> list[list[float]]:
    out = []
    for x, y in points:
        lat, lon = local_to_wgs84(ref, x, y)
        out.append([round(lon, 8), round(lat, 8)])
    return out


def progress(
    feedback: PlanFeedback, raw: dict[str, Any], ref: Ref, plan_names: dict[int, str]
) -> dict[str, Any]:
    """The current (or last) run: each area's route split into the part mowed and the rest."""
    # The same reading of finishIds as the run history's, so the two cannot disagree.
    done_parts = finished_parts(raw)
    paths = []
    for area in feedback.areas:
        kind = "edge" if area.path_type == 1 else "fill"
        done_all = area.area_id is not None and done_parts.get(area.area_id, {}).get(kind, False)
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
