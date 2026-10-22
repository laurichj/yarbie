"""A made-up yard for the built-in simulator, shaped like a real ``get_map`` reply.

Points are metres from ``REFERENCE``, x pointing west and y north, as on the robot.
"""

from __future__ import annotations

import math
from typing import Any

REFERENCE = (37.42752, -122.16975)
SERIAL = "SIM-YARBIE-0001"

DOCK = {"phi": 0.0, "x": -0.054, "y": -0.207}


def _ref() -> dict[str, float]:
    return {"latitude": REFERENCE[0], "longitude": REFERENCE[1]}


def _ring(points: list[tuple[float, float]], *, close: bool) -> list[dict[str, float]]:
    out = []
    for i, (x, y) in enumerate(points):
        nx, ny = points[(i + 1) % len(points)]
        out.append({"x": x, "y": y, "phi": round(math.atan2(ny - y, nx - x), 3)})
    if close:
        out.append(dict(out[0]))
    return out


def _area(area_id: int, name: str, points: list[tuple[float, float]]) -> dict[str, Any]:
    return {
        "algorithm_type": 0,
        "id": area_id,
        "labels": [area_id],
        "leaf_piles": [],
        "mapping_module_type": 99,
        "mult_grid": 1,
        "name": name,
        "push_snow_dir": 9999.0,
        "range": _ring(points, close=False),
        "ref": _ref(),
        "slope_degrees": 9999.0,
        "snowPiles": [],
        "trimming_edges": [],
    }


def site_map() -> dict[str, Any]:
    dock = {
        "chargingPoint": dict(DOCK),
        "enable": True,
        "hasChargingStation": 1,
        "id": 1,
        "name": "",
        "startPoint": {"phi": 2.906, "x": -1.026, "y": 0.027},
        "straightPhi": 2.9055,
    }
    front = [(-4.0, 3.0), (-22.0, 3.5), (-23.0, 16.0), (-12.0, 18.5), (-3.5, 15.0)]
    back = [(6.0, -4.0), (6.5, -18.0), (-14.0, -19.0), (-15.0, -5.0)]
    return {
        "allchargingData": [dock],
        "chargingData": dock,
        "areas": [_area(1, "Front Lawn", front), _area(2, "Back Lawn", back)],
        "nogozones": [
            {
                "area_id": -1,
                "enable": True,
                "id": 20,
                "name": "Flower bed",
                "range": _ring(
                    [(-10.0, 8.0), (-13.0, 8.0), (-13.0, 11.0), (-10.0, 11.0)], close=True
                ),
                "ref": _ref(),
                "trimming_edges": [],
                "type": 0,
            }
        ],
        "novisionzones": [],
        "elec_fence": [],
        "pathways": [
            {
                "connectids": [],
                "end_id": 1,
                "id": 21,
                "leaf_piles": [],
                "name": "Dock to front",
                "range": _ring(
                    [(DOCK["x"], DOCK["y"]), (-1.5, 1.0), (-3.0, 2.5), (-6.0, 5.0)], close=False
                ),
                "ref": _ref(),
                "snowPiles": [],
                "start_id": 0,
                "trimming_edges": [],
            },
            {
                "connectids": [],
                "end_id": 2,
                "id": 22,
                "leaf_piles": [],
                "name": "Dock to back",
                "range": _ring([(DOCK["x"], DOCK["y"]), (0.5, -2.5), (0.0, -7.0)], close=False),
                "ref": _ref(),
                "snowPiles": [],
                "start_id": 0,
                "trimming_edges": [],
            },
        ],
        "sidewalks": [],
        "deadends": [],
        "mul_points": [],
    }


def plans() -> list[dict[str, Any]]:
    return [
        {"id": 1, "name": "Whole yard", "areaIds": [1, 2], "enable_self_order": True},
        {"id": 2, "name": "Front only", "areaIds": [1], "enable_self_order": True},
    ]


def snapshot() -> dict[str, Any]:
    """Just enough of ``get_device_msg`` for the session and the status bar."""
    return {
        "version": "3.14.11",
        "BatteryMSG": {"capacity": 87},
        "CombinedOdom": {"x": DOCK["x"], "y": DOCK["y"], "phi": 0.0},
        "StateMSG": {
            "working_state": 0,
            "on_going_planning": 0,
            "planning_paused": 0,
            "on_going_recharging": 0,
            "charging_status": 2,
            "error_code": 0,
        },
        "HeadMsg": {"head_type": 5},
        # An RTK fix, so a plan may start (the session refuses without one).
        "RTKMSG": {"status": "4", "sat_num": 28, "heading": 212.0},
    }


def route(area: list[tuple[float, float]], spacing: float = 0.6) -> tuple[list[tuple[float, float]], list[tuple[float, float]]]:
    """A plausible route for one area, for the simulator's previews: rows across it,
    boustrophedon, and one lap of its edge. The robot plans its own; this only has to
    look like one."""
    ys = [y for _, y in area]
    rows: list[tuple[float, float]] = []
    y = min(ys) + spacing / 2
    left_to_right = True
    while y < max(ys):
        crossings: list[float] = []
        for (x1, y1), (x2, y2) in zip(area, area[1:] + area[:1], strict=True):
            if (y1 <= y < y2) or (y2 <= y < y1):
                crossings.append(x1 + (y - y1) * (x2 - x1) / (y2 - y1))
        crossings.sort()
        if len(crossings) >= 2:
            a, b = crossings[0], crossings[-1]
            rows += [(a, y), (b, y)] if left_to_right else [(b, y), (a, y)]
            left_to_right = not left_to_right
        y += spacing
    edge = [*area, area[0]]
    return rows, edge
