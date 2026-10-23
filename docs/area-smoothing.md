# Smoothing area and no-go outlines

Status: the algorithm is built and tested (`custom_components/yarbie/editor/smooth.py`,
`tests/ha/test_smooth.py`). It has been tried on the real map with
`scripts/smooth_report.py`. The panel wiring (phase 2 below) is not built yet.

## Problem

Areas are mapped by driving the robot round them. What comes back has a point every
30–40 cm, wobbles a few centimetres either side of the real edge, and has kinks where
the robot paused or backed up. A 440 m boundary ("Shop") is 1,228 points. That makes
outlines slow to edit and noisy to look at. It may also make the robot's own planning
slower: a big map has already timed out route preview.

We want to drop points that don't change the line by more than a set offset (default
6 in), and draw round curves as even arcs. The line may only move one way:

| Family | Moves | Why |
|---|---|---|
| `areas` | inward only (area shrinks) | the robot never mows beyond what was driven |
| `nogozones` | outward only (zone grows) | the robot never mows closer to what was avoided |
| `novisionzones`, `elec_fence` | not smoothed | not editable here yet; if they become editable, no-vision would grow like a no-go zone |
| `pathways`, `sidewalks` | not smoothed | they are lines with dock-link rules (see `diff.DOCK_LINK_M`); a plain two-sided pass could come later |

## What the real map looks like

The robot's map as read on 2026-10-22: 9 areas, 80 no-go zones, 11 pathways.

- **Areas** have 20–1,228 points, with a median spacing of 0.32–0.54 m. Some are wound
  clockwise and some anticlockwise, so "inside" is worked out per outline from the sign
  of its area.
- **Four areas already cross themselves**: Driveway South, Shop, North Garden Fence and
  Tree net area, at the places where the robot reversed (turns sharper than 150°).
  `diff._check` already lets existing crossings through and only warns about new ones.
- **No-go zones** are simple rings of 7–29 points, 0.3–1.0 m apart. They are mostly trees
  and were already drawn coarsely, so there is little to remove at 6 in.
- **Areas share edges**: Backyard and Tree net area for about 30 m, Driveway South and Shop
  for about 23 m, and the garden areas with each other for 2–9 m each. Shrinking both
  sides of a shared edge would leave an unmown strip up to twice the tolerance wide.
- **Pathway ends** are 0.45–1.6 m inside their areas, so a 6 in shrink can't push one
  outside its area. They are pinned anyway, as a guard.

## Method

All of this runs in the robot's local metres. The outline is converted with each zone's
own `ref`, as `diff.py` does, so untouched points come back exactly.

1. **Clean.** Drop consecutive points under 1 cm apart and the repeated closing point.
2. **Pin what must not move.** Points stay put if they are within:
   - 0.5 m of a self-crossing or a reversal;
   - 1 m of a pathway end or the dock;
   - 0.3 m of another area's outline.

   Pinned points split the ring into stretches, and each stretch is smoothed on its own.
3. **Whole circle.** If nothing is pinned and the whole outline is within ⅔ of the
   tolerance of a least-squares circle, replace it with a regular polygon. This is the
   tree case, and it does automatically what the panel's "Make it a circle" does by hand.
4. **Arcs.** Along each stretch, grow runs greedily while they still fit a circle. A run
   counts as an arc if:
   - it has at least 5 points and sweeps at least 30° without doubling back;
   - every point is within ⅔ of the tolerance of the circle (RMS within ¼);
   - its radius is 0.2–50 m;
   - it bulges more than the tolerance, so a single straight edge wouldn't do.

   An arc is drawn at the innermost or outermost radius of its points, whichever keeps
   it on the allowed side. On that side the corners either sit on the circle
   (inscribed) or the edges just touch it (circumscribed), so the arc never crosses back
   over. Corners are spaced so each edge strays at most ⅓ of the tolerance from the
   circle, and never more than 45° apart.

   | | bulges out (convex) | curves in (concave) |
   |---|---|---|
   | area (shrink) | min radius, corners on circle | max radius, edges touch circle |
   | no-go (grow) | max radius, edges touch circle | min radius, corners on circle |

5. **Straight runs: one-sided Douglas–Peucker.** This is standard DP with one extra rule.
   An edge only replaces the points between its ends if all of them:
   - are on the allowed side of the edge (outside it for areas, inside it for no-go
     zones);
   - are within the tolerance of it;
   - leave the edge crossing no other part of the outline.

   When a point is on the wrong side, the run is split there first. So on a wobbly
   straight edge the kept points are the innermost wobbles (areas) or the outermost
   (no-go zones), and every kept point is one the robot actually drove.
6. **Check every piece against the original.** A piece is kept only if:
   - it crosses no edge of the original outline;
   - its midpoints and new corners are on the allowed side (inside test);
   - both ways round, no point is more than the tolerance from the other line (sampled
     every tolerance-length along the new line).

   If an arc fails, its run falls back to straight edges; if those fail, it keeps the
   original points.
7. **Check the whole outline.** If the assembled outline crosses itself anywhere the
   original didn't, the pieces at that crossing fall back one step (arc → straight →
   original), and the check repeats.

Why one-sided DP and not offsetting the outline first: offsetting a self-crossing
1,200-point ring needs a geometry library (shapely/GEOS). The integration's only
requirement today is `aiomqtt`, and HA OS installs on musl and arm. One-sided DP only
needs side tests and segment crossings, the same as `diff.crossings`, and it only ever
keeps driven points.

## Results on the real map

`python scripts/smooth_report.py <backup.json>`, with arcs on and shared edges pinned.
Each result was checked separately with shapely:
- every smoothed area lies inside the original, and every original no-go zone inside
  its smoothed one, to within 0.004 m² (the 2 mm allowed for rounding);
- the measured Hausdorff distance is never more than the tolerance.

| Tolerance | Area points | No-go points | Largest move | Area lost (largest) |
|---|---|---|---|---|
| 3 in | 3,334 → 1,877 (−44%) | −2% | 3.0 in | — |
| **6 in** | **3,334 → 1,572 (−53%)** | **−4%** | **5.9 in** | **Shop −10.1 m² of 2,917 (0.3%)** |
| 12 in | 3,334 → 1,265 (−62%) | −9% | 11.9 in | — |
| 6 in, arcs off | 3,334 → 1,497 (−55%) | −5% | 5.9 in | — |

At 6 in, per area: Driveway South 764 → 284, Shop 1,228 → 588, Tree net area
424 → 197, East Garden Fence 245 → 115, South Garden Fence 126 → 63. The three largest
areas take 0.2–0.5 s each in pure Python.

No-go zones barely change at 6 in because they were drawn coarsely to begin with. A few
round ones become arcs, and some of those gain points (8 → 10), which is the cost of
drawing them round. Arcs cost areas about 5% more points overall than straight edges
alone, in exchange for even curves around beds and trees.

Known rough edge: where an arc meets a straight piece, two or three points can end up a
few centimetres apart. A last pass that tries removing each join point, checked the same
way, would tidy this up (phase 3).

## Units and settings

- The tolerance is stored in metres and shown in the user's HA units. When
  `hass.config.units` is US customary (in the panel, `hass.config.unit_system.length`
  is `"mi"`), it is shown in **inches** with a default of 6. In metric it is shown in
  **centimetres** with a default of 15. HA has no small-length unit, so the larger
  length unit only decides which of the two to use.
- The integration options flow gets "Smoothing tolerance", stored in metres as
  `smoothing_tolerance_m` (default `smooth.DEFAULT_TOLERANCE_M` = 0.1524).
- The panel can override it for one smoothing run (a number box next to the button). The
  override isn't saved.
- Two switches, both on by default: **Round curves** (arcs), and **Keep edges shared with
  other areas**.

## How it fits the editor

Smoothing proposes geometry. It never writes to the robot by itself. The result becomes a
pending edit like any other, so it goes through `yarbie/preview` → Changes →
"Confirm: send N changes" → `yarbie/apply`, with the existing safety rails: revision
check, backup before every apply, busy check, and warnings.

**Websocket:** `yarbie/smooth`
`{features: [GeoJSON feature…], tolerance_m, arcs, keep_shared}` → for each feature:
`{id, geometry, stats: Smoothed.public()}`.

- It takes the features as they are in the editor, so it smooths unsent edits too.
  It converts each one with its zone's `ref`, like `diff._update`.
- Keep points (pathway ends and the dock) come from the current map. Neighbours are the
  other areas' outlines, as edited.
- The work runs in `asyncio.to_thread`, which keeps the event loop free on big maps.
- It needs an admin, like every editor command, but it doesn't need writes to be
  enabled: it is read-only.

**Panel:**
- **Selected tab:** a "Smooth outline" fieldset for areas and no-go zones. It shows the
  tolerance in the user's units and a one-line summary, e.g. "1,228 → 588 points, 10 m²
  smaller, at most 5.8 in", with Apply/Discard. While it's open, the proposed outline is
  drawn over the current one.
- **Tools:** "Smooth all…" smooths every area and no-go zone. It shows a list of what
  each would change and applies the ones that are ticked. Each one goes into the
  editor's undo history as a separate edit.
- The diff's "Area reshapes … rarely verified" warning still shows for every smoothed
  area. That's correct: see the risks below.

## Risks and open questions

1. **Area reshapes on the robot are lightly verified.** `diff._check` already warns
   about this. Before the "Smooth all" button, smooth **one** small area (North House
   Yard or South Garden Fence), apply it, then check that:
   - the app shows the new outline;
   - `area` (m²) is recomputed;
   - a route preview and a short mow behave.

   The backup from that apply is the way back.
2. **Edge trimming follows the outline.** With `trimming_edges` on, the edge pass will
   follow the smoothed line, up to 6 in inside the driven one. That's what we want for
   noise, but along a fence it means trimming up to 6 in further from the fence. Use 3 in
   for areas that are trimmed against hard edges.
3. **Does the robot need dense points?** The app's own drawn no-go zones are sparse
   (0.4–1 m), and pathways are 2–4 points, so sparse outlines are accepted for those. For
   areas this is unverified until step 1.
4. **Existing self-crossings** are left alone, not fixed. Repairing them (for example
   removing the little loops where the robot reversed) is a separate, riskier change:
   it can grow an area.
5. **No-go zones overlapping areas:** growing a no-go zone can only take mowing away,
   never add it, so there is no interaction to check.

## Phases

1. *(done)* `editor/smooth.py`, its tests, and `scripts/smooth_report.py` to try it on a
   backup.
2. `yarbie/smooth` websocket command, the option in the options flow, the Selected-tab
   action, and the translations. Then do the one-area trial on the robot (risk 1).
3. "Smooth all…" and the cleanup pass for arc joins. Optionally a two-sided pass for
   pathways and sidewalks that keeps the dock-linked first point.
