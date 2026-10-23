from __future__ import annotations

import copy
import math

from custom_components.yarbie.editor import demo, diff, mapdoc


def _features() -> dict:
    return mapdoc.to_feature_collection(demo.site_map(), demo.REFERENCE)


def _feature(fc: dict, key: str) -> dict:
    return next(f for f in fc["features"] if f["id"] == key)


def test_round_trip_is_sub_centimetre() -> None:
    raw = demo.site_map()
    fc = _features()
    for record in raw["areas"] + raw["nogozones"] + raw["pathways"]:
        family = "areas" if record in raw["areas"] else (
            "nogozones" if record in raw["nogozones"] else "pathways"
        )
        feature = _feature(fc, mapdoc.feature_key(family, record["id"]))
        back = mapdoc.geometry_to_local(feature["geometry"], demo.REFERENCE)
        before = mapdoc.local_points(record)
        if mapdoc.is_closed_ring(before):
            before = before[:-1]
        assert len(back) == len(before)
        for (x, y), (bx, by, _) in zip(back, before, strict=True):
            assert math.dist((x, y), (bx, by)) < 0.01


def test_x_points_west() -> None:
    fc = mapdoc.to_feature_collection(
        {"nogozones": [{"id": 1, "range": [{"x": 10, "y": 0}, {"x": 10, "y": 1}, {"x": 11, "y": 1}],
                        "ref": {"latitude": 40.0, "longitude": -105.0}}]},
        None,
    )
    lon = fc["features"][0]["geometry"]["coordinates"][0][0][0]
    assert lon < -105.0  # positive x is west of the reference


def test_unchanged_map_has_no_ops() -> None:
    assert diff.compute(demo.site_map(), demo.REFERENCE, _features()) == []


def test_new_nogozone_is_closed_and_supported() -> None:
    fc = _features()
    lon, lat = demo.REFERENCE[1], demo.REFERENCE[0]
    d = 0.00003
    fc["features"].append({
        "type": "Feature",
        "id": "new:1",
        "geometry": {"type": "Polygon", "coordinates": [[
            [lon, lat], [lon + d, lat], [lon + d, lat + d], [lon, lat + d], [lon, lat]
        ]]},
        "properties": {"family": "nogozones", "name": "Pond"},
    })
    [op] = diff.compute(demo.site_map(), demo.REFERENCE, fc)
    assert (op.kind, op.family, op.supported, op.name) == ("create", "nogozones", True, "Pond")
    assert op.points is not None and op.points[0][:2] == op.points[-1][:2]
    assert len(op.points) == 5


def test_area_delete_and_create_are_not_supported() -> None:
    fc = _features()
    fc["features"] = [f for f in fc["features"] if f["id"] != "areas:2"]
    [op] = diff.compute(demo.site_map(), demo.REFERENCE, fc)
    assert (op.kind, op.family, op.supported) == ("delete", "areas", False)
    assert op.reason


def test_rename_area_and_change_pattern() -> None:
    fc = _features()
    props = _feature(fc, "areas:1")["properties"]
    props["name"] = "Front"
    props["algorithm_type"] = 1
    [op] = diff.compute(demo.site_map(), demo.REFERENCE, fc)
    assert op.kind == "update" and op.supported
    assert op.changes == ["name", "pattern"]
    assert op.points is None  # the outline is left alone


def test_moving_vertex_keeps_other_headings() -> None:
    raw = demo.site_map()
    fc = _features()
    ring = _feature(fc, "areas:1")["geometry"]["coordinates"][0]
    ring[2] = [ring[2][0] + 0.00002, ring[2][1]]
    [op] = diff.compute(raw, demo.REFERENCE, fc)
    assert op.changes == ["shape"]
    assert op.warnings  # area reshapes are flagged
    before = mapdoc.local_points(raw["areas"][0])
    assert op.points is not None
    assert op.points[0][2] == before[0][2]
    assert len(op.points) == len(before)  # areas stay open rings


def test_pathway_losing_dock_link_warns() -> None:
    raw = copy.deepcopy(demo.site_map())
    fc = _features()
    for key in ("pathways:21", "pathways:22"):
        line = _feature(fc, key)["geometry"]["coordinates"]
        line[0] = [line[0][0] + 0.00002, line[0][1]]
    ops = diff.compute(raw, demo.REFERENCE, fc)
    assert len(ops) == 2
    for op in ops:
        assert any("WP005" in w.text for w in op.warnings)
        assert any("no pathway starts at the dock" in w.text for w in op.warnings)


def test_self_intersection_warns() -> None:
    fc = _features()
    ring = _feature(fc, "nogozones:20")["geometry"]["coordinates"][0]
    ring[1], ring[2] = ring[2], ring[1]
    [op] = diff.compute(demo.site_map(), demo.REFERENCE, fc)
    [warning] = [w for w in op.warnings if "cross itself" in w.text]
    assert len(warning.points) == 1  # where the two swapped edges meet
    assert op.public()["warnings"][0]["points"][0][0] < 0  # sent as [lon, lat]


def test_existing_crossing_is_not_blamed_on_an_edit() -> None:
    raw = copy.deepcopy(demo.site_map())
    # The robot already holds a bow-tie no-go zone; renaming it adds no crossing.
    rng = raw["nogozones"][0]["range"]
    rng[1], rng[2] = rng[2], rng[1]
    rng[-1] = dict(rng[0])
    fc = mapdoc.to_feature_collection(raw, demo.REFERENCE)
    _feature(fc, "nogozones:20")["properties"]["name"] = "Renamed"
    [op] = diff.compute(raw, demo.REFERENCE, fc)
    assert op.changes == ["name"] and op.warnings == []


def test_crossings_finds_each_crossing() -> None:
    bow_tie = [(0.0, 0.0), (2.0, 2.0), (2.0, 0.0), (0.0, 2.0)]
    assert diff.crossings(bow_tie) == [(1.0, 1.0)]
    assert diff.crossings([(0.0, 0.0), (2.0, 0.0), (2.0, 2.0), (0.0, 2.0)]) == []


def test_only_the_dock_in_use_counts() -> None:
    """Seen on 3.14.20 after the dock moved: the old one stays in allchargingData with
    enable false, listed first."""
    raw = copy.deepcopy(demo.site_map())
    old = {**raw["allchargingData"][0], "id": 1, "enable": False, "chargingPoint": {"phi": 0.0, "x": 1.714, "y": 0.729}}
    new = {**raw["allchargingData"][0], "id": 2, "enable": True, "chargingPoint": {"phi": 0.0, "x": 15.088, "y": 5.045}}
    raw["allchargingData"] = [old, new]
    raw["chargingData"] = new
    assert mapdoc.dock_point(raw) == (15.088, 5.045)
    docks = [f for f in mapdoc.to_feature_collection(raw, demo.REFERENCE)["features"] if f["properties"]["family"] == "dock"]
    assert [d["properties"]["id"] for d in docks] == [2]
    # A map with only chargingData still has its dock.
    del raw["allchargingData"]
    assert mapdoc.dock_point(raw) == (15.088, 5.045)
