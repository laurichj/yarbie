"""The map as a small SVG for dashboards: zones, pathways, dock, the robot, this run.

Drawn in the map's own frame (metres, x west, y north), so it needs no imagery and no
network. East is right and north is up.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any
from xml.sax.saxutils import escape

from .mapdoc import FAMILIES, POLYGON_FAMILIES, docks, local_points

STYLE = {
    "areas": ('fill="#c8e6c9" stroke="#43a047"', 0.25),
    "nogozones": ('fill="#ffcdd2" stroke="#e53935"', 0.2),
    "novisionzones": ('fill="#e1bee7" stroke="#8e24aa"', 0.2),
    "elec_fence": ('fill="none" stroke="#6d4c41"', 0.2),
    "pathways": ('fill="none" stroke="#ffa000"', 0.35),
    "sidewalks": ('fill="none" stroke="#757575" stroke-dasharray="0.6 0.4"', 0.3),
    "deadends": ('fill="none" stroke="#9e9e9e"', 0.2),
}
PAD_M = 2.0


def _xy(x: float, y: float) -> tuple[float, float]:
    return (-x, -y)  # east right, north up


def _path(points: Sequence[tuple[float, float]]) -> str:
    return " ".join(f"{px:.2f},{py:.2f}" for px, py in (_xy(x, y) for x, y in points))


def svg(
    raw: Mapping[str, Any],
    robot: tuple[float, float, float] | None = None,
    mowed: Iterable[Sequence[tuple[float, float]]] = (),
) -> bytes:
    shapes: list[str] = []
    xs: list[float] = []
    ys: list[float] = []
    for family in FAMILIES:
        style, width = STYLE.get(family, ('fill="none" stroke="#999"', 0.2))
        for record in raw.get(family) or []:
            if not isinstance(record, Mapping):
                continue
            pts = [(x, y) for x, y, _ in local_points(record)]
            if len(pts) < 2:
                continue
            for x, y in pts:
                px, py = _xy(x, y)
                xs.append(px)
                ys.append(py)
            tag = "polygon" if family in POLYGON_FAMILIES and len(pts) > 2 else "polyline"
            name = escape(str(record.get("name") or ""))
            shapes.append(
                f'<{tag} points="{_path(pts)}" {style} stroke-width="{width}" '
                f'stroke-linejoin="round"><title>{name}</title></{tag}>'
            )
    for line in mowed:
        if len(line) > 1:
            shapes.append(
                f'<polyline points="{_path(line)}" fill="none" stroke="#1b5e20" '
                'stroke-width="0.25" opacity="0.8"/>'
            )
    for dock in docks(raw):
        point = dock["chargingPoint"]
        px, py = _xy(float(point["x"]), float(point["y"]))
        xs.append(px)
        ys.append(py)
        shapes.append(f'<rect x="{px - 0.5:.2f}" y="{py - 0.5:.2f}" width="1" height="1" rx="0.2" fill="#455a64"/>')
    if robot is not None:
        px, py = _xy(robot[0], robot[1])
        shapes.append(f'<circle cx="{px:.2f}" cy="{py:.2f}" r="0.6" fill="#0288d1" stroke="#fff" stroke-width="0.2"/>')
    if not xs:
        xs, ys = [-10.0, 10.0], [-10.0, 10.0]
    x0, x1 = min(xs) - PAD_M, max(xs) + PAD_M
    y0, y1 = min(ys) - PAD_M, max(ys) + PAD_M
    body = "".join(shapes)
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{x0:.2f} {y0:.2f} {x1 - x0:.2f} {y1 - y0:.2f}" '
        f'width="800" height="{800 * (y1 - y0) / (x1 - x0):.0f}">'
        f'<rect x="{x0:.2f}" y="{y0:.2f}" width="{x1 - x0:.2f}" height="{y1 - y0:.2f}" fill="#f1f4ef"/>'
        f"{body}</svg>"
    ).encode()
