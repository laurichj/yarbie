from __future__ import annotations

from typing import Any

from custom_components.yarbie._vendor.yarbo_local.models import local_to_wgs84
from custom_components.yarbie.editor import demo, heatmap


def run(areas: list[int], *, segments: int = 1, laps: int = 2, start: int = 1_700_000_000) -> dict[str, Any]:
    finish = []
    for a in areas:
        finish += [{"id": a, "move_type": 0, "clean_times": 1}, {"id": a, "move_type": 1, "clean_times": 1}]
    return {
        "plan": {"areaIds": areas},
        "startTime": start,
        "startTimeV": [start + i for i in range(segments)],
        "finishIds": finish,
        "cleanState": 5,
        "mower_params": {"area_ids": areas, "perimeter_mowing_lapss": [laps] * len(areas)},
    }


def cell_at(result: dict[str, Any], x: float, y: float) -> float:
    lat, lon = local_to_wgs84(demo.REFERENCE, x, y)
    best = min(result["cells"], key=lambda c: (c[0] - lon) ** 2 + (c[1] - lat) ** 2)
    return best[2]


def test_pathways_to_the_mowed_area_are_counted_per_trip() -> None:
    history = [run([1]), run([1], segments=3)]  # 1 trip, then 3 trips
    result = heatmap.estimate(demo.site_map(), history, demo.REFERENCE)
    assert result["runs"] == 2 and result["trips"] == 4
    passes = {p["name"]: p["passes"] for p in result["pathways"]}
    assert passes == {"Dock to front": 8}  # out and back, four times
    assert [a["fills"] for a in result["areas"]] == [2.0]


def test_edges_wear_more_than_the_middle() -> None:
    result = heatmap.estimate(demo.site_map(), [run([2], laps=3)], demo.REFERENCE)
    middle = cell_at(result, -4.0, -11.0)
    edge = cell_at(result, 6.2, -11.0)
    assert middle == 1.0  # one fill pass
    assert edge > middle  # three laps plus row-end turns


def test_window_skips_old_runs() -> None:
    history = [run([1], start=1_000), run([2], start=2_000_000_000)]
    result = heatmap.estimate(demo.site_map(), history, demo.REFERENCE, since=1_500_000_000)
    assert result["runs"] == 1 and [a["name"] for a in result["areas"]] == ["Back Lawn"]


def test_unfinished_run_counts_the_part_mowed() -> None:
    partial = run([1])
    partial["finishIds"] = []
    partial["cleanState"] = 0
    partial["area_path"] = [{"x": 0, "y": 0}] * 100
    partial["area_path_index"] = 25
    result = heatmap.estimate(demo.site_map(), [partial], demo.REFERENCE)
    assert result["areas"] == [{"id": 1, "name": "Front Lawn", "fills": 0.2}]  # 0.25, rounded
