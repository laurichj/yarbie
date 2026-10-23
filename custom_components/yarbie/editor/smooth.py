"""Thin out and round off outlines the robot drove.

A driven outline has a point every 30-40 cm and wobbles a few centimetres either way, so a
straight fence comes back as dozens of points and a tree as a lumpy polygon. Smoothing
drops points that do not move the line more than a tolerance, and replaces runs that
follow a circle with an even arc.

It only ever moves a line one way. An area only gets smaller, so the robot never mows
beyond what was driven; a no-go zone only gets bigger, so it never mows closer to what was
avoided. Every stretch is checked against the original before it is used, and a stretch
that fails keeps its original points.

All geometry here is in the robot's local metres.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
import math
from typing import Any, Literal

from .._vendor.yarbo_local.models import local_to_wgs84
from .diff import CROSSING_SAME_M, crossings
from .mapdoc import Ref, dock_point, feature_key, geometry_to_local, parse_ref, site_reference

Pt = tuple[float, float]
Direction = Literal["shrink", "grow"]

# Which way each family may move. Lines (pathways, sidewalks) are not smoothed.
DIRECTION: dict[str, Direction] = {"areas": "shrink", "nogozones": "grow"}

DEFAULT_TOLERANCE_M = 0.1524  # 6 in

# Points closer than this are one point (the robot repeats points when it pauses).
SAME_POINT_M = 0.01
# Side and distance tests allow this much: outlines come back rounded to the millimetre.
SLACK_M = 0.002
# A turn sharper than this is the robot backing up; the outline is left alone around it.
REVERSAL = math.radians(150)
# Points this close to a crossing, a reversal or a point to keep are left where they are.
PIN_M = 0.5
KEEP_NEAR_M = 1.0
# Points this close to a neighbouring outline are on a shared edge and stay, so smoothing
# two areas that meet does not open an unmown strip between them.
SHARED_M = 0.3

# What counts as a circle. The points may spread ARC_SPREAD of the tolerance either side of
# it, which leaves the rest of the tolerance for the straight edges drawn along it.
ARC_SPREAD = 2 / 3
ARC_RMS = 1 / 4
MIN_ARC_POINTS = 5
MIN_ARC_SWEEP = math.radians(30)
MIN_RADIUS_M = 0.2
MAX_RADIUS_M = 50.0
MAX_ARC_POINTS = 250
# Arc edges never turn more than this, however loose the tolerance.
MAX_ARC_STEP = math.radians(45)
MIN_CIRCLE_CORNERS = 8

GRID_M = 1.0


@dataclass
class Smoothed:
    points: list[Pt]
    before: int
    after: int
    area_before: float  # m², always positive
    area_after: float
    max_offset: float  # metres: the furthest any original point is from the new outline
    arcs: int = 0
    circle: bool = False
    pinned: int = 0  # points left untouched (crossings, reversals, points to keep)
    notes: list[str] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return self.after != self.before

    def public(self) -> dict[str, Any]:
        out = asdict(self)
        out.pop("points")
        return out


def smooth_feature(
    family: str,
    outline: Sequence[Pt],
    tolerance: float,
    *,
    arcs: bool = True,
    keep: Iterable[Pt] = (),
    neighbours: Iterable[Sequence[Pt]] = (),
) -> Smoothed:
    if family not in DIRECTION:
        raise ValueError(f"{family} cannot be smoothed")
    return smooth_ring(
        outline, tolerance, DIRECTION[family], arcs=arcs, keep=keep, neighbours=neighbours
    )


def smooth_ring(
    ring: Sequence[Pt],
    tolerance: float,
    direction: Direction,
    *,
    arcs: bool = True,
    keep: Iterable[Pt] = (),
    neighbours: Iterable[Sequence[Pt]] = (),
) -> Smoothed:
    """The outline ``ring`` (open: no repeated closing point) with points removed and
    circles rounded, never moving more than ``tolerance`` and only ever inward
    (``shrink``) or outward (``grow``).

    Points within ``KEEP_NEAR_M`` of a ``keep`` point (a pathway end, the dock) and within
    ``SHARED_M`` of a ``neighbours`` outline (closed rings) are left where they are."""
    if tolerance <= 0:
        raise ValueError("the tolerance must be more than zero")
    original = [(float(x), float(y)) for x, y in ring]
    pts = _clean(original)
    area = signed_area(pts)
    unchanged = Smoothed(original, len(original), len(original), abs(area), abs(area), 0.0)
    if len(pts) < 4 or abs(area) < 1e-6:
        unchanged.notes.append("Too small to smooth.")
        return unchanged

    shrink = direction == "shrink"
    orient = 1 if area > 0 else -1
    # Original points must stay on this side of every new edge (+ is left of travel).
    side = -orient if shrink else orient
    convex_sign = orient
    edges = _Grid(pts, closed=True)
    ctx = _Context(tolerance, side, shrink, convex_sign, edges)

    before_crossings = crossings(pts)
    pins = _pins(pts, before_crossings, list(keep), [_Grid(r, closed=True) for r in neighbours])

    if arcs and not pins:
        circle = _whole_circle(pts, ctx)
        if circle is not None and not _new_crossings(circle, before_crossings):
            result = _finish(original, pts, circle)
            result.circle = True
            result.arcs = 1
            return result

    start = min(pins) if pins else _sharpest(pts)
    n = len(pts)
    pts = pts[start:] + pts[:start]
    pins = sorted((p - start) % n for p in pins)
    anchors = pins if pins else [0]
    if anchors[0] != 0:
        anchors = [0, *anchors]

    pieces: list[_Piece] = []
    for t, a in enumerate(anchors):
        b = anchors[t + 1] if t + 1 < len(anchors) else n
        pieces += _stretch([pts[k % n] for k in range(a, b + 1)], ctx, arcs)

    # Pieces are each checked against the original, not against each other, so two can
    # still cross where the outline nearly meets itself. Put those back, a step at a time.
    reverted = 0
    while True:
        ring, owner = _assemble(pieces)
        added = _new_crossings(ring, before_crossings)
        if not added:
            break
        culprits = {owner[i] for p in added for i in _segments_at(ring, p)}
        if not any(pieces[k].demote(ctx) for k in culprits):
            unchanged.notes.append(
                "Smoothing would have made the outline cross itself, so it was left as it is."
            )
            return unchanged
        reverted += 1

    tidy = _tidy(ring, ctx, {pts[i] for i in pins})
    if len(tidy) < len(ring) and not _new_crossings(tidy, before_crossings):
        ring = tidy

    result = _finish(original, pts, ring)
    result.arcs = sum(1 for p in pieces if p.arc)
    result.pinned = len(pins)
    if reverted:
        result.notes.append("Some stretches kept more points where the outline nearly meets itself.")
    if before_crossings:
        result.notes.append(
            f"The outline already crosses itself in {len(before_crossings)} "
            f"place{'s' if len(before_crossings) != 1 else ''}; points there were left alone."
        )
    return result


# -- the pieces


@dataclass
class _Context:
    tolerance: float
    side: int
    shrink: bool
    convex_sign: int
    edges: _Grid


@dataclass
class _Piece:
    """A run of the original outline and what stands in for it; both share end points."""

    run: list[Pt]
    candidate: list[Pt]
    arc: bool = False

    def demote(self, ctx: _Context) -> bool:
        """Fall back a step (arc, then straight edges, then the original); False if this
        piece is already the original."""
        if self.arc:
            self.arc = False
            self.candidate = _straight(self.run, ctx)
            return True
        if self.candidate != self.run:
            self.candidate = self.run
            return True
        return False


def _stretch(stretch: list[Pt], ctx: _Context, arcs: bool) -> list[_Piece]:
    """One stretch between fixed points, as arcs where it follows a circle and as few
    straight edges as will do elsewhere."""
    if len(stretch) <= 2:
        return [_Piece(stretch, stretch)]
    found = _find_arcs(stretch, ctx) if arcs else []
    pieces: list[_Piece] = []
    cursor = 0
    for i, j, circle in found:
        if i > cursor:
            run = stretch[cursor : i + 1]
            pieces.append(_Piece(run, _straight(run, ctx)))
        run = stretch[i : j + 1]
        candidate = _arc(run, circle, ctx)
        if candidate is not None and _run_ok(candidate, run, ctx):
            pieces.append(_Piece(run, candidate, arc=True))
        else:
            pieces.append(_Piece(run, _straight(run, ctx)))
        cursor = j
    if cursor < len(stretch) - 1:
        run = stretch[cursor:]
        pieces.append(_Piece(run, _straight(run, ctx)))
    return pieces


def _assemble(pieces: list[_Piece]) -> tuple[list[Pt], list[int]]:
    """The closed ring, and which piece drew each of its edges (edge i runs from point i)."""
    ring: list[Pt] = []
    owner: list[int] = []
    for k, piece in enumerate(pieces):
        ring.extend(piece.candidate[:-1])
        owner.extend([k] * (len(piece.candidate) - 1))
    return ring, owner


def _new_crossings(ring: list[Pt], before: list[Pt]) -> list[Pt]:
    return [p for p in crossings(ring) if all(math.dist(p, q) > CROSSING_SAME_M for q in before)]


def _segments_at(ring: list[Pt], p: Pt) -> list[int]:
    n = len(ring)
    return [i for i in range(n) if _segment_distance(p, ring[i], ring[(i + 1) % n]) < 1e-6]


def _straight(run: list[Pt], ctx: _Context) -> list[Pt]:
    candidate = _simplify(run, ctx)
    return candidate if _run_ok(candidate, run, ctx) else run


def _simplify(run: list[Pt], ctx: _Context) -> list[Pt]:
    """Douglas-Peucker that only accepts an edge with every point it replaces on the
    allowed side of it, within the tolerance, and not crossing the outline elsewhere.

    Where a point is on the wrong side the run is split there, so the edges end up on the
    innermost points (areas) or outermost points (no-go zones) of the wobble."""
    n = len(run)
    keep = {0, n - 1}
    stack = [(0, n - 1)]
    if n > 2 and math.dist(run[0], run[-1]) < SAME_POINT_M:
        far = max(range(1, n - 1), key=lambda k: math.dist(run[0], run[k]))
        keep.add(far)
        stack = [(0, far), (far, n - 1)]
    while stack:
        a, b = stack.pop()
        if b - a < 2:
            continue
        k = _worst(run, a, b, ctx)
        if k is None and not ctx.edges.crosses(run[a], run[b]):
            continue
        if k is None:
            k = max(range(a + 1, b), key=lambda m: _segment_distance(run[m], run[a], run[b]))
        keep.add(k)
        stack.append((a, k))
        stack.append((k, b))
    return [run[k] for k in sorted(keep)]


def _worst(run: list[Pt], a: int, b: int, ctx: _Context) -> int | None:
    """The point that stops ``a``-``b`` replacing the points between: the deepest one on
    the wrong side, else the furthest beyond the tolerance."""
    pa, pb = run[a], run[b]
    length = math.dist(pa, pb)
    if length < SAME_POINT_M:
        return max(range(a + 1, b), key=lambda m: math.dist(pa, run[m]))
    dx, dy = (pb[0] - pa[0]) / length, (pb[1] - pa[1]) / length
    wrong, wrong_at = SLACK_M, None
    far, far_at = ctx.tolerance, None
    for m in range(a + 1, b):
        p = run[m]
        lateral = (dx * (p[1] - pa[1]) - dy * (p[0] - pa[0])) * ctx.side
        if -lateral > wrong:
            wrong, wrong_at = -lateral, m
        d = _segment_distance(p, pa, pb)
        if d > far:
            far, far_at = d, m
    return wrong_at if wrong_at is not None else far_at


@dataclass
class _Circle:
    cx: float
    cy: float
    r: float


def _find_arcs(stretch: list[Pt], ctx: _Context) -> list[tuple[int, int, _Circle]]:
    """Runs that follow a circle closely enough to be drawn as one, longest first from
    each start."""
    found: list[tuple[int, int, _Circle]] = []
    n = len(stretch)
    i = 0
    while i + MIN_ARC_POINTS <= n:
        best: tuple[int, _Circle] | None = None
        for j in range(i + MIN_ARC_POINTS - 1, min(n, i + MAX_ARC_POINTS)):
            run = stretch[i : j + 1]
            circle = _fit_circle(run)
            if circle is None or circle.r > MAX_RADIUS_M:
                if j - i > 2 * MIN_ARC_POINTS:
                    break
                continue
            verdict = _arc_verdict(run, circle, ctx.tolerance)
            if verdict == "stop":
                break
            if verdict == "ok":
                best = (j, circle)
        if best is None:
            i += 1
            continue
        found.append((i, best[0], best[1]))
        i = best[0]
    return found


def _arc_verdict(run: Sequence[Pt], circle: _Circle, tolerance: float) -> Literal["ok", "no", "stop"]:
    radii = [math.hypot(x - circle.cx, y - circle.cy) for x, y in run]
    spread = max(radii) - min(radii)
    if spread > 2 * ARC_SPREAD * tolerance:
        return "stop"
    if circle.r < MIN_RADIUS_M:
        return "no"
    if spread > ARC_SPREAD * tolerance:
        return "no"
    if math.sqrt(sum((d - circle.r) ** 2 for d in radii) / len(radii)) > ARC_RMS * tolerance:
        return "no"
    sweep = _sweep(run, circle)
    if sweep is None:
        return "stop"
    # A run so gently curved that one straight edge would do is not worth an arc.
    if abs(sweep) < MIN_ARC_SWEEP or circle.r * (1 - math.cos(min(abs(sweep), math.pi) / 2)) < tolerance:
        return "no"
    return "ok"


def _sweep(run: Sequence[Pt], circle: _Circle) -> float | None:
    """How far round the circle the run goes (signed), or None if it doubles back."""
    angles = [math.atan2(y - circle.cy, x - circle.cx) for x, y in run]
    total = 0.0
    sign = 0
    for a, b in zip(angles, angles[1:], strict=False):
        d = (b - a + math.pi) % (2 * math.pi) - math.pi
        s = 1 if d > 0 else -1
        if sign and s != sign or abs(d) > math.pi / 2:
            return None
        sign = s
        total += d
    return total


def _fit_circle(points: Sequence[Pt]) -> _Circle | None:
    """The least-squares circle (Kasa's algebraic fit), solved about the first point."""
    ox, oy = points[0]
    sx = sy = sxx = syy = sxy = sz = sxz = syz = 0.0
    for px, py in points:
        x, y = px - ox, py - oy
        z = x * x + y * y
        sx += x
        sy += y
        sxx += x * x
        syy += y * y
        sxy += x * y
        sz += z
        sxz += x * z
        syz += y * z
    n = float(len(points))
    m = ((sxx, sxy, sx), (sxy, syy, sy), (sx, sy, n))
    rhs = (-sxz, -syz, -sz)
    det = _det3(m)
    if abs(det) < 1e-12:
        return None
    d, e, f = (
        _det3(tuple(tuple(rhs[r] if c == col else m[r][c] for c in range(3)) for r in range(3))) / det
        for col in range(3)
    )
    r2 = d * d / 4 + e * e / 4 - f
    if r2 <= 0:
        return None
    return _Circle(ox - d / 2, oy - e / 2, math.sqrt(r2))


def _det3(m: Sequence[Sequence[float]]) -> float:
    return (
        m[0][0] * (m[1][1] * m[2][2] - m[1][2] * m[2][1])
        - m[0][1] * (m[1][0] * m[2][2] - m[1][2] * m[2][0])
        + m[0][2] * (m[1][0] * m[2][1] - m[1][1] * m[2][0])
    )


def _arc_radius(run: Sequence[Pt], circle: _Circle, convex: bool, ctx: _Context) -> tuple[float, bool]:
    """The radius to draw at and whether the corners go outside it (circumscribed).

    The new line must be on the allowed side of every point: for an area's outward bulge
    (convex) that is inside the innermost point, with corners on the circle so the edges
    are inside it too; for a no-go zone round a tree it is outside the outermost point,
    with edges touching the circle so the corners are outside it."""
    radii = [math.hypot(x - circle.cx, y - circle.cy) for x, y in run]
    outer = convex != ctx.shrink
    return (max(radii), True) if outer else (min(radii), False)


def _step(radius: float, ctx: _Context) -> float:
    chord = (1 - ARC_SPREAD) * ctx.tolerance
    if chord >= radius:
        return MAX_ARC_STEP
    return min(MAX_ARC_STEP, 2 * math.acos(1 - chord / radius))


def _arc(run: list[Pt], circle: _Circle, ctx: _Context) -> list[Pt] | None:
    sweep = _sweep(run, circle)
    if sweep is None:
        return None
    convex = (1 if sweep > 0 else -1) == ctx.convex_sign
    radius, outer = _arc_radius(run, circle, convex, ctx)
    count = max(2, math.ceil(abs(sweep) / _step(radius, ctx)))
    delta = sweep / count
    start = math.atan2(run[0][1] - circle.cy, run[0][0] - circle.cx)
    out: list[Pt] = [run[0]]
    # Outside the circle the edges touch it, so the corners sit at r / cos(step / 2).
    corner = radius / math.cos(delta / 2) if outer else radius
    for k in range(1, count):
        t = start + k * delta
        p = (round(circle.cx + corner * math.cos(t), 3), round(circle.cy + corner * math.sin(t), 3))
        if math.dist(p, out[-1]) >= SAME_POINT_M:
            out.append(p)
    if math.dist(out[-1], run[-1]) < SAME_POINT_M:
        out.pop()
    out.append(run[-1])
    return out


def _whole_circle(pts: list[Pt], ctx: _Context) -> list[Pt] | None:
    """A whole outline that is a circle (a tree), as a regular polygon."""
    circle = _fit_circle(pts)
    if circle is None or circle.r < MIN_RADIUS_M:
        return None
    radii = [math.hypot(x - circle.cx, y - circle.cy) for x, y in pts]
    if max(radii) - min(radii) > ARC_SPREAD * ctx.tolerance:
        return None
    if _sweep([*pts, pts[0]], circle) is None:
        return None
    radius, outer = (max(radii), True) if not ctx.shrink else (min(radii), False)
    count = max(MIN_CIRCLE_CORNERS, math.ceil(2 * math.pi / _step(radius, ctx)))
    corner = radius / math.cos(math.pi / count) if outer else radius
    start = math.atan2(pts[0][1] - circle.cy, pts[0][0] - circle.cx)
    turn = 2 * math.pi / count * ctx.convex_sign
    ring = [
        (round(circle.cx + corner * math.cos(start + k * turn), 3), round(circle.cy + corner * math.sin(start + k * turn), 3))
        for k in range(count)
    ]
    return ring if _run_ok([*ring, ring[0]], [*pts, pts[0]], ctx, whole=True) else None


def _run_ok(candidate: list[Pt], run: list[Pt], ctx: _Context, whole: bool = False) -> bool:
    """Whether ``candidate`` can stand in for ``run``: it crosses no part of the outline,
    lies on the allowed side of it, and no point of either is more than the tolerance
    from the other."""
    limit = ctx.tolerance + SLACK_M
    originals = set(run)
    for a, b in zip(candidate, candidate[1:], strict=False):
        if ctx.edges.crosses(a, b):
            return False
        mid = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
        for p in (mid,) if b in originals else (mid, b):
            if ctx.edges.distance(p, SLACK_M) <= SLACK_M:
                continue
            if ctx.edges.contains(p) != ctx.shrink:
                return False
    new = _Grid(candidate, closed=False)
    if any(new.distance(p, limit) > limit for p in run):
        return False
    old = _Grid(run, closed=False)
    return all(old.distance(p, limit) <= limit for p in _samples(candidate, ctx.tolerance))


def _tidy(ring: list[Pt], ctx: _Context, fixed: set[Pt]) -> list[Pt]:
    """Drop corners left close together where pieces meet (an arc's end beside a
    straight edge's), where the outline passes the same checks without them."""
    out = list(ring)
    i = 0
    while i < len(out) and len(out) > 4:
        n = len(out)
        u, v, w = out[i - 1], out[i], out[(i + 1) % n]
        if v not in fixed and min(math.dist(u, v), math.dist(v, w)) <= ctx.tolerance:
            nearby = [out[(i + k) % n] for k in range(-3, 4) if k != 0]
            if _can_drop(u, v, w, nearby, ctx):
                del out[i]
                continue
        i += 1
    return out


def _can_drop(u: Pt, v: Pt, w: Pt, nearby: list[Pt], ctx: _Context) -> bool:
    """Whether ``u``-``w`` can stand in for ``u``-``v``-``w``. ``nearby`` is the outline
    round ``v`` without it, for the original points that were closest to ``v``'s edges."""
    limit = ctx.tolerance + SLACK_M
    if ctx.edges.crosses(u, w):
        return False
    mid = ((u[0] + w[0]) / 2, (u[1] + w[1]) / 2)
    if ctx.edges.distance(mid, SLACK_M) > SLACK_M and ctx.edges.contains(mid) != ctx.shrink:
        return False
    if any(ctx.edges.distance(p, limit) > limit for p in _samples([u, w], ctx.tolerance)):
        return False
    local = _Grid(nearby, closed=False)
    reach = max(math.dist(u, v), math.dist(v, w)) + limit
    for i in ctx.edges._near(v, v, reach):
        p = ctx.edges.segments[i][0]
        if math.dist(p, v) <= reach and min(
            _segment_distance(p, u, w), local.distance(p, limit)
        ) > limit:
            return False
    return True


def _samples(line: Sequence[Pt], spacing: float) -> Iterable[Pt]:
    for a, b in zip(line, line[1:], strict=False):
        steps = max(1, math.ceil(math.dist(a, b) / spacing))
        for k in range(steps):
            t = k / steps
            yield (a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1]))
    if line:
        yield line[-1]


def _finish(original: list[Pt], cleaned: list[Pt], ring: list[Pt]) -> Smoothed:
    area_before = abs(signed_area(cleaned))
    area_after = abs(signed_area(ring))
    grid = _Grid(ring, closed=True)
    offset = max(grid.distance(p, math.inf) for p in original)
    return Smoothed(ring, len(original), len(ring), area_before, area_after, round(offset, 3))


# -- the editor's features


def smooth_features(
    raw: Mapping[str, Any],
    reference: Ref | None,
    edited: Mapping[str, Any],
    ids: Iterable[str],
    tolerance: float,
    *,
    arcs: bool = True,
    keep_shared: bool = True,
) -> list[dict[str, Any]]:
    """Smoothed outlines for the editor's features ``ids`` (GeoJSON, as edited).

    Each is worked on in its zone's own frame, as ``diff`` does. Pathway ends (as edited)
    and the dock are kept; with ``keep_shared``, so are edges shared with other areas."""
    site_ref = site_reference(raw, reference)
    records = {
        feature_key(family, r.get("id")): r
        for family in DIRECTION
        for r in raw.get(family) or []
        if isinstance(r, Mapping)
    }
    features = [
        f for f in edited.get("features") or []
        if isinstance(f, Mapping) and isinstance(f.get("geometry"), Mapping)
    ]
    by_id = {str(f.get("id")): f for f in features}
    frames: dict[Ref, tuple[list[Pt], list[tuple[str, list[Pt]]]]] = {}

    def frame(ref: Ref) -> tuple[list[Pt], list[tuple[str, list[Pt]]]]:
        """Points to keep and area outlines in ``ref``'s metres (a site has one ref, usually)."""
        if ref not in frames:
            keep: list[Pt] = []
            areas: list[tuple[str, list[Pt]]] = []
            for f in features:
                family = (f.get("properties") or {}).get("family")
                kind = f["geometry"].get("type")
                if family in ("pathways", "sidewalks") and kind == "LineString":
                    line = geometry_to_local(f["geometry"], ref)
                    keep += [line[0], line[-1]] if line else []
                elif family == "areas" and kind == "Polygon":
                    areas.append((str(f.get("id")), geometry_to_local(f["geometry"], ref)))
            if (dock := dock_point(raw)) is not None and site_ref is not None:
                lat, lon = local_to_wgs84(site_ref, *dock)
                keep += geometry_to_local({"type": "LineString", "coordinates": [[lon, lat]]}, ref)
            frames[ref] = (keep, areas)
        return frames[ref]

    out: list[dict[str, Any]] = []
    for key in ids:
        feature = by_id.get(key)
        if feature is None:
            raise LookupError(f"no feature {key}")
        family = str((feature.get("properties") or {}).get("family"))
        if family not in DIRECTION or feature["geometry"].get("type") != "Polygon":
            raise ValueError(f"{key} is not an area or no-go zone")
        ref = parse_ref((records.get(key) or {}).get("ref")) or site_ref
        if ref is None:
            raise ValueError("the robot has no GPS reference, so shapes cannot be measured")
        keep, areas = frame(ref)
        shared = family == "areas" and keep_shared
        result = smooth_feature(
            family,
            geometry_to_local(feature["geometry"], ref),
            tolerance,
            arcs=arcs,
            keep=keep if family == "areas" else (),
            neighbours=[ring for k, ring in areas if k != key] if shared else (),
        )
        ring = [_lonlat(ref, x, y) for x, y in result.points]
        out.append(
            {
                "id": key,
                "geometry": {"type": "Polygon", "coordinates": [[*ring, ring[0]]]},
                "stats": result.public(),
            }
        )
    return out


def _lonlat(ref: Ref, x: float, y: float) -> list[float]:
    lat, lon = local_to_wgs84(ref, x, y)
    return [round(lon, 9), round(lat, 9)]


# -- small geometry


def signed_area(ring: Sequence[Pt]) -> float:
    n = len(ring)
    return 0.5 * sum(
        ring[i][0] * ring[(i + 1) % n][1] - ring[(i + 1) % n][0] * ring[i][1] for i in range(n)
    )


def _clean(ring: list[Pt]) -> list[Pt]:
    out: list[Pt] = []
    for p in ring:
        if not out or math.dist(p, out[-1]) >= SAME_POINT_M:
            out.append(p)
    while len(out) > 1 and math.dist(out[0], out[-1]) < SAME_POINT_M:
        out.pop()
    return out


def _turn(a: Pt, b: Pt, c: Pt) -> float:
    v1 = (b[0] - a[0], b[1] - a[1])
    v2 = (c[0] - b[0], c[1] - b[1])
    return math.atan2(v1[0] * v2[1] - v1[1] * v2[0], v1[0] * v2[0] + v1[1] * v2[1])


def _sharpest(pts: list[Pt]) -> int:
    n = len(pts)
    return max(range(n), key=lambda i: abs(_turn(pts[i - 1], pts[i], pts[(i + 1) % n])))


def _pins(pts: list[Pt], crossed: list[Pt], keep: list[Pt], neighbours: list[_Grid]) -> list[int]:
    """Points to leave alone: near a crossing, a reversal, a point the caller keeps, or a
    neighbouring outline."""
    n = len(pts)
    spots = list(crossed)
    spots += [pts[i] for i in range(n) if abs(_turn(pts[i - 1], pts[i], pts[(i + 1) % n])) > REVERSAL]
    pinned = {i for i, p in enumerate(pts) if any(math.dist(p, s) <= PIN_M for s in spots)}
    pinned |= {i for i, p in enumerate(pts) if any(math.dist(p, k) <= KEEP_NEAR_M for k in keep)}
    pinned |= {
        i for i, p in enumerate(pts) if any(g.distance(p, SHARED_M) <= SHARED_M for g in neighbours)
    }
    return sorted(pinned)


def _segment_distance(p: Pt, a: Pt, b: Pt) -> float:
    dx, dy = b[0] - a[0], b[1] - a[1]
    length2 = dx * dx + dy * dy
    if length2 == 0:
        return math.dist(p, a)
    t = max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / length2))
    return math.dist(p, (a[0] + t * dx, a[1] + t * dy))


def _orient(a: Pt, b: Pt, c: Pt) -> float:
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def _properly_cross(p1: Pt, p2: Pt, q1: Pt, q2: Pt) -> bool:
    """Whether two segments cross at a point inside both (touching does not count).

    No slack here: an edge that grazes two corners can dip outside between them, so even
    a millimetre's crossing is a crossing."""
    eps = 1e-9

    def sign(v: float) -> int:
        return 0 if abs(v) < eps else (1 if v > 0 else -1)

    d1, d2 = sign(_orient(q1, q2, p1)), sign(_orient(q1, q2, p2))
    d3, d4 = sign(_orient(p1, p2, q1)), sign(_orient(p1, p2, q2))
    return d1 * d2 < 0 and d3 * d4 < 0


class _Grid:
    """A polyline's segments bucketed by square, for nearby-segment questions."""

    def __init__(self, points: Sequence[Pt], *, closed: bool) -> None:
        self.points = list(points)
        n = len(self.points)
        count = n if closed else n - 1
        self.segments = [(self.points[i], self.points[(i + 1) % n]) for i in range(max(count, 0))]
        self.cells: Mapping[tuple[int, int], list[int]] = defaultdict(list)
        for i, (a, b) in enumerate(self.segments):
            for cell in _cells(a, b, 0.0):
                self.cells[cell].append(i)  # type: ignore[index]

    def _near(self, a: Pt, b: Pt, pad: float) -> set[int]:
        found: set[int] = set()
        for cell in _cells(a, b, pad):
            found.update(self.cells.get(cell, ()))
        return found

    def crosses(self, a: Pt, b: Pt) -> bool:
        return any(_properly_cross(a, b, *self.segments[i]) for i in self._near(a, b, 0.0))

    def distance(self, p: Pt, limit: float) -> float:
        """The distance from ``p`` to the nearest segment, or more than ``limit``."""
        if math.isinf(limit):
            near: Iterable[int] = range(len(self.segments))
        else:
            near = self._near(p, p, limit)
        return min((_segment_distance(p, *self.segments[i]) for i in near), default=math.inf)

    def contains(self, p: Pt) -> bool:
        """Even-odd inside test against the closed outline."""
        x, y = p
        inside = False
        for (x1, y1), (x2, y2) in self.segments:
            if (y1 > y) != (y2 > y) and x < x1 + (y - y1) * (x2 - x1) / (y2 - y1):
                inside = not inside
        return inside


def _cells(a: Pt, b: Pt, pad: float) -> Iterable[tuple[int, int]]:
    x0 = math.floor((min(a[0], b[0]) - pad) / GRID_M)
    x1 = math.floor((max(a[0], b[0]) + pad) / GRID_M)
    y0 = math.floor((min(a[1], b[1]) - pad) / GRID_M)
    y1 = math.floor((max(a[1], b[1]) + pad) / GRID_M)
    for cx in range(x0, x1 + 1):
        for cy in range(y0, y1 + 1):
            yield (cx, cy)
