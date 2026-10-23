// The Area settings view: every work area as a row, its settings as columns, so a
// setting can be set on several areas at once and the areas kept in sync.
//
// Cells that differ from most areas in their column are tinted; edits stay pending
// (outlined) until reviewed and sent with a two-click confirm. The integration backs up
// each affected area's settings before sending, then reads them all again.

import { api, ApiError, type AreaChange, type AreaSettings, type Status } from "./api";
import { GROUPS, HIDDEN, PATTERNS, read, type Field, type Params } from "./areaSettings";

interface Column extends Omit<Field, "kind"> {
  kind: Field["kind"] | "pattern";
  group: string;
}

const PATTERN: Column = { path: "algorithm_type", label: "Mowing pattern", kind: "pattern", group: "Area" };
// Shown until the user picks their own: what is changed most often.
const DEFAULT_SHOWN = [
  "algorithm_type",
  "first_clean_params.blade_height",
  "first_clean_params.route_angle",
  "first_clean_params.plan_speed",
  "edge_circles",
  "edge_area_first",
  "mower_ngz_edge_sw",
  "avoidance_level",
];
const STORE_KEY = "yarbie.areaColumns";

const $ = <T extends HTMLElement = HTMLElement>(id: string) => document.getElementById(id) as T;
const esc = (s: unknown) =>
  String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]!);

// -- state (kept while the panel is open, so going back to the map loses nothing)

let rows: AreaSettings[] = [];
let columns: Column[] = [];
let shown = new Set<string>(loadShown());
const pending = new Map<number, Map<string, unknown>>(); // area id -> path -> new value
const selected = new Set<number>();
let status: Status | null = null;
let stage: "edit" | "review" = "edit";
let armed = false;
let sending = false;
let results: { area_id: number; name: string; ok: boolean; error: string | null }[] | null = null;
let sentSomething = false;
let onClose: (changed: boolean) => void = () => {};

function loadShown(): string[] {
  try {
    const saved = JSON.parse(localStorage.getItem(STORE_KEY) ?? "null");
    if (Array.isArray(saved) && saved.every((p) => typeof p === "string")) return saved;
  } catch {
    // storage unavailable
  }
  return DEFAULT_SHOWN;
}

function saveShown() {
  try {
    localStorage.setItem(STORE_KEY, JSON.stringify([...shown]));
  } catch {
    // storage unavailable; the choice just won't persist
  }
}

// -- values

function original(row: AreaSettings, path: string): unknown {
  if (path === PATTERN.path) return row.algorithm_type;
  return row.params ? read(row.params, path) : undefined;
}

function value(row: AreaSettings, path: string): unknown {
  const edit = pending.get(row.id);
  return edit?.has(path) ? edit.get(path) : original(row, path);
}

/** Floats come back from the robot as 0.30000001192092896: compare them as the table shows them. */
function norm(v: unknown): unknown {
  return typeof v === "number" ? Number(v.toFixed(4)) : v;
}
const same = (a: unknown, b: unknown) => norm(a) === norm(b);

function show(v: unknown, column: Column): string {
  if (v === undefined || v === null) return "–";
  if (column.kind === "pattern") return PATTERNS.find(([n]) => n === v)?.[1] ?? `Type ${v}`;
  if (column.kind === "bool") return v ? "on" : "off";
  return `${norm(v)}${column.unit ? ` ${column.unit}` : ""}`;
}

function setPending(row: AreaSettings, path: string, next: unknown) {
  const edit = pending.get(row.id) ?? new Map<string, unknown>();
  if (same(next, original(row, path))) edit.delete(path);
  else edit.set(path, next);
  if (edit.size) pending.set(row.id, edit);
  else pending.delete(row.id);
  armed = false;
}

/** The value most areas have in this column, and how many different values there are. */
function consensus(column: Column): { common: unknown; distinct: number } {
  const counts = new Map<unknown, number>();
  for (const row of rows) {
    const v = norm(value(row, column.path));
    if (v === undefined) continue;
    counts.set(v, (counts.get(v) ?? 0) + 1);
  }
  let common: unknown;
  let best = 0;
  for (const [v, n] of counts) {
    if (n > best) [common, best] = [v, n];
  }
  return { common, distinct: counts.size };
}

// -- columns: the named settings, then any other number or switch the robot sends

function buildColumns(editable: string[]) {
  const named: Column[] = [PATTERN, ...GROUPS.flatMap((g) => g.fields.map((f) => ({ ...f, group: g.title })))];
  const known = new Set(named.map((c) => c.path));
  const raw = new Map<string, Column>();
  for (const key of editable) {
    if (HIDDEN.has(key)) continue;
    for (const row of rows) {
      const v = row.params?.[key];
      const add = (path: string, x: unknown) => {
        if (known.has(path) || raw.has(path) || (typeof x !== "number" && typeof x !== "boolean")) return;
        raw.set(path, { path, label: path, kind: typeof x === "boolean" ? "bool" : "number", group: "Raw robot fields" });
      };
      if (v && typeof v === "object" && !Array.isArray(v)) {
        for (const [sub, x] of Object.entries(v as Params)) add(`${key}.${sub}`, x);
      } else add(key, v);
    }
  }
  // Only columns some area has a value for (a robot may not send every key).
  columns = [...named, ...[...raw.values()].sort((a, b) => a.path.localeCompare(b.path))].filter(
    (c) => c.kind === "pattern" || rows.some((r) => original(r, c.path) !== undefined),
  );
}

// -- loading and opening

export async function openAreaSettings(close: (changed: boolean) => void, focusArea?: number) {
  onClose = close;
  $("app").hidden = true;
  $("area-view").hidden = false;
  if (focusArea !== undefined) selected.add(focusArea);
  if (!rows.length) await load();
  else render();
}

function closeView() {
  $("area-view").hidden = true;
  $("app").hidden = false;
  onClose(sentSomething);
  sentSomething = false;
}

async function load() {
  const body = $("area-body");
  body.innerHTML = `<p class="hint">Reading every area's settings from the robot…</p>`;
  try {
    const [got, st] = await Promise.all([api.areaSettings(), api.status().catch(() => null)]);
    rows = got.areas;
    status = st;
    buildColumns(got.editable);
  } catch (err) {
    body.innerHTML = `<p class="hint error">Could not read the areas: ${esc(message(err))}</p>`;
    return;
  }
  // Edits for areas that are gone, or already equal to what the robot holds, are dropped.
  for (const id of [...pending.keys()]) {
    const row = rows.find((r) => r.id === id);
    if (!row) pending.delete(id);
    else for (const [path, v] of pending.get(id)!) setPending(row, path, v);
  }
  for (const id of [...selected]) if (!rows.some((r) => r.id === id)) selected.delete(id);
  render();
}

function refusal(): string | null {
  if (!status) return "No answer from yarbie.";
  if (!status.connected) return "Not connected to the robot.";
  if (!status.writable) return `Read-only: ${status.error ?? "writes are off"}.`;
  if (status.busy) return "The robot is running or paused in a plan. Stop or finish it before changing settings.";
  return null;
}

// -- drawing

function visible(): Column[] {
  return columns.filter((c) => shown.has(c.path));
}

function render() {
  const changes = [...pending.values()].reduce((n, m) => n + m.size, 0);
  const why = refusal();
  $("area-rails").textContent = why ?? "";
  $("area-rails").hidden = !why;
  const body = $("area-body");
  body.innerHTML = `
    <div class="area-toolbar">
      ${columnPicker()}
      <button id="area-differ" title="Show every column where the areas do not all agree">Show columns that differ</button>
      <button id="area-reload" title="Read every area's settings from the robot again${changes ? " (your pending edits are kept)" : ""}">Reload</button>
      <span class="hint">${selected.size} of ${rows.length} selected</span>
      <span class="legend"><i class="sw-differs"></i> differs from most areas <i class="sw-pending"></i> edited, not sent</span>
    </div>
    ${stage === "review" ? review() : ""}
    ${results ? resultList() : ""}
    <div class="area-scroll">${table()}</div>
    <div class="area-actions">
      <button id="area-review" class="primary" ${changes && stage === "edit" ? "" : "disabled"}>Review ${changes} change${changes === 1 ? "" : "s"}</button>
      <button id="area-discard" ${changes ? "" : "disabled"}>Discard all</button>
      <span class="hint">* meaning inferred from the field name. Hover a heading for the robot's field.</span>
    </div>`;
  wire();
}

function columnPicker(): string {
  const groups = [...new Set(columns.map((c) => c.group))];
  return `<details class="column-picker"><summary>Columns (${visible().length})</summary><div>
    ${groups
      .map(
        (g) => `<fieldset><legend>${esc(g)}</legend>${columns
          .filter((c) => c.group === g)
          .map(
            (c) => `<label class="check" title="robot field: ${esc(c.path)}"><input type="checkbox" data-col="${esc(c.path)}" ${
              shown.has(c.path) ? "checked" : ""
            }/> ${esc(c.label)}${c.inferred ? " *" : ""}</label>`,
          )
          .join("")}</fieldset>`,
      )
      .join("")}</div></details>`;
}

function heading(c: Column): string {
  const prefix = c.group === "Second pass" ? "2nd pass: " : "";
  const { distinct } = consensus(c);
  return `<th title="robot field: ${esc(c.path)}">${esc(prefix + c.label)}${c.inferred ? " *" : ""}${
    c.unit ? ` <span class="unit">${esc(c.unit)}</span>` : ""
  }${distinct > 1 ? `<div class="differs-count" data-count="${esc(c.path)}">${distinct} values</div>` : `<div class="differs-count" data-count="${esc(c.path)}"></div>`}</th>`;
}

function editor(c: Column, v: unknown, attrs: string): string {
  if (c.kind === "pattern") {
    const known = PATTERNS.some(([n]) => n === v);
    return `<select ${attrs}>${v === undefined ? `<option value="" selected>–</option>` : ""}${PATTERNS.map(
      ([n, label]) => `<option value="${n}" ${n === v ? "selected" : ""}>${label}</option>`,
    ).join("")}${!known && typeof v === "number" ? `<option value="${v}" selected>Type ${v}</option>` : ""}</select>`;
  }
  if (c.kind === "bool") return `<input type="checkbox" ${attrs} ${v ? "checked" : ""}/>`;
  const limits = [
    c.step !== undefined ? `step="${c.step}"` : 'step="any"',
    c.min !== undefined ? `min="${c.min}"` : "",
    c.max !== undefined ? `max="${c.max}"` : "",
  ].join(" ");
  return `<input type="number" ${limits} ${attrs} value="${typeof v === "number" ? norm(v) : ""}"/>`;
}

function table(): string {
  const cols = visible();
  const all = rows.length > 0 && rows.every((r) => selected.has(r.id));
  const setRow = cols
    .map((c, i) => {
      const v = c.kind === "bool" ? undefined : consensus(c).common;
      const input =
        c.kind === "bool"
          ? `<select data-bulk="${i}"><option value="1">on</option><option value="0">off</option></select>`
          : editor(c, v, `data-bulk="${i}"`);
      return `<th class="bulk">${input}<button data-set="${i}" ${selected.size ? "" : "disabled"} title="Set this on the selected areas">Set</button></th>`;
    })
    .join("");
  const body = rows
    .map((row) => {
      const cells = cols
        .map((c) => {
          if (c.kind !== "pattern" && !row.params) return `<td class="na" title="The robot did not send this area's settings">–</td>`;
          return `<td data-cell="${row.id}|${esc(c.path)}">${editor(c, value(row, c.path), `data-edit="${row.id}|${esc(c.path)}"`)}</td>`;
        })
        .join("");
      return `<tr class="${selected.has(row.id) ? "selected" : ""}">
        <td class="pick"><input type="checkbox" data-row="${row.id}" ${selected.has(row.id) ? "checked" : ""}/></td>
        <th class="area-name">${esc(row.name || `Area ${row.id}`)}</th>${cells}</tr>`;
    })
    .join("");
  return `<table class="area-table">
    <thead>
      <tr><th class="pick"><input type="checkbox" id="area-all" ${all ? "checked" : ""} title="Select all"/></th><th class="area-name">Area</th>${cols.map(heading).join("")}</tr>
      <tr class="bulk-row"><th></th><th class="area-name hint">Set on selected</th>${setRow}</tr>
    </thead>
    <tbody>${body || `<tr><td colspan="${cols.length + 2}" class="hint">The robot has no work areas.</td></tr>`}</tbody>
  </table>`;
}

/** Tint, outline and explain every cell from the state, without redrawing the inputs. */
function decorate() {
  for (const c of visible()) {
    const { common, distinct } = consensus(c);
    const count = document.querySelector<HTMLElement>(`[data-count="${CSS.escape(c.path)}"]`);
    if (count) count.textContent = distinct > 1 ? `${distinct} values` : "";
    for (const row of rows) {
      const td = document.querySelector<HTMLElement>(`[data-cell="${row.id}|${CSS.escape(c.path)}"]`);
      if (!td) continue;
      const v = value(row, c.path);
      const edited = pending.get(row.id)?.has(c.path) ?? false;
      td.classList.toggle("differs", distinct > 1 && !same(v, common));
      td.classList.toggle("pending", edited);
      td.title = [
        `robot field: ${c.path}`,
        edited ? `was ${show(original(row, c.path), c)}, will be ${show(v, c)}` : "",
        distinct > 1 && !same(v, common) ? `most areas: ${show(common, c)}` : "",
      ]
        .filter(Boolean)
        .join(" · ");
    }
  }
  const changes = [...pending.values()].reduce((n, m) => n + m.size, 0);
  const reviewButton = $<HTMLButtonElement>("area-review");
  if (reviewButton) {
    reviewButton.disabled = !changes || stage !== "edit";
    reviewButton.textContent = `Review ${changes} change${changes === 1 ? "" : "s"}`;
  }
  const discard = $<HTMLButtonElement>("area-discard");
  if (discard) discard.disabled = !changes;
}

function inputValue(c: Column, el: HTMLInputElement | HTMLSelectElement): unknown {
  if (c.kind === "bool") return el instanceof HTMLInputElement && el.type === "checkbox" ? el.checked : el.value === "1";
  if (el.value === "") return undefined;
  const n = Number(el.value);
  return Number.isNaN(n) ? undefined : n;
}

// -- review and send

function review(): string {
  const cols = new Map(columns.map((c) => [c.path, c]));
  const areas = rows.filter((r) => pending.has(r.id));
  const why = refusal();
  return `<div class="area-review">
    <h2>Review: ${areas.length} area${areas.length === 1 ? "" : "s"}</h2>
    <ul class="area-changes">${areas
      .map(
        (row) => `<li><button class="discard" data-drop="${row.id}" title="Discard this area's changes">✕</button>
          <b>${esc(row.name || `Area ${row.id}`)}</b><ul>${[...pending.get(row.id)!]
            .map(([path, v]) => {
              const c = cols.get(path) ?? { ...PATTERN, path, label: path, kind: "number" as const };
              return `<li>${esc(c.label)}: ${esc(show(original(row, path), c))} → <b>${esc(show(v, c))}</b></li>`;
            })
            .join("")}</ul></li>`,
      )
      .join("")}</ul>
    <div class="row">
      <button id="area-send" class="${armed ? "confirm" : "primary"}" ${why || sending ? "disabled" : ""}>${
        sending ? "Sending…" : armed ? `Confirm: send to ${areas.length} area${areas.length === 1 ? "" : "s"}` : "Send to robot"
      }</button>
      <button id="area-back-edit">Back to editing</button>
    </div>
    <p class="hint">${why ? esc(why) : "Each area's current settings are backed up first, then each area is sent its changes."}</p>
  </div>`;
}

function changesToSend(): AreaChange[] {
  return [...pending].map(([areaId, edits]) => {
    const change: AreaChange = { area_id: areaId };
    for (const [path, v] of edits) {
      if (path === PATTERN.path) {
        change.algorithm_type = v as number;
        continue;
      }
      const params = (change.params ??= {});
      const [key, sub] = path.split(".");
      if (sub) params[key] = { ...((params[key] as Params | undefined) ?? {}), [sub]: v };
      else params[key] = v;
    }
    return change;
  });
}

async function send() {
  if (!armed) {
    armed = true;
    render();
    return;
  }
  armed = false;
  sending = true;
  render();
  try {
    const result = await api.applyAreaSettings(changesToSend());
    rows = result.areas;
    results = result.results;
    sentSomething ||= result.results.some((r) => r.ok);
    // What was sent is now what the robot holds; failed areas keep their edits to retry.
    for (const r of result.results) if (r.ok) pending.delete(r.area_id);
    for (const id of [...pending.keys()]) {
      const row = rows.find((x) => x.id === id);
      if (row) for (const [path, v] of pending.get(id)!) setPending(row, path, v);
    }
    stage = "edit";
  } catch (err) {
    results = null;
    say(message(err), "error");
  }
  sending = false;
  status = await api.status().catch(() => status);
  render();
}

function resultList(): string {
  const failed = results!.filter((r) => !r.ok).length;
  return `<div class="area-results ${failed ? "error" : "ok"}">
    <b>${failed ? `${results!.length - failed} of ${results!.length} areas changed` : `All ${results!.length} areas changed`}</b>; settings read again from the robot.
    <ul>${results!.map((r) => `<li>${r.ok ? "✓" : "✗"} ${esc(r.name || `Area ${r.area_id}`)}${r.error ? `: ${esc(r.error)}` : ""}</li>`).join("")}</ul>
  </div>`;
}

// -- events

function wire() {
  const body = $("area-body");
  body.querySelectorAll<HTMLInputElement>("input[data-col]").forEach((box) =>
    box.addEventListener("change", () => {
      if (box.checked) shown.add(box.dataset.col!);
      else shown.delete(box.dataset.col!);
      saveShown();
      render();
      body.querySelector<HTMLDetailsElement>(".column-picker")!.open = true;
    }),
  );
  $("area-differ").addEventListener("click", () => {
    for (const c of columns) if (consensus(c).distinct > 1) shown.add(c.path);
    saveShown();
    render();
  });
  $("area-reload").addEventListener("click", () => void load());
  $("area-all")?.addEventListener("change", (e) => {
    if ((e.target as HTMLInputElement).checked) rows.forEach((r) => selected.add(r.id));
    else selected.clear();
    render();
  });
  body.querySelectorAll<HTMLInputElement>("input[data-row]").forEach((box) =>
    box.addEventListener("change", () => {
      const id = Number(box.dataset.row);
      if (box.checked) selected.add(id);
      else selected.delete(id);
      render();
    }),
  );
  const cols = visible();
  body.querySelectorAll<HTMLInputElement | HTMLSelectElement>("[data-edit]").forEach((el) =>
    el.addEventListener("change", () => {
      const [id, path] = el.dataset.edit!.split("|");
      const row = rows.find((r) => r.id === Number(id));
      const c = columns.find((x) => x.path === path);
      if (!row || !c) return;
      const v = inputValue(c, el);
      if (v === undefined) return;
      setPending(row, path, v);
      results = null;
      decorate();
    }),
  );
  body.querySelectorAll<HTMLButtonElement>("button[data-set]").forEach((b) =>
    b.addEventListener("click", () => {
      const c = cols[Number(b.dataset.set)];
      const input = body.querySelector<HTMLInputElement | HTMLSelectElement>(`[data-bulk="${b.dataset.set}"]`)!;
      const v = inputValue(c, input);
      if (v === undefined) return say(`Give a value for ${c.label} first.`, "error");
      let count = 0;
      for (const row of rows) {
        if (!selected.has(row.id) || (c.kind !== "pattern" && !row.params)) continue;
        setPending(row, c.path, v);
        count++;
      }
      results = null;
      render();
      say(`${c.label} set to ${show(v, c)} on ${count} area${count === 1 ? "" : "s"} (not sent yet).`);
    }),
  );
  $("area-review").addEventListener("click", () => {
    stage = "review";
    armed = false;
    render();
    document.querySelector(".area-review")?.scrollIntoView({ block: "nearest" });
  });
  $("area-discard").addEventListener("click", (e) => {
    const button = e.currentTarget as HTMLButtonElement;
    if (!button.classList.contains("confirm")) {
      button.classList.add("confirm");
      button.textContent = "Discard every edit?";
      return;
    }
    pending.clear();
    stage = "edit";
    render();
  });
  $("area-send")?.addEventListener("click", () => void send());
  $("area-back-edit")?.addEventListener("click", () => {
    stage = "edit";
    armed = false;
    render();
  });
  body.querySelectorAll<HTMLButtonElement>("button[data-drop]").forEach((b) =>
    b.addEventListener("click", () => {
      pending.delete(Number(b.dataset.drop));
      if (!pending.size) stage = "edit";
      armed = false;
      render();
    }),
  );
  decorate();
}

export function wireAreaView() {
  $("area-back").addEventListener("click", closeView);
}

// -- helpers

function message(err: unknown): string {
  if (err instanceof ApiError) return err.message;
  return err instanceof Error ? err.message : String(err);
}

function say(text: string, kind: "ok" | "error" | "" = "") {
  const footer = $("area-message");
  footer.textContent = text;
  footer.className = kind;
}
