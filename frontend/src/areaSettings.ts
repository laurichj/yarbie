// The mowing settings of an area, with readable names. The robot documents none of these;
// names the robot itself uses in its run history (perimeter_mowing_laps, mowing_heights,
// route_angles, ...) are taken as confirmed, the rest are inferred and marked with *.

export type Kind = "number" | "bool";

export interface Field {
  path: string; // key, or "group.key" for first/second pass settings
  label: string;
  kind: Kind;
  unit?: string;
  step?: number;
  min?: number;
  max?: number;
  inferred?: boolean;
}

export const GROUPS: { title: string; fields: Field[]; when?: (p: Params) => boolean }[] = [
  {
    title: "Mowing",
    fields: [
      { path: "first_clean_params.blade_height", label: "Cutting height", kind: "number", unit: "mm", step: 5, min: 0 },
      { path: "first_clean_params.route_angle", label: "Row direction", kind: "number", unit: "°", step: 5, min: -90, max: 90 },
      { path: "first_clean_params.plan_speed", label: "Speed", kind: "number", unit: "m/s", step: 0.05, min: 0 },
      { path: "first_clean_params.blade_speed", label: "Blade speed", kind: "number", step: 5, min: 0, inferred: true },
      { path: "first_clean_params.turn_type", label: "Turn type", kind: "number", step: 1, min: 0 },
      { path: "clean_times", label: "Passes", kind: "number", step: 1, min: 1, max: 2, inferred: true },
    ],
  },
  {
    title: "Second pass",
    when: (p) => Number(p.clean_times) >= 2,
    fields: [
      { path: "double_clean_params.blade_height", label: "Cutting height", kind: "number", unit: "mm", step: 5, min: 0 },
      { path: "double_clean_params.route_angle", label: "Row direction", kind: "number", unit: "°", step: 5, min: -90, max: 90 },
      { path: "double_clean_params.plan_speed", label: "Speed", kind: "number", unit: "m/s", step: 0.05, min: 0 },
    ],
  },
  {
    title: "Edges",
    fields: [
      { path: "edge_circles", label: "Perimeter laps", kind: "number", step: 1, min: 0 },
      { path: "edge_area_first", label: "Mow the edge first", kind: "bool" },
      { path: "mower_ngz_edge_sw", label: "Mow around no-go zones", kind: "bool" },
      { path: "edge_bypass_sw", label: "Go around obstacles on the edge", kind: "bool" },
      { path: "edge_direction_mode", label: "Edge direction", kind: "number", step: 1, min: 0, inferred: true },
    ],
  },
  {
    title: "Obstacles",
    fields: [
      { path: "avoidance_level", label: "Avoidance level", kind: "number", step: 1, min: 0 },
      { path: "ngz_expand_dis", label: "Keep further from no-go zones", kind: "number", unit: "m", step: 0.05, min: 0, inferred: true },
      { path: "en_trimmer", label: "Trimmer", kind: "bool", inferred: true },
    ],
  },
];

// The area's mowing pattern (algorithm_type, verified on the robot: 2, 3 and 5 plan what
// 0 does). An area record field, sent with save_clean_area, not a mowing setting.
export const PATTERNS: [number, string][] = [
  [0, "Rows"],
  [4, "Rows, fewer turns"],
  [1, "Spiral (contour)"],
];

// Values the robot keeps for itself or that only make sense to its firmware.
export const HIDDEN = new Set(["id", "mapping_module_type", "current_index", "mower_target_height_count"]);

export type Params = Record<string, unknown>;

const esc = (s: string) =>
  s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]!);

export function read(params: Params, path: string): unknown {
  const [key, sub] = path.split(".");
  const value = params[key];
  return sub ? (value as Params | undefined)?.[sub] : value;
}

function input(field: Field, value: unknown, readOnly = false): string {
  const title = `robot field: ${field.path}`;
  const off = readOnly ? "disabled" : "";
  const label = `${esc(field.label)}${field.inferred ? " *" : ""}${field.unit ? ` <span class="unit">${field.unit}</span>` : ""}`;
  if (field.kind === "bool") {
    return `<label class="check" title="${title}"><input type="checkbox" data-path="${field.path}" ${value ? "checked" : ""} ${off}/> ${label}</label>`;
  }
  const attrs = [
    field.step !== undefined ? `step="${field.step}"` : 'step="any"',
    field.min !== undefined ? `min="${field.min}"` : "",
    field.max !== undefined ? `max="${field.max}"` : "",
  ].join(" ");
  const shown = typeof value === "number" ? Number(value.toFixed(4)) : "";
  return `<label class="field" title="${title}">${label}<input type="number" ${attrs} data-path="${field.path}" value="${shown}" ${off}/></label>`;
}

/** The form: named settings by group, then everything else editable under Advanced.
 * Read-only (no save, nothing to tick) for users who may not change settings. */
export function settingsForm(
  params: Params,
  editable: string[],
  otherAreas: { id: number; name: string }[],
  readOnly = false,
): string {
  const named = new Set(GROUPS.flatMap((g) => g.fields.map((f) => f.path)));
  const groups = GROUPS.filter((g) => !g.when || g.when(params))
    .map((g) => {
      const fields = g.fields.filter((f) => read(params, f.path) !== undefined);
      if (!fields.length) return "";
      return `<fieldset><legend>${g.title}</legend><div class="params-grid">${fields
        .map((f) => input(f, read(params, f.path), readOnly))
        .join("")}</div></fieldset>`;
    })
    .join("");
  const advanced: string[] = [];
  for (const key of editable) {
    if (HIDDEN.has(key)) continue;
    const value = params[key];
    if (value && typeof value === "object" && !Array.isArray(value)) {
      for (const [sub, v] of Object.entries(value as Params)) {
        const path = `${key}.${sub}`;
        if (!named.has(path) && (typeof v === "number" || typeof v === "boolean")) {
          advanced.push(input({ path, label: path, kind: typeof v === "boolean" ? "bool" : "number" }, v, readOnly));
        }
      }
    } else if (!named.has(key) && (typeof value === "number" || typeof value === "boolean")) {
      advanced.push(input({ path: key, label: key, kind: typeof value === "boolean" ? "bool" : "number" }, value, readOnly));
    }
  }
  return `${groups}
    <details><summary>Advanced (raw robot fields)</summary><div class="params-grid">${advanced.join("")}</div></details>
    ${
      readOnly
        ? `<p class="hint">* = meaning inferred from the field name.</p>`
        : `${
            otherAreas.length
              ? `<details><summary>Also apply changes to…</summary>${otherAreas
                  .map((a) => `<label class="check"><input type="checkbox" data-also="${a.id}"/> ${esc(a.name)}</label>`)
                  .join("")}</details>`
              : ""
          }
    <div class="row"><button id="params-save" class="primary" disabled data-write>Save settings to robot</button></div>
    <p class="hint">Saved straight to the robot. * = meaning inferred from the field name.</p>`
    }`;
}

/** Collect what was changed in the form, as the nested record the robot takes. */
export function trackChanges(box: HTMLElement, onChange: (changes: Params) => void): void {
  const changes: Params = {};
  box.querySelectorAll<HTMLInputElement>("input[data-path]").forEach((el) =>
    el.addEventListener("change", () => {
      const [key, sub] = el.dataset.path!.split(".");
      const value = el.type === "checkbox" ? el.checked : Number(el.value);
      if (el.type !== "checkbox" && (el.value === "" || Number.isNaN(value))) return;
      if (sub) changes[key] = { ...((changes[key] as Params | undefined) ?? {}), [sub]: value };
      else changes[key] = value;
      onChange(changes);
    }),
  );
}
