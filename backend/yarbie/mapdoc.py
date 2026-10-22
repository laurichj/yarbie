"""The robot's map as GeoJSON for the browser, and back.

The robot stores points in metres from each zone's ``ref`` (x west, y north). The
browser only sees WGS84. Converting back always uses the zone's own ``ref``, so an
untouched vertex comes back to within a millimetre of where it was.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import hashlib
import json
import math
from typing import Any

from yarbo_local.models import local_to_wgs84, wgs84_to_local

# The families the editor shows. Closed ones are polygons, the rest lines.
POLYGON_FAMILIES = ("areas", "nogozones", "novisionzones", "elec_fence")
LINE_FAMILIES = ("pathways", "sidewalks", "deadends")
FAMILIES = POLYGON_FAMILIES + LINE_FAMILIES

LocalPoint = tuple[float, float, float]  # x, y, phi
Ref = tuple[float, float]


def feature_key(family: str, object_id: Any) -> str:
    return f"{family}:{object_id}"


def map_revision(raw: Mapping[str, Any]) -> str:
    """A fingerprint of the map, to notice it changed underneath an edit."""
    canonical = json.dumps(raw, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


def parse_ref(raw: Any) -> Ref | None:
    if not isinstance(raw, Mapping):
        return None
    lat, lon = raw.get("latitude"), raw.get("longitude")
    if not isinstance(lat, int | float) or not isinstance(lon, int | float):
        return None
    if abs(lat) < 1e-6 and abs(lon) < 1e-6:
        return None
    return (float(lat), float(lon))


def local_points(record: Mapping[str, Any]) -> list[LocalPoint]:
    out: list[LocalPoint] = []
    for p in record.get("range") or []:
        if isinstance(p, Mapping) and isinstance(p.get("x"), int | float) and isinstance(
            p.get("y"), int | float
        ):
            phi = p.get("phi")
            out.append((float(p["x"]), float(p["y"]), float(phi) if isinstance(phi, int | float) else 0.0))
    return out


def is_closed_ring(points: Sequence[LocalPoint]) -> bool:
    return len(points) > 3 and points[0][:2] == points[-1][:2]


def site_reference(raw: Mapping[str, Any], fallback: Ref | None = None) -> Ref | None:
    for family in FAMILIES:
        for record in raw.get(family) or []:
            if isinstance(record, Mapping) and (ref := parse_ref(record.get("ref"))):
                return ref
    return fallback


def _lonlat(ref: Ref, x: float, y: float) -> list[float]:
    lat, lon = local_to_wgs84(ref, x, y)
    return [round(lon, 9), round(lat, 9)]


def to_feature_collection(raw: Mapping[str, Any], reference: Ref | None) -> dict[str, Any]:
    """Every zone, line and dock as GeoJSON; zones keep their id and record fields."""
    features: list[dict[str, Any]] = []
    site_ref = site_reference(raw, reference)
    for family in FAMILIES:
        for record in raw.get(family) or []:
            if not isinstance(record, Mapping):
                continue
            ref = parse_ref(record.get("ref")) or site_ref
            pts = local_points(record)
            if ref is None or not pts:
                continue
            coords = [_lonlat(ref, x, y) for x, y, _ in pts]
            if family in POLYGON_FAMILIES:
                if len(coords) < 3:
                    continue
                if coords[0] != coords[-1]:
                    coords.append(coords[0])
                geometry: dict[str, Any] = {"type": "Polygon", "coordinates": [coords]}
            else:
                if len(coords) < 2:
                    continue
                geometry = {"type": "LineString", "coordinates": coords}
            props: dict[str, Any] = {
                "family": family,
                "id": record.get("id"),
                "name": str(record.get("name") or ""),
                "enabled": bool(record.get("enable", True)),
            }
            if family == "areas":
                props["algorithm_type"] = record.get("algorithm_type")
                props["area_m2"] = record.get("area")
            if family in ("pathways", "sidewalks"):
                props["start_id"] = record.get("start_id")
                props["end_id"] = record.get("end_id")
            features.append(
                {
                    "type": "Feature",
                    "id": feature_key(family, record.get("id")),
                    "geometry": geometry,
                    "properties": props,
                }
            )
    if site_ref is not None:
        for dock in raw.get("allchargingData") or []:
            if not isinstance(dock, Mapping):
                continue
            point = dock.get("chargingPoint")
            if not isinstance(point, Mapping):
                continue
            features.append(
                {
                    "type": "Feature",
                    "id": feature_key("dock", dock.get("id")),
                    "geometry": {
                        "type": "Point",
                        "coordinates": _lonlat(site_ref, float(point["x"]), float(point["y"])),
                    },
                    "properties": {"family": "dock", "id": dock.get("id"), "name": "Dock"},
                }
            )
    return {"type": "FeatureCollection", "features": features}


def geometry_to_local(geometry: Mapping[str, Any], ref: Ref) -> list[tuple[float, float]]:
    """A Polygon's outer ring (closing point dropped) or a LineString, in local metres."""
    kind = geometry.get("type")
    coords: Any = geometry.get("coordinates") or []
    if kind == "Polygon":
        ring = list(coords[0]) if coords else []
        if len(ring) > 1 and ring[0] == ring[-1]:
            ring = ring[:-1]
    elif kind == "LineString":
        ring = list(coords)
    else:
        raise ValueError(f"unsupported geometry {kind!r}")
    out = []
    for lon, lat, *_ in ring:
        x, y = wgs84_to_local(ref, float(lat), float(lon))
        out.append((round(x, 3), round(y, 3)))
    return out


def with_headings(
    points: Sequence[tuple[float, float]], previous: Sequence[LocalPoint], *, closed: bool
) -> list[LocalPoint]:
    """Attach ``phi`` to each point: kept where a vertex did not move, else the heading to
    the next vertex (radians from +x toward +y, as the robot stores it)."""
    kept = {(round(x, 2), round(y, 2)): phi for x, y, phi in previous}
    out: list[LocalPoint] = []
    n = len(points)
    for i, (x, y) in enumerate(points):
        phi = kept.get((round(x, 2), round(y, 2)))
        if phi is None:
            j = (i + 1) % n if closed else min(i + 1, n - 1)
            k = j if j != i else max(i - 1, 0)
            nx, ny = points[k]
            phi = math.atan2(ny - y, nx - x) if k > i or closed else math.atan2(y - ny, x - nx)
            phi = round(phi, 3)
        out.append((x, y, phi))
    return out


def same_outline(
    a: Sequence[tuple[float, float]], b: Sequence[LocalPoint], tolerance: float = 0.01
) -> bool:
    """Whether two outlines match vertex for vertex within ``tolerance`` metres."""
    bb = [(x, y) for x, y, _ in b]
    if is_closed_ring(b):
        bb = bb[:-1]
    if len(a) != len(bb):
        return False
    return all(math.dist(p, q) <= tolerance for p, q in zip(a, bb, strict=True))
