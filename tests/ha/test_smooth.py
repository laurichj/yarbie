from __future__ import annotations

import math
import random

import pytest

from custom_components.yarbie.editor import smooth
from custom_components.yarbie.editor.diff import crossings

TOL = smooth.DEFAULT_TOLERANCE_M


def _inside(p: smooth.Pt, ring: list[smooth.Pt]) -> bool:
    x, y = p
    inside = False
    for (x1, y1), (x2, y2) in zip(ring, ring[1:] + ring[:1], strict=True):
        if (y1 > y) != (y2 > y) and x < x1 + (y - y1) * (x2 - x1) / (y2 - y1):
            inside = not inside
    return inside


def _on_or_inside(p: smooth.Pt, ring: list[smooth.Pt]) -> bool:
    edges = smooth._Grid(ring, closed=True)
    return edges.distance(p, smooth.SLACK_M) <= smooth.SLACK_M or _inside(p, ring)


def _on_or_outside(p: smooth.Pt, ring: list[smooth.Pt]) -> bool:
    edges = smooth._Grid(ring, closed=True)
    return edges.distance(p, smooth.SLACK_M) <= smooth.SLACK_M or not _inside(p, ring)


def _driven_rectangle(seed: int = 1) -> list[smooth.Pt]:
    """A 20 x 10 m rectangle driven anticlockwise, a point every 35 cm, 3 cm of wobble."""
    rng = random.Random(seed)
    corners = [(0.0, 0.0), (20.0, 0.0), (20.0, 10.0), (0.0, 10.0)]
    out: list[smooth.Pt] = []
    for (x1, y1), (x2, y2) in zip(corners, corners[1:] + corners[:1], strict=True):
        steps = round(math.dist((x1, y1), (x2, y2)) / 0.35)
        for k in range(steps):
            t = k / steps
            out.append(
                (round(x1 + t * (x2 - x1) + rng.uniform(-0.03, 0.03), 3),
                 round(y1 + t * (y2 - y1) + rng.uniform(-0.03, 0.03), 3))
            )
    return out


def _driven_circle(radius: float, points: int, wobble: float, seed: int = 2) -> list[smooth.Pt]:
    rng = random.Random(seed)
    return [
        (round((radius + rng.uniform(-wobble, wobble)) * math.cos(2 * math.pi * k / points), 3),
         round((radius + rng.uniform(-wobble, wobble)) * math.sin(2 * math.pi * k / points), 3))
        for k in range(points)
    ]


@pytest.mark.parametrize("clockwise", [False, True])
def test_area_only_shrinks(clockwise: bool) -> None:
    ring = _driven_rectangle()
    if clockwise:
        ring.reverse()
    result = smooth.smooth_feature("areas", ring, TOL)
    assert result.after < result.before / 4
    assert result.area_after <= result.area_before
    assert result.max_offset <= TOL + smooth.SLACK_M
    # Every new corner is on or inside the driven outline, and every driven point on or
    # outside the new one.
    assert all(_on_or_inside(p, ring) for p in result.points)
    assert all(_on_or_outside(p, result.points) for p in ring)


def test_no_go_zone_only_grows() -> None:
    ring = _driven_rectangle()
    result = smooth.smooth_feature("nogozones", ring, TOL)
    assert result.after < result.before / 4
    assert result.area_after >= result.area_before
    assert all(_on_or_inside(p, result.points) for p in ring)


def test_tree_becomes_a_circle_round_every_driven_point() -> None:
    ring = _driven_circle(1.5, 30, 0.03)
    result = smooth.smooth_feature("nogozones", ring, TOL)
    assert result.circle
    assert result.after < result.before
    assert all(_on_or_inside(p, result.points) for p in ring)
    assert result.max_offset <= TOL + smooth.SLACK_M
    # An even polygon: every corner the same distance from the centre.
    radii = [math.hypot(x, y) for x, y in result.points]
    assert max(radii) - min(radii) < 0.05


def test_round_area_becomes_a_circle_inside_every_driven_point() -> None:
    ring = _driven_circle(6.0, 110, 0.03)
    result = smooth.smooth_feature("areas", ring, TOL)
    assert result.circle
    assert all(_on_or_inside(p, ring) for p in result.points)


def test_round_bed_cut_into_an_area_is_an_arc() -> None:
    """A lawn that wraps round a circular bed: the curve bends away from the lawn."""
    ring = _driven_rectangle()
    # Replace part of the top edge with a half circle dipping into the lawn.
    top = [p for p in ring if p[1] > 9.5 and 7.0 < p[0] < 13.0]
    first = ring.index(top[0])
    bed = [(round(10 + 3 * math.cos(t), 3), round(10 - 3 * math.sin(t), 3))
           for t in (math.pi * k / 40 for k in range(1, 40))]
    ring = ring[:first] + bed + ring[first + len(top):]
    result = smooth.smooth_feature("areas", ring, TOL)
    assert result.arcs >= 1
    assert result.area_after <= result.area_before
    assert all(_on_or_inside(p, ring) for p in result.points)
    assert result.max_offset <= TOL + smooth.SLACK_M


@pytest.mark.parametrize("tolerance", [0.05, 0.1524, 0.3])
def test_never_moves_more_than_the_tolerance(tolerance: float) -> None:
    for seed in range(5):
        ring = _driven_rectangle(seed)
        for family in ("areas", "nogozones"):
            result = smooth.smooth_feature(family, ring, tolerance)
            assert result.max_offset <= tolerance + smooth.SLACK_M


def test_points_near_a_crossing_are_left_alone() -> None:
    ring = _driven_rectangle()
    # Make the outline cross itself near the bottom-left corner.
    ring[3], ring[-3] = ring[-3], ring[3]
    before = crossings(ring)
    assert before
    result = smooth.smooth_feature("areas", ring, TOL)
    assert result.pinned > 0
    kept = [p for p in ring if any(math.dist(p, c) <= smooth.PIN_M for c in before)]
    assert all(p in result.points for p in kept)
    assert len(crossings(result.points)) <= len(before)


def test_points_near_a_kept_point_stay() -> None:
    ring = _driven_rectangle()
    pathway_end = (10.0, 0.2)
    result = smooth.smooth_feature("areas", ring, TOL, keep=[pathway_end])
    near = [p for p in ring if math.dist(p, pathway_end) <= smooth.KEEP_NEAR_M]
    assert near and all(p in result.points for p in near)


def test_already_sparse_outlines_are_left_as_they_are() -> None:
    square = [(0.0, 0.0), (5.0, 0.0), (5.0, 5.0), (0.0, 5.0)]
    for family in ("areas", "nogozones"):
        result = smooth.smooth_feature(family, square, TOL)
        assert result.points == square
        assert not result.changed


def test_lines_are_not_smoothed() -> None:
    with pytest.raises(ValueError):
        smooth.smooth_feature("pathways", [(0.0, 0.0), (1.0, 0.0)], TOL)


def test_an_edge_shared_with_another_area_stays() -> None:
    ring = _driven_rectangle()
    next_door = [(0.0, -10.0), (20.0, -10.0), (20.0, 0.0), (0.0, 0.0)]
    result = smooth.smooth_feature("areas", ring, TOL, neighbours=[next_door])
    shared = [p for p in ring if abs(p[1]) <= smooth.SHARED_M]
    assert shared and all(p in result.points for p in shared)
    assert result.after < result.before / 2
