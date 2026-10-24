"""Try smoothing on a saved map and report what it would do.

    .venv/bin/python scripts/smooth_report.py MAP.json [--tolerance-in 6] [--no-arcs] [--svg OUT.html]

MAP.json is an editor backup (/config/yarbie/backups/*.json) or a bare map. Nothing is sent
to the robot. With --svg, writes a page with each outline drawn before and after. Run it
with the dev venv's Python: the vendored yarbo-local needs pyyaml.
"""

from __future__ import annotations

import argparse
import html
import json
import math
from pathlib import Path
import sys
import time
import types

ROOT = Path(__file__).resolve().parents[1]
# Load the editor modules without Home Assistant: stub the package __init__s.
for name, path in (
    ("custom_components", ROOT / "custom_components"),
    ("custom_components.yarbie", ROOT / "custom_components/yarbie"),
):
    module = types.ModuleType(name)
    module.__path__ = [str(path)]
    sys.modules[name] = module

from custom_components.yarbie.editor import mapdoc, smooth  # noqa: E402

INCH = 0.0254


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("map", type=Path)
    parser.add_argument("--tolerance-in", type=float, default=6.0)
    parser.add_argument("--no-arcs", action="store_true")
    parser.add_argument("--smooth-shared", action="store_true", help="smooth edges areas share too")
    parser.add_argument("--svg", type=Path)
    parser.add_argument("--json", type=Path, help="write each outline before and after")
    args = parser.parse_args()
    data = json.loads(args.map.read_text())
    raw = data["map"] if isinstance(data.get("map"), dict) else data
    tolerance = args.tolerance_in * INCH

    keep = _keep_points(raw)
    outlines = {
        record.get("id"): [(x, y) for x, y, _ in mapdoc.local_points(record)]
        for record in raw.get("areas") or []
    }
    rows = []
    drawings = []
    for family in smooth.DIRECTION:
        for record in raw.get(family) or []:
            pts = [(x, y) for x, y, _ in mapdoc.local_points(record)]
            if mapdoc.is_closed_ring(mapdoc.local_points(record)):
                pts = pts[:-1]
            started = time.perf_counter()
            result = smooth.smooth_feature(
                family,
                pts,
                tolerance,
                arcs=not args.no_arcs,
                keep=keep if family == "areas" else (),
                neighbours=[]
                if family != "areas" or args.smooth_shared
                else [o for k, o in outlines.items() if k != record.get("id")],
            )
            ms = (time.perf_counter() - started) * 1000
            rows.append((family, record, result, ms))
            drawings.append((family, record, pts, result))

    print(f"tolerance {args.tolerance_in:g} in ({tolerance * 100:.1f} cm), arcs {'off' if args.no_arcs else 'on'}")
    print(f"{'family':10} {'id':>5} {'name':20} {'pts':>5} {'->':>5} {'cut':>5} {'area m2':>9} {'change':>8} "
          f"{'max off':>8} {'arcs':>4} {'pin':>4} {'ms':>6}  notes")
    totals: dict[str, list[int]] = {}
    for family, record, r, ms in rows:
        change = r.area_after - r.area_before
        t = totals.setdefault(family, [0, 0])
        t[0] += r.before
        t[1] += r.after
        print(
            f"{family:10} {record.get('id'):>5} {str(record.get('name'))[:20]:20} {r.before:5} {r.after:5} "
            f"{100 * (1 - r.after / r.before):4.0f}% {r.area_before:9.2f} {change:+8.2f} "
            f"{r.max_offset / INCH:6.2f}in {r.arcs if not r.circle else 'circ':>4} {r.pinned:4} {ms:6.0f}  "
            + " ".join(r.notes)
        )
    for family, (before, after) in totals.items():
        print(f"{family}: {before} -> {after} points ({100 * (1 - after / before):.0f}% fewer)")
    if args.json:
        args.json.write_text(json.dumps([
            {"family": f, "id": r.get("id"), "name": r.get("name"), "before": b, "after": res.points}
            for f, r, b, res in drawings
        ]))
    if args.svg:
        args.svg.write_text(_page(drawings, args.tolerance_in))
        print(f"wrote {args.svg}")


def _keep_points(raw: dict) -> list[tuple[float, float]]:
    """Where pathways meet areas, and the dock: shrinking an area must not leave them out."""
    out = []
    for record in raw.get("pathways") or []:
        pts = mapdoc.local_points(record)
        if pts:
            out += [pts[0][:2], pts[-1][:2]]
    dock = mapdoc.dock_point(raw)
    if dock:
        out.append(dock)
    return out


def _page(drawings: list, tolerance_in: float) -> str:
    cards = []
    for family, record, before, result in drawings:
        xs = [p[0] for p in before + result.points]
        ys = [p[1] for p in before + result.points]
        x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
        pad = max(x1 - x0, y1 - y0) * 0.05 + 0.2
        w, h = x1 - x0 + 2 * pad, y1 - y0 + 2 * pad
        stroke = max(w, h) / 400

        def path(points: list) -> str:
            return " ".join(f"{x - x0 + pad:.3f},{y1 + pad - y:.3f}" for x, y in points)

        dots = "".join(
            f'<circle cx="{x - x0 + pad:.3f}" cy="{y1 + pad - y:.3f}" r="{stroke * 1.6:.3f}" class="new"/>'
            for x, y in result.points
        )
        title = f"{html.escape(str(record.get('name')))} · {family} #{record.get('id')}"
        facts = (
            f"{result.before} → {result.after} points · area {result.area_before:.1f} → "
            f"{result.area_after:.1f} m² · max offset {result.max_offset / INCH:.1f} in"
            + (" · circle" if result.circle else f" · {result.arcs} arcs")
        )
        cards.append(
            f'<figure><svg viewBox="0 0 {w:.3f} {h:.3f}" style="--s:{stroke:.4f}">'
            f'<polygon points="{path(before)}" class="old"/><polygon points="{path(result.points)}" class="after"/>'
            f"{dots}</svg><figcaption><b>{title}</b><br>{facts}</figcaption></figure>"
        )
    return f"""<!doctype html><meta charset="utf-8"><title>Smoothing at {tolerance_in:g} in</title>
<style>body{{font:14px system-ui;margin:16px;background:#fff}}main{{display:grid;grid-template-columns:repeat(auto-fill,minmax(360px,1fr));gap:12px}}
figure{{margin:0;border:1px solid #ddd;padding:8px}}svg{{width:100%;height:340px}}
.old{{fill:none;stroke:#d33;stroke-width:calc(var(--s)*1.5)}}.after{{fill:rgba(40,110,255,.12);stroke:#26f;stroke-width:var(--s)}}
.new{{fill:#26f}}</style><h1>Smoothing at {tolerance_in:g} in</h1><p>Red: as driven. Blue: smoothed, with its points.</p>
<main>{"".join(cards)}</main>"""


if __name__ == "__main__":
    main()
