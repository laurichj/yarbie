// Smoothing: fewer points along a driven outline, round curves as arcs. Areas only shrink
// and no-go zones only grow. The integration works the outlines out; this shows them in
// the user's units and turns them into edits to review like any other.

import type { Family, Smoothed, Smoothing } from "./api";

export const SMOOTHABLE: Family[] = ["areas", "nogozones"];
// Until the integration has answered (it sends its own default and limits): 6 in.
export const DEFAULT_SMOOTHING: Smoothing = { tolerance_m: 0.1524, unit: "in", unit_m: 0.0254, limits: [0.5, 35.4] };
const SQ_FT_PER_M2 = 10.7639;

/** This session's choices; the tolerance starts from the integration options. */
export const choices: { tolerance_m: number | null; arcs: boolean; keepShared: boolean } = {
  tolerance_m: null,
  arcs: true,
  keepShared: true,
};

export function tolerance(s: Smoothing): number {
  return choices.tolerance_m ?? s.tolerance_m;
}

export function length(m: number, s: Smoothing): string {
  return `${(m / s.unit_m).toFixed(1)} ${s.unit}`;
}

function area(m2: number, s: Smoothing): string {
  if (s.unit === "in") return `${Math.round(m2 * SQ_FT_PER_M2).toLocaleString()} ft²`;
  return `${m2 >= 10 ? Math.round(m2).toLocaleString() : m2.toFixed(1)} m²`;
}

/** One line on what smoothing would do to an outline. */
export function summary(r: Smoothed, family: Family, s: Smoothing): string {
  const { before, after, area_before, area_after, max_offset, arcs, circle, notes } = r.stats;
  if (after >= before && !arcs) return ["Already as simple as this tolerance allows.", ...notes].join(" ");
  const change = Math.abs(area_after - area_before);
  const parts = [
    `${before.toLocaleString()} → ${after.toLocaleString()} points`,
    `${area(change, s)} ${family === "areas" ? "smaller" : "larger"}`,
    `moves at most ${length(max_offset, s)}`,
  ];
  if (circle) parts.push("a circle");
  else if (arcs) parts.push(`${arcs} arc${arcs === 1 ? "" : "s"}`);
  return [parts.join(" · "), ...notes].join(" ");
}

/** Whether a result changes anything worth sending. */
export function changes(r: Smoothed): boolean {
  return r.stats.after !== r.stats.before || r.stats.arcs > 0;
}

/** The tolerance and switches, as form fields named ``<prefix>-…``. */
export function controls(prefix: string, s: Smoothing, areas: boolean): string {
  const step = s.unit === "in" ? 0.5 : 1;
  const [min, max] = s.limits;
  return `
    <label class="field" style="margin-top:8px">How far a line may move (${s.unit})
      <input type="number" id="${prefix}-tol" step="${step}" min="${min}" max="${max}"
        value="${(tolerance(s) / s.unit_m).toFixed(1)}"/></label>
    <label class="check"><input type="checkbox" id="${prefix}-arcs" ${choices.arcs ? "checked" : ""}/> Round curves</label>
    ${
      areas
        ? `<label class="check"><input type="checkbox" id="${prefix}-shared" ${choices.keepShared ? "checked" : ""}/> Keep edges shared with other areas</label>`
        : ""
    }`;
}

/** Read the fields back into ``choices``; false if the tolerance is not a number in range. */
export function readControls(prefix: string, s: Smoothing): boolean {
  const input = document.getElementById(`${prefix}-tol`) as HTMLInputElement;
  const value = Number(input.value);
  if (!(value >= Number(input.min) && value <= Number(input.max))) return false;
  choices.tolerance_m = value * s.unit_m;
  choices.arcs = (document.getElementById(`${prefix}-arcs`) as HTMLInputElement).checked;
  const shared = document.getElementById(`${prefix}-shared`) as HTMLInputElement | null;
  if (shared) choices.keepShared = shared.checked;
  return true;
}
