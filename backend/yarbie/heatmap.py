"""An estimate of where the robot drives most, from its run history and today's map.

The robot keeps no record of where it actually went: only, per finished run, which plan
ran, which areas were mowed and edged (``finishIds``), how many times it went home to
charge (one work segment per trip), and the edge settings used. Yarbo's cloud keeps
nothing more. So this models each run:

* every fill of an area crosses each spot of it once;
* every edge pass drives the area's border once per perimeter lap, and the row ends of
  a fill turn there too;
* no-go zones inside a mowed area get their border driven once per fill when the run
  mowed around them;
* every trip between the dock and an area, and between areas, follows the pathways
  (the robot links areas by pathways and crosses an area between its pathway ends).

Counts go into a grid of ``CELL_M`` metre cells in the map frame. The result is relative
wear, not a measurement: routes are today's map, and turns or retries are not known.
"""

from __future__ import annotations

from collections import defaultdict, deque
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
import math
import time
from typing import Any

from yarbo_local.models import local_to_wgs84

from .mapdoc import local_points, site_reference

CELL_M = 1.0
BORDER_M = 1.0  # width of the band a perimeter lap or a row end wears
SAMPLE_M = 0.5  # spacing of samples along a line

Pt = tuple[float, float]
Cell = tuple[int, int]


@dataclass
class Estimate:
    runs: int = 0
    first: float | None = None
    last: float | None = None
    trips: int = 0
    grid: dict[Cell, float] = field(default_factory=lambda: defaultdict(float))
    pathway_passes: dict[int, float] = field(default_factory=lambda: defaultdict(float))
    area_fills: dict[int, float] = field(default_factory=lambda: defaultdict(float))


def estimate(
    raw: Mapping[str, Any],
    history: Sequence[Mapping[str, Any]],
    reference: tuple[float, float] | None,
    *,
    since: float | None = None,
) -> dict[str, Any]:
    ref = site_reference(raw, reference)
    if ref is None:
        raise ValueError("the map has no GPS reference")
    areas = {
        int(a["id"]): _open([(x, y) for x, y, _ in local_points(a)])
        for a in raw.get("areas") or []
        if isinstance(a, Mapping) and isinstance(a.get("id"), int)
    }
    names = {int(a["id"]): str(a.get("name") or "") for a in raw.get("areas") or [] if isinstance(a, Mapping)}
    nogos = [_open([(x, y) for x, y, _ in local_points(z)]) for z in raw.get("nogozones") or []]
    pathways = {
        int(p["id"]): p for p in raw.get("pathways") or [] if isinstance(p, Mapping) and isinstance(p.get("id"), int)
    }
    dock = _dock(raw)
    network = _Network(areas, pathways, dock)

    est = Estimate()
    for run in history:
        start = run.get("startTime")
        if since is not None and (not isinstance(start, int | float) or start < since):
            continue
        _add_run(est, run, areas, nogos, network)
    cells = _cells(est.grid, ref)
    hottest = sorted(est.pathway_passes.items(), key=lambda kv: -kv[1])[:5]
    return {
        "runs": est.runs,
        "first": est.first,
        "last": est.last,
        "trips": est.trips,
        "max": max((w for *_, w in cells), default=0),
        "cell_m": CELL_M,
        "cells": cells,
        "pathways": [
            {"id": pid, "name": str(pathways[pid].get("name") or pid), "passes": round(n)}
            for pid, n in hottest
            if pid in pathways
        ],
        "areas": [
            {"id": aid, "name": names.get(aid, str(aid)), "fills": round(n, 1)}
            for aid, n in sorted(est.area_fills.items(), key=lambda kv: -kv[1])
        ],
        "dock_area": names.get(network.dock_area) if network.dock_area is not None else None,
    }


# -- one run


def _add_run(
    est: Estimate,
    run: Mapping[str, Any],
    areas: Mapping[int, list[Pt]],
    nogos: Sequence[list[Pt]],
    network: _Network,
) -> None:
    plan_areas = [a for a in (run.get("plan") or {}).get("areaIds") or [] if a in areas]
    if not plan_areas:
        return
    est.runs += 1
    start = run.get("startTime")
    if isinstance(start, int | float):
        est.first = start if est.first is None else min(est.first, start)
        est.last = start if est.last is None else max(est.last, start)

    fills: dict[int, float] = defaultdict(float)
    edges: dict[int, float] = defaultdict(float)
    order: list[int] = []
    for done in run.get("finishIds") or []:
        if not isinstance(done, Mapping):
            continue
        aid = done.get("id")
        if not isinstance(aid, int) or aid not in areas:
            continue
        if aid not in order:
            order.append(aid)
        # move_type 1 ends the fill of an area, 0 its edge pass.
        if done.get("move_type") == 1:
            fills[aid] += 1
        else:
            edges[aid] += 1
    unfinished = [a for a in plan_areas if a not in order]
    path = run.get("area_path") or []
    index = run.get("area_path_index")
    if unfinished and path and isinstance(index, int) and index > 0:
        fills[unfinished[0]] += min(1.0, index / len(path))  # stopped part way through
        order.append(unfinished[0])

    params = run.get("mower_params") or {}
    laps = _per_area(params, "perimeter_mowing_lapss", 1)
    around_nogos = _per_area(params, "mower_ngz_edge_sws", True)

    for aid, n in fills.items():
        est.area_fills[aid] += n
        _fill(est.grid, areas[aid], n)
        _border(est.grid, areas[aid], n)  # row ends turn along the edge
        if around_nogos.get(aid, True):
            for zone in nogos:
                if zone and _inside(_centroid(zone), areas[aid]):
                    _border(est.grid, zone, n)
    for aid, n in edges.items():
        _border(est.grid, areas[aid], n * max(1, int(laps.get(aid, 1))))

    # Trips: out through the areas in the order they finished, then home; each further
    # work segment is another round trip, to the area being mowed when it went to charge.
    segments = max(1, len(run.get("startTimeV") or []))
    visits = order or plan_areas
    stops: list[int | None] = [None, *visits, None]
    for k in range(1, segments):
        stops += [visits[min(len(visits) - 1, k * len(visits) // segments)], None]
    for a, b in zip(stops, stops[1:], strict=False):
        if a != b:
            if b is None:
                est.trips += 1
            for line, pathway_id in network.route(a, b):
                _line(est.grid, line, 1.0)
                if pathway_id is not None:
                    est.pathway_passes[pathway_id] += 1


def _per_area(params: Mapping[str, Any], key: str, default: Any) -> dict[int, Any]:
    ids = params.get("area_ids") or []
    values = params.get(key) or []
    return {a: (values[i] if i < len(values) else default) for i, a in enumerate(ids)}


# -- how areas connect


DOCK = 0  # the robot's own id for the dock in pathway links


class _Network:
    """Areas joined by pathways. The dock is the area it stands in, or a stop of its own
    (pathways may start at it, id 0) when it stands outside every area."""

    def __init__(
        self, areas: Mapping[int, list[Pt]], pathways: Mapping[int, Mapping[str, Any]], dock: Pt | None
    ) -> None:
        self.dock = dock
        self.dock_area = next((a for a, ring in areas.items() if dock and _inside(dock, ring)), None)
        self.dock_node: int | None = self.dock_area if self.dock_area is not None else (DOCK if dock else None)
        self.edges: dict[int, list[tuple[int, int | None, list[Pt]]]] = defaultdict(list)
        for pid, p in pathways.items():
            line = [(x, y) for x, y, _ in local_points(p)]
            s = self._node(p.get("start_id"))
            e = self._node(p.get("end_id"))
            if len(line) < 2 or s is None or e is None:
                continue
            known = {*areas, DOCK}
            if s in known and e in known and s != e:
                self.edges[s].append((e, pid, line))
                self.edges[e].append((s, pid, line[::-1]))
        if self.dock_node == DOCK and not self.edges.get(DOCK) and dock is not None and areas:
            # A dock outside the areas with no pathway from it: assume the short way in.
            near = min(areas, key=lambda a: _distance_to_ring(dock, areas[a]))
            way = [dock, min(areas[near], key=lambda q: math.dist(dock, q))]
            self.edges[DOCK].append((near, None, way))
            self.edges[near].append((DOCK, None, way[::-1]))

    def _node(self, raw_id: Any) -> int | None:
        """An area id, with 0 (the dock) read as the area the dock is in."""
        if not isinstance(raw_id, int) or raw_id < 0:
            return None
        return self.dock_node if raw_id == DOCK else raw_id

    def route(self, a: int | None, b: int | None) -> list[tuple[list[Pt], int | None]]:
        """Lines driven from ``a`` to ``b``: pathways, and straight crossings of areas
        between where one pathway ends and the next begins."""
        start = self.dock_node if a is None else a
        goal = self.dock_node if b is None else b
        if start is None or goal is None:
            return []
        hops = self._hops(start, goal)
        if hops is None:
            return []
        out: list[tuple[list[Pt], int | None]] = []
        here: Pt | None = self.dock if a is None else None
        for pid, line in hops:
            if here is not None:
                out.append(([here, line[0]], None))
            out.append((line, pid))
            here = line[-1]
        if b is None and here is not None and self.dock is not None:
            out.append(([here, self.dock], None))
        return out

    def _hops(self, start: int, goal: int) -> list[tuple[int | None, list[Pt]]] | None:
        if start == goal:
            return []
        seen = {start}
        queue: deque[tuple[int, list[tuple[int | None, list[Pt]]]]] = deque([(start, [])])
        while queue:
            node, path = queue.popleft()
            for nxt, pid, line in self.edges.get(node, []):
                if nxt in seen:
                    continue
                if nxt == goal:
                    return [*path, (pid, line)]
                seen.add(nxt)
                queue.append((nxt, [*path, (pid, line)]))
        return None


# -- drawing into the grid


def _cell(p: Pt) -> Cell:
    return (math.floor(p[0] / CELL_M), math.floor(p[1] / CELL_M))


def _line(grid: dict[Cell, float], line: Sequence[Pt], weight: float) -> None:
    touched: set[Cell] = set()
    for a, b in zip(line, line[1:], strict=False):
        steps = max(1, int(math.dist(a, b) / SAMPLE_M))
        for i in range(steps + 1):
            t = i / steps
            touched.add(_cell((a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1]))))
    for c in touched:  # one pass wears a cell once, however slowly it crosses it
        grid[c] += weight


def _border(grid: dict[Cell, float], ring: Sequence[Pt], weight: float) -> None:
    if len(ring) < 3:
        return
    reach = max(0, round(BORDER_M / CELL_M / 2))
    touched: set[Cell] = set()
    closed = [*ring, ring[0]]
    for a, b in zip(closed, closed[1:], strict=False):
        steps = max(1, int(math.dist(a, b) / SAMPLE_M))
        for i in range(steps + 1):
            t = i / steps
            cx, cy = _cell((a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1])))
            for dx in range(-reach, reach + 1):
                for dy in range(-reach, reach + 1):
                    touched.add((cx + dx, cy + dy))
    for c in touched:
        grid[c] += weight


def _fill(grid: dict[Cell, float], ring: Sequence[Pt], weight: float) -> None:
    """Every cell whose centre is inside ``ring``, by scanlines."""
    if len(ring) < 3:
        return
    ys = [p[1] for p in ring]
    n = len(ring)
    for j in range(math.floor(min(ys) / CELL_M), math.floor(max(ys) / CELL_M) + 1):
        y = (j + 0.5) * CELL_M
        xs: list[float] = []
        for k in range(n):
            (x1, y1), (x2, y2) = ring[k], ring[(k + 1) % n]
            if (y1 > y) != (y2 > y):
                xs.append(x1 + (y - y1) * (x2 - x1) / (y2 - y1))
        xs.sort()
        for left, right in zip(xs[::2], xs[1::2], strict=False):
            for i in range(math.ceil(left / CELL_M - 0.5), math.floor(right / CELL_M - 0.5) + 1):
                grid[(i, j)] += weight


def _cells(grid: Mapping[Cell, float], ref: tuple[float, float]) -> list[list[float]]:
    out: list[list[float]] = []
    for (i, j), w in grid.items():
        if w <= 0:
            continue
        lat, lon = local_to_wgs84(ref, (i + 0.5) * CELL_M, (j + 0.5) * CELL_M)
        out.append([round(lon, 7), round(lat, 7), round(w, 2)])
    return out


# -- geometry helpers


def _open(ring: list[Pt]) -> list[Pt]:
    return ring[:-1] if len(ring) > 3 and ring[0] == ring[-1] else ring


def _dock(raw: Mapping[str, Any]) -> Pt | None:
    for dock in raw.get("allchargingData") or []:
        point = dock.get("chargingPoint") if isinstance(dock, Mapping) else None
        if isinstance(point, Mapping):
            return (float(point["x"]), float(point["y"]))
    return None


def _inside(p: Pt, ring: Sequence[Pt]) -> bool:
    x, y = p
    inside = False
    for i in range(len(ring)):
        (x1, y1), (x2, y2) = ring[i], ring[i - 1]
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
            inside = not inside
    return inside


def _centroid(ring: Iterable[Pt]) -> Pt:
    pts = list(ring)
    return (sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts))


def _distance_to_ring(p: Pt, ring: Sequence[Pt]) -> float:
    return min(math.dist(p, q) for q in ring) if ring else math.inf


def window(days: int | None) -> float | None:
    return None if not days else time.time() - days * 86400
