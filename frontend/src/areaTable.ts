// The Area settings table: every work area as a row, its settings as columns, so a
// setting can be set on several areas at once and the areas kept in sync. The dashboard's
// Area settings card (areaSettingsCard.ts) hosts it.
//
// Cells that differ from most areas in their column are tinted; edits stay pending
// (outlined) until reviewed and sent with a two-click confirm. The integration backs up
// each affected area's settings before sending, then reads them all again. Read-only for
// users who are not admins: the integration refuses them the write anyway.

import type { AreaChange, AreaSettings, AreaSettingsResult } from "./api";
import { GROUPS, HIDDEN, PATTERNS, read, type Field, type Params } from "./areaSettings";

interface Column extends Omit<Field, "kind"> {
  kind: Field["kind"] | "pattern";
  group: string;
}

/** What the table needs from the card around it. */
export interface TableHost {
  root: ShadowRoot; // the table's elements are found here
  call<T>(type: string, data?: Record<string, unknown>): Promise<T>; // yarbie/<type>
  readOnly(): boolean; // not an admin: show the settings, offer no edits
  refusal(): string | null; // why a send would be refused now (the write rails)
  refreshWrites(): Promise<void>;
  say(text: string, kind?: "ok" | "error" | ""): void;
  message(err: unknown): string;
  sent(): void; // something was changed on the robot
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

const esc = (s: unknown) =>
  String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]!);

function loadShown(): string[] {
  try {
    const saved = JSON.parse(localStorage.getItem(STORE_KEY) ?? "null");
    if (Array.isArray(saved) && saved.every((p) => typeof p === "string")) return saved;
  } catch {
    // storage unavailable
  }
  return DEFAULT_SHOWN;
}

function saveShown(shown: Set<string>) {
  try {
    localStorage.setItem(STORE_KEY, JSON.stringify([...shown]));
  } catch {
    // storage unavailable; the choice just won't persist
  }
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

function inputValue(c: Column, el: HTMLInputElement | HTMLSelectElement): unknown {
  if (c.kind === "bool") return el instanceof HTMLInputElement && el.type === "checkbox" ? el.checked : el.value === "1";
  if (el.value === "") return undefined;
  const n = Number(el.value);
  return Number.isNaN(n) ? undefined : n;
}

export class AreaTable {
  rows: AreaSettings[] = [];
  editable: string[] = []; // the settings keys the integration lets through
  #columns: Column[] = [];
  #shown = new Set<string>(loadShown());
  readonly pending = new Map<number, Map<string, unknown>>(); // area id -> path -> new value
  readonly selected = new Set<number>();
  #stage: "edit" | "review" = "edit";
  #armed = false;
  #sending = false;
  #results: AreaSettingsResult["results"] | null = null;
  #loadError: string | null = null;

  constructor(private readonly host: TableHost) {}

  #el<T extends HTMLElement = HTMLElement>(id: string): T | null {
    return this.host.root.getElementById(id) as T | null;
  }

  /** True while there is anything the user would lose to a reload. */
  busy(): boolean {
    return this.pending.size > 0 || this.#sending;
  }

  // -- values

  #original(row: AreaSettings, path: string): unknown {
    if (path === PATTERN.path) return row.algorithm_type;
    return row.params ? read(row.params, path) : undefined;
  }

  #value(row: AreaSettings, path: string): unknown {
    const edit = this.pending.get(row.id);
    return edit?.has(path) ? edit.get(path) : this.#original(row, path);
  }

  #setPending(row: AreaSettings, path: string, next: unknown) {
    const edit = this.pending.get(row.id) ?? new Map<string, unknown>();
    if (same(next, this.#original(row, path))) edit.delete(path);
    else edit.set(path, next);
    if (edit.size) this.pending.set(row.id, edit);
    else this.pending.delete(row.id);
    this.#armed = false;
  }

  /** The value most areas have in this column, and how many different values there are. */
  #consensus(column: Column): { common: unknown; distinct: number } {
    const counts = new Map<unknown, number>();
    for (const row of this.rows) {
      const v = norm(this.#value(row, column.path));
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

  #buildColumns(editable: string[]) {
    const named: Column[] = [PATTERN, ...GROUPS.flatMap((g) => g.fields.map((f) => ({ ...f, group: g.title })))];
    const known = new Set(named.map((c) => c.path));
    const raw = new Map<string, Column>();
    for (const key of editable) {
      if (HIDDEN.has(key)) continue;
      for (const row of this.rows) {
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
    this.#columns = [...named, ...[...raw.values()].sort((a, b) => a.path.localeCompare(b.path))].filter(
      (c) => c.kind === "pattern" || this.rows.some((r) => this.#original(r, c.path) !== undefined),
    );
  }

  // -- loading

  /** Read every area again: the settings may have changed since (the single-area form,
   * the app), and edits must be compared with what the robot holds now. Pending edits are
   * kept. */
  async load() {
    const body = this.#el("area-body");
    if (body) body.innerHTML = `<p class="hint">Reading every area's settings from the robot…</p>`;
    try {
      const got = await this.host.call<{ areas: AreaSettings[]; editable: string[] }>("area_settings/get");
      this.rows = got.areas;
      this.editable = got.editable;
      this.#buildColumns(got.editable);
      this.#loadError = null;
    } catch (err) {
      this.#loadError = `Could not read the areas: ${this.host.message(err)}`;
      this.render();
      return;
    }
    // Edits for areas that are gone, or already equal to what the robot holds, are dropped.
    for (const id of [...this.pending.keys()]) {
      const row = this.rows.find((r) => r.id === id);
      if (!row) this.pending.delete(id);
      else for (const [path, v] of this.pending.get(id)!) this.#setPending(row, path, v);
    }
    for (const id of [...this.selected]) if (!this.rows.some((r) => r.id === id)) this.selected.delete(id);
    this.render();
  }

  // -- drawing

  #visible(): Column[] {
    return this.#columns.filter((c) => this.#shown.has(c.path));
  }

  /** Draw into #area-body (the card puts it there). */
  render() {
    const body = this.#el("area-body");
    if (!body) return;
    if (this.#loadError) {
      body.innerHTML = `<p class="hint error">${esc(this.#loadError)}</p>`;
      return;
    }
    const changes = [...this.pending.values()].reduce((n, m) => n + m.size, 0);
    const edit = !this.host.readOnly();
    body.innerHTML = `
      <div class="area-toolbar">
        ${this.#columnPicker()}
        <button id="area-differ" title="Show every column where the areas do not all agree">Show columns that differ</button>
        <button id="area-reload" title="Read every area's settings from the robot again${changes ? " (your pending edits are kept)" : ""}">Reload</button>
        ${edit ? `<span class="hint">${this.selected.size} of ${this.rows.length} selected</span>` : ""}
        <span class="legend"><i class="sw-differs"></i> differs from most areas ${edit ? `<i class="sw-pending"></i> edited, not sent` : ""}</span>
      </div>
      ${this.#stage === "review" ? this.#review() : ""}
      ${this.#results ? this.#resultList() : ""}
      <div class="area-scroll">${this.#table()}</div>
      <div class="area-actions">
        ${
          edit
            ? `<button id="area-review" class="primary" ${changes && this.#stage === "edit" ? "" : "disabled"}>Review ${changes} change${changes === 1 ? "" : "s"}</button>
               <button id="area-discard" ${changes ? "" : "disabled"}>Discard all</button>`
            : ""
        }
        <span class="hint">* meaning inferred from the field name. Hover a heading for the robot's field.</span>
      </div>`;
    this.#wire();
  }

  #columnPicker(): string {
    const groups = [...new Set(this.#columns.map((c) => c.group))];
    return `<details class="column-picker"><summary>Columns (${this.#visible().length})</summary><div>
      ${groups
        .map(
          (g) => `<fieldset><legend>${esc(g)}</legend>${this.#columns
            .filter((c) => c.group === g)
            .map(
              (c) => `<label class="check" title="robot field: ${esc(c.path)}"><input type="checkbox" data-col="${esc(c.path)}" ${
                this.#shown.has(c.path) ? "checked" : ""
              }/> ${esc(c.label)}${c.inferred ? " *" : ""}</label>`,
            )
            .join("")}</fieldset>`,
        )
        .join("")}</div></details>`;
  }

  #heading(c: Column): string {
    const prefix = c.group === "Second pass" ? "2nd pass: " : "";
    const { distinct } = this.#consensus(c);
    return `<th title="robot field: ${esc(c.path)}">${esc(prefix + c.label)}${c.inferred ? " *" : ""}${
      c.unit ? ` <span class="unit">${esc(c.unit)}</span>` : ""
    }<div class="differs-count" data-count="${esc(c.path)}">${distinct > 1 ? `${distinct} values` : ""}</div></th>`;
  }

  #input(c: Column, v: unknown, attrs: string): string {
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

  #table(): string {
    const cols = this.#visible();
    const edit = !this.host.readOnly();
    const all = this.rows.length > 0 && this.rows.every((r) => this.selected.has(r.id));
    const setRow = cols
      .map((c, i) => {
        const v = c.kind === "bool" ? undefined : this.#consensus(c).common;
        const input =
          c.kind === "bool"
            ? `<select data-bulk="${i}"><option value="1">on</option><option value="0">off</option></select>`
            : this.#input(c, v, `data-bulk="${i}"`);
        return `<th class="bulk">${input}<button data-set="${i}" ${this.selected.size ? "" : "disabled"} title="Set this on the selected areas">Set</button></th>`;
      })
      .join("");
    const body = this.rows
      .map((row) => {
        const cells = cols
          .map((c) => {
            if (c.kind !== "pattern" && !row.params) return `<td class="na" title="The robot did not send this area's settings">–</td>`;
            const v = this.#value(row, c.path);
            return `<td data-cell="${row.id}|${esc(c.path)}">${
              edit ? this.#input(c, v, `data-edit="${row.id}|${esc(c.path)}"`) : esc(show(v, c))
            }</td>`;
          })
          .join("");
        return `<tr class="${this.selected.has(row.id) ? "selected" : ""}" data-area-row="${row.id}">
          ${edit ? `<td class="pick"><input type="checkbox" data-row="${row.id}" ${this.selected.has(row.id) ? "checked" : ""}/></td>` : ""}
          <th class="area-name">${esc(row.name || `Area ${row.id}`)}</th>${cells}</tr>`;
      })
      .join("");
    return `<table class="area-table">
      <thead>
        <tr>${edit ? `<th class="pick"><input type="checkbox" id="area-all" ${all ? "checked" : ""} title="Select all"/></th>` : ""}<th class="area-name">Area</th>${cols.map((c) => this.#heading(c)).join("")}</tr>
        ${edit ? `<tr class="bulk-row"><th></th><th class="area-name hint">Set on selected</th>${setRow}</tr>` : ""}
      </thead>
      <tbody>${body || `<tr><td colspan="${cols.length + 2}" class="hint">The robot has no work areas.</td></tr>`}</tbody>
    </table>`;
  }

  /** Tint, outline and explain every cell from the state, without redrawing the inputs. */
  #decorate() {
    const root = this.host.root;
    for (const c of this.#visible()) {
      const { common, distinct } = this.#consensus(c);
      const count = root.querySelector<HTMLElement>(`[data-count="${CSS.escape(c.path)}"]`);
      if (count) count.textContent = distinct > 1 ? `${distinct} values` : "";
      for (const row of this.rows) {
        const td = root.querySelector<HTMLElement>(`[data-cell="${row.id}|${CSS.escape(c.path)}"]`);
        if (!td) continue;
        const v = this.#value(row, c.path);
        const edited = this.pending.get(row.id)?.has(c.path) ?? false;
        td.classList.toggle("differs", distinct > 1 && !same(v, common));
        td.classList.toggle("pending", edited);
        td.title = [
          `robot field: ${c.path}`,
          edited ? `was ${show(this.#original(row, c.path), c)}, will be ${show(v, c)}` : "",
          distinct > 1 && !same(v, common) ? `most areas: ${show(common, c)}` : "",
        ]
          .filter(Boolean)
          .join(" · ");
      }
    }
    const changes = [...this.pending.values()].reduce((n, m) => n + m.size, 0);
    const reviewButton = this.#el<HTMLButtonElement>("area-review");
    if (reviewButton) {
      reviewButton.disabled = !changes || this.#stage !== "edit";
      reviewButton.textContent = `Review ${changes} change${changes === 1 ? "" : "s"}`;
    }
    const discard = this.#el<HTMLButtonElement>("area-discard");
    if (discard) discard.disabled = !changes;
  }

  // -- review and send

  #review(): string {
    const cols = new Map(this.#columns.map((c) => [c.path, c]));
    const areas = this.rows.filter((r) => this.pending.has(r.id));
    const why = this.host.refusal();
    return `<div class="area-review">
      <h3>Review: ${areas.length} area${areas.length === 1 ? "" : "s"}</h3>
      <ul class="area-changes">${areas
        .map(
          (row) => `<li><button class="discard" data-drop="${row.id}" title="Discard this area's changes">✕</button>
            <b>${esc(row.name || `Area ${row.id}`)}</b><ul>${[...this.pending.get(row.id)!]
              .map(([path, v]) => {
                const c = cols.get(path) ?? { ...PATTERN, path, label: path, kind: "number" as const };
                return `<li>${esc(c.label)}: ${esc(show(this.#original(row, path), c))} → <b>${esc(show(v, c))}</b></li>`;
              })
              .join("")}</ul></li>`,
        )
        .join("")}</ul>
      <div class="row">
        <button id="area-send" class="${this.#armed ? "confirm" : "primary"}" ${why || this.#sending ? "disabled" : ""}>${
          this.#sending ? "Sending…" : this.#armed ? `Confirm: send to ${areas.length} area${areas.length === 1 ? "" : "s"}` : "Send to robot"
        }</button>
        <button id="area-back-edit">Back to editing</button>
      </div>
      <p class="hint">${why ? esc(why) : "Each area's current settings are backed up first, then each area is sent its changes."}</p>
    </div>`;
  }

  #changesToSend(): AreaChange[] {
    return [...this.pending].map(([areaId, edits]) => {
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

  async #send() {
    if (!this.#armed) {
      this.#armed = true;
      this.render();
      return;
    }
    this.#armed = false;
    this.#sending = true;
    this.render();
    try {
      const result = await this.host.call<AreaSettingsResult>("area_settings/apply", { changes: this.#changesToSend() });
      this.#results = result.results;
      if (result.results.some((r) => r.ok)) this.host.sent();
      // What was sent is now what the robot holds; failed areas keep their edits to retry.
      for (const r of result.results) if (r.ok || r.skipped) this.pending.delete(r.area_id);
      this.#stage = "edit";
      if (result.areas) {
        const fresh = new Map(result.areas.map((a) => [a.id, a]));
        this.rows = this.rows.map((r) => fresh.get(r.id) ?? r);
        for (const id of [...this.pending.keys()]) {
          const row = this.rows.find((x) => x.id === id);
          if (row) for (const [path, v] of this.pending.get(id)!) this.#setPending(row, path, v);
        }
      } else {
        // Sent, but the read-back failed: read everything again (load keeps pending edits).
        this.host.say(`Sent, but reading the areas back failed (${result.read_error ?? "no answer"}). Reading them again…`, "error");
        this.#sending = false;
        await this.load();
        return;
      }
    } catch (err) {
      this.#results = null;
      this.host.say(this.host.message(err), "error");
    }
    this.#sending = false;
    await this.host.refreshWrites();
    this.render();
  }

  #resultList(): string {
    const results = this.#results!;
    const failed = results.filter((r) => r.error).length;
    const changed = results.filter((r) => r.ok).length;
    return `<div class="area-results ${failed ? "error" : "ok"}">
      <b>${changed} of ${results.length} area${results.length === 1 ? "" : "s"} changed</b>; settings read again from the robot.
      <ul>${results
        .map((r) => `<li>${r.ok ? "✓" : r.skipped ? "–" : "✗"} ${esc(r.name || `Area ${r.area_id}`)}${r.error ? `: ${esc(r.error)}` : r.skipped ? `: ${esc(r.skipped)}` : ""}</li>`)
        .join("")}</ul>
    </div>`;
  }

  // -- events

  #wire() {
    const root = this.host.root;
    const body = this.#el("area-body")!;
    const on = (id: string, type: string, fn: (e: Event) => void) => this.#el(id)?.addEventListener(type, fn);
    body.querySelectorAll<HTMLInputElement>("input[data-col]").forEach((box) =>
      box.addEventListener("change", () => {
        if (box.checked) this.#shown.add(box.dataset.col!);
        else this.#shown.delete(box.dataset.col!);
        saveShown(this.#shown);
        this.render();
        body.querySelector<HTMLDetailsElement>(".column-picker")!.open = true;
      }),
    );
    on("area-differ", "click", () => {
      for (const c of this.#columns) if (this.#consensus(c).distinct > 1) this.#shown.add(c.path);
      saveShown(this.#shown);
      this.render();
    });
    on("area-reload", "click", () => void this.load());
    this.#decorate();
    if (this.host.readOnly()) return;
    on("area-all", "change", (e) => {
      if ((e.target as HTMLInputElement).checked) this.rows.forEach((r) => this.selected.add(r.id));
      else this.selected.clear();
      this.render();
    });
    body.querySelectorAll<HTMLInputElement>("input[data-row]").forEach((box) =>
      box.addEventListener("change", () => {
        const id = Number(box.dataset.row);
        if (box.checked) this.selected.add(id);
        else this.selected.delete(id);
        this.render();
      }),
    );
    const cols = this.#visible();
    body.querySelectorAll<HTMLInputElement | HTMLSelectElement>("[data-edit]").forEach((el) =>
      el.addEventListener("change", () => {
        const [id, path] = el.dataset.edit!.split("|");
        const row = this.rows.find((r) => r.id === Number(id));
        const c = this.#columns.find((x) => x.path === path);
        if (!row || !c) return;
        const v = inputValue(c, el);
        if (v === undefined) return;
        this.#setPending(row, path, v);
        this.#results = null;
        this.#decorate();
      }),
    );
    body.querySelectorAll<HTMLButtonElement>("button[data-set]").forEach((b) =>
      b.addEventListener("click", () => {
        const c = cols[Number(b.dataset.set)];
        const input = body.querySelector<HTMLInputElement | HTMLSelectElement>(`[data-bulk="${b.dataset.set}"]`)!;
        const v = inputValue(c, input);
        if (v === undefined) return this.host.say(`Give a value for ${c.label} first.`, "error");
        let count = 0;
        for (const row of this.rows) {
          if (!this.selected.has(row.id) || (c.kind !== "pattern" && !row.params)) continue;
          this.#setPending(row, c.path, v);
          count++;
        }
        this.#results = null;
        this.render();
        this.host.say(`${c.label} set to ${show(v, c)} on ${count} area${count === 1 ? "" : "s"} (not sent yet).`);
      }),
    );
    on("area-review", "click", () => {
      this.#stage = "review";
      this.#armed = false;
      this.render();
      root.querySelector(".area-review")?.scrollIntoView({ block: "nearest" });
    });
    on("area-discard", "click", (e) => {
      const button = e.currentTarget as HTMLButtonElement;
      if (!button.classList.contains("confirm")) {
        button.classList.add("confirm");
        button.textContent = "Discard every edit?";
        return;
      }
      this.pending.clear();
      this.#stage = "edit";
      this.render();
    });
    on("area-send", "click", () => void this.#send());
    on("area-back-edit", "click", () => {
      this.#stage = "edit";
      this.#armed = false;
      this.render();
    });
    body.querySelectorAll<HTMLButtonElement>("button[data-drop]").forEach((b) =>
      b.addEventListener("click", () => {
        this.pending.delete(Number(b.dataset.drop));
        if (!this.pending.size) this.#stage = "edit";
        this.#armed = false;
        this.render();
      }),
    );
  }
}
