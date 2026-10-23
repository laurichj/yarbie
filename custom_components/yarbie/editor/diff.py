"""Compare an edited map with the robot's and list the writes that would get there."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
import math
from typing import Any, Literal

from .._vendor.yarbo_local.models import local_to_wgs84

from .mapdoc import (
    LocalPoint,
    Ref,
    dock_point,
    feature_key,
    geometry_to_local,
    is_closed_ring,
    local_points,
    parse_ref,
    same_outline,
    site_reference,
    with_headings,
)

OpKind = Literal["create", "update", "delete"]

# What the robot can be told, per family. Everything listed is a verified command in
# yarbo-local's registry, captured from the phone app on firmware 3.14.11.
SUPPORTED: dict[str, set[str]] = {
    "nogozones": {"create", "update", "delete"},
    "pathways": {"create", "update", "delete"},
    "sidewalks": {"create", "update", "delete"},
    "areas": {"update"},
}
UNSUPPORTED_REASON = {
    ("areas", "create"): "New areas are mapped by driving the robot round them in the app.",
    ("areas", "delete"): "There is no verified command to delete an area. Delete it in the app.",
}
EDITABLE_FAMILIES = frozenset(SUPPORTED)

# The robot links a pathway to the dock only when its first point is this close to the
# charging point (0.27 m linked, 0.31 m did not).
DOCK_LINK_M = 0.27


@dataclass
class Warning:
    text: str
    # Where to look, in the op's local metres; sent to the browser as [lon, lat].
    points: list[tuple[float, float]] = field(default_factory=list)


@dataclass
class Op:
    kind: OpKind
    family: str
    key: str
    id: int | None
    name: str
    supported: bool
    reason: str | None = None
    warnings: list[Warning] = field(default_factory=list)
    changes: list[str] = field(default_factory=list)
    # What to send: local points with headings, the reference they are relative to.
    points: list[LocalPoint] | None = None
    ref: Ref | None = None
    enabled: bool = True
    algorithm_type: int | None = None

    def public(self) -> dict[str, Any]:
        out = asdict(self)
        out.pop("points")
        out.pop("ref")
        out["warnings"] = [
            {
                "text": w.text,
                "points": [_lonlat(self.ref, x, y) for x, y in w.points] if self.ref else [],
            }
            for w in self.warnings
        ]
        return out

    def warn(self, text: str, points: Sequence[tuple[float, float]] = ()) -> None:
        self.warnings.append(Warning(text, list(points)))


def _lonlat(ref: Ref, x: float, y: float) -> list[float]:
    lat, lon = local_to_wgs84(ref, x, y)
    return [round(lon, 9), round(lat, 9)]


def _records(raw: Mapping[str, Any], family: str) -> dict[str, Mapping[str, Any]]:
    return {
        feature_key(family, r.get("id")): r
        for r in raw.get(family) or []
        if isinstance(r, Mapping)
    }


def _dock_point(raw: Mapping[str, Any]) -> tuple[float, float] | None:
    return dock_point(raw)


def _support(kind: OpKind, family: str) -> tuple[bool, str | None]:
    if kind in SUPPORTED.get(family, set()):
        return True, None
    return False, UNSUPPORTED_REASON.get(
        (family, kind), f"{family} cannot be changed from here yet; export it instead."
    )


def _int(value: Any) -> int | None:
    return int(value) if isinstance(value, int | float) and not isinstance(value, bool) else None


def compute(
    raw: Mapping[str, Any], reference: Ref | None, edited: Mapping[str, Any]
) -> list[Op]:
    """The ops that turn ``raw`` into ``edited`` (a GeoJSON FeatureCollection).

    Deletes come first, then updates, then creates, which is the order they are applied.
    """
    site_ref = site_reference(raw, reference)
    edited_features = {
        str(f.get("id")): f
        for f in edited.get("features") or []
        if isinstance(f, Mapping) and (f.get("properties") or {}).get("family") in EDITABLE_FAMILIES
    }
    deletes: list[Op] = []
    updates: list[Op] = []
    creates: list[Op] = []

    for family in sorted(EDITABLE_FAMILIES):
        closed = family != "pathways" and family != "sidewalks"
        for key, record in _records(raw, family).items():
            name = str(record.get("name") or "")
            feature = edited_features.pop(key, None)
            if feature is None:
                ok, reason = _support("delete", family)
                deletes.append(
                    Op("delete", family, key, _int(record.get("id")), name, ok, reason)
                )
                continue
            op = _update(family, key, record, feature, site_ref, closed)
            if op is not None:
                updates.append(op)

    for key, feature in edited_features.items():
        props = feature.get("properties") or {}
        family = str(props["family"])
        if site_ref is None:
            raise ValueError("the robot has no GPS reference, so new shapes cannot be placed")
        closed = family not in ("pathways", "sidewalks")
        outline = geometry_to_local(feature.get("geometry") or {}, site_ref)
        ok, reason = _support("create", family)
        creates.append(
            Op(
                "create",
                family,
                key,
                None,
                str(props.get("name") or "").strip() or _default_name(family),
                ok,
                reason,
                points=_wire_points(family, with_headings(outline, [], closed=closed), None),
                ref=site_ref,
                enabled=bool(props.get("enabled", True)),
            )
        )

    ops = deletes + updates + creates
    _check(ops, raw)
    return ops


def _default_name(family: str) -> str:
    return {"nogozones": "No-go zone", "pathways": "Pathway", "sidewalks": "Sidewalk"}.get(
        family, "Area"
    )


def _wire_points(
    family: str, points: list[LocalPoint], previous: Sequence[LocalPoint] | None
) -> list[LocalPoint]:
    """Close the ring where the robot stores it closed: always for no-go zones (the app
    repeats the first vertex), and otherwise as the record already was."""
    close = family == "nogozones" or (previous is not None and is_closed_ring(previous))
    if close and points and points[0][:2] != points[-1][:2]:
        return [*points, points[0]]
    return points


def _update(
    family: str,
    key: str,
    record: Mapping[str, Any],
    feature: Mapping[str, Any],
    site_ref: Ref | None,
    closed: bool,
) -> Op | None:
    props = feature.get("properties") or {}
    ref = parse_ref(record.get("ref")) or site_ref
    if ref is None:
        return None
    before = local_points(record)
    outline = geometry_to_local(feature.get("geometry") or {}, ref)
    changes: list[str] = []
    points: list[LocalPoint] | None = None
    if not same_outline(outline, before):
        changes.append("shape")
        points = _wire_points(family, with_headings(outline, before, closed=closed), before)
    old_name = str(record.get("name") or "")
    name = str(props.get("name") or old_name)
    if name != old_name:
        changes.append("name")
    enabled = bool(props.get("enabled", record.get("enable", True)))
    if family == "nogozones" and enabled != bool(record.get("enable", True)):
        changes.append("enabled")
    algorithm_type = _int(props.get("algorithm_type"))
    if family == "areas" and algorithm_type is not None and algorithm_type != _int(
        record.get("algorithm_type")
    ):
        changes.append("pattern")
    else:
        algorithm_type = None
    if not changes:
        return None
    if points is None and family != "areas":
        points = before  # these saves always send the whole record
    ok, reason = _support("update", family)
    return Op(
        "update",
        family,
        key,
        _int(record.get("id")),
        name,
        ok,
        reason,
        changes=changes,
        points=points,
        ref=ref,
        enabled=enabled,
        algorithm_type=algorithm_type,
    )


def _open_ring(points: Sequence[tuple[float, float]]) -> list[tuple[float, float]]:
    pts = list(points)
    return pts[:-1] if len(pts) > 3 and pts[0] == pts[-1] else pts


def _check(ops: list[Op], raw: Mapping[str, Any]) -> None:
    """Attach warnings: shapes the robot will reject or that break docking."""
    dock = _dock_point(raw)
    for op in ops:
        if op.points is None:
            continue
        flat = [(x, y) for x, y, _ in op.points]
        if op.family in ("nogozones", "areas"):
            ring = _open_ring(flat)
            if len(ring) < 3:
                op.warn("A polygon needs at least three corners.")
            else:
                # Outlines the robot drove often cross themselves already; only crossings
                # this edit adds are worth a warning.
                before: list[tuple[float, float]] = []
                if op.kind == "update":
                    record = _records(raw, op.family).get(op.key) or {}
                    before = crossings(_open_ring([(x, y) for x, y, _ in local_points(record)]))
                added = [
                    p for p in crossings(ring) if all(math.dist(p, q) > CROSSING_SAME_M for q in before)
                ]
                if added:
                    where = "one place" if len(added) == 1 else f"{len(added)} places"
                    op.warn(
                        f"This edit makes the outline cross itself in {where}; "
                        "the robot may reject it.",
                        added,
                    )
        if op.family == "areas" and "shape" in op.changes:
            op.warn(
                "Area reshapes use the app's in-place save, which was verified for names and "
                "patterns but rarely for new outlines. Check the area in the app afterwards."
            )
        if op.family == "pathways" and dock is not None and flat:
            was_linked = False
            if op.kind == "update":
                record = _records(raw, "pathways").get(op.key) or {}
                was_linked = _int(record.get("start_id")) == 0
            near = math.dist(flat[0], dock) <= DOCK_LINK_M
            if was_linked and not near:
                op.warn(
                    f"The first point moves more than {DOCK_LINK_M} m from the dock, so this "
                    "pathway loses its dock link. Without one, plans started from the dock "
                    "fail (WP005).",
                    [flat[0], dock],
                )
    linked_after = _dock_links_after(ops, raw, dock)
    if dock is not None and linked_after == 0 and any(o.family == "pathways" for o in ops):
        for op in ops:
            if op.family == "pathways":
                op.warn("After this change no pathway starts at the dock.", [dock])


def _dock_links_after(
    ops: list[Op], raw: Mapping[str, Any], dock: tuple[float, float] | None
) -> int:
    if dock is None:
        return 0
    starts: dict[str, tuple[float, float] | None] = {}
    for key, record in _records(raw, "pathways").items():
        pts = local_points(record)
        starts[key] = (pts[0][0], pts[0][1]) if pts else None
    for op in ops:
        if op.family != "pathways":
            continue
        if op.kind == "delete":
            starts.pop(op.key, None)
        elif op.points:
            starts[op.key] = (op.points[0][0], op.points[0][1])
    return sum(1 for p in starts.values() if p is not None and math.dist(p, dock) <= DOCK_LINK_M)


# Two crossings this close are taken to be the same one, moved slightly by the edit.
CROSSING_SAME_M = 0.05

Pt = tuple[float, float]


def crossings(ring: Sequence[Pt]) -> list[Pt]:
    """Where a closed outline crosses itself.

    A sweep along x compares only edges whose x ranges overlap, which keeps outlines of a
    thousand driven points quick enough to check on every edit.
    """
    n = len(ring)
    if n < 4:
        return []
    edges = sorted(
        ((min(ring[i][0], ring[(i + 1) % n][0]), max(ring[i][0], ring[(i + 1) % n][0]), i) for i in range(n)),
    )
    found: list[Pt] = []
    active: list[tuple[float, int]] = []  # (max x, edge index)
    for lo, hi, i in edges:
        active = [(h, j) for h, j in active if h >= lo]
        a1, a2 = ring[i], ring[(i + 1) % n]
        for _, j in active:
            if abs(i - j) == 1 or abs(i - j) == n - 1:
                continue  # neighbours share a corner
            hit = _intersection(a1, a2, ring[j], ring[(j + 1) % n])
            if hit is not None:
                found.append(hit)
        active.append((hi, i))
    return found


def _intersection(p1: Pt, p2: Pt, q1: Pt, q2: Pt) -> Pt | None:
    def orient(a: Pt, b: Pt, c: Pt) -> float:
        return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])

    d1, d2 = orient(q1, q2, p1), orient(q1, q2, p2)
    d3, d4 = orient(p1, p2, q1), orient(p1, p2, q2)
    if (d1 > 0) == (d2 > 0) or (d3 > 0) == (d4 > 0):
        return None
    t = d1 / (d1 - d2)
    return (p1[0] + t * (p2[0] - p1[0]), p1[1] + t * (p2[1] - p1[1]))
