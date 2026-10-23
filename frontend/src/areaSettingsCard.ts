// custom:yarbie-area-settings-card: every work area's mowing settings, as the Area
// settings table (areaTable.ts) or one area's form. The Yarbo dashboard puts it alone in a
// full-width "Area settings" view; ?area=<id> in the address opens that area.
//
// Both send through yarbie/area_settings/apply, so every change is checked against the
// write rails and backed up first. Users who are not admins see the settings read-only.

import { AreaTable } from "./areaTable";
import { PATTERNS, settingsForm, trackChanges, type Params } from "./areaSettings";
import type { AreaChange, AreaSettingsResult } from "./api";
import { YarbieCard, defineCard, errorText, esc, queryParam } from "./cardBase";

class YarbieAreaSettingsCard extends YarbieCard {
  #table = new AreaTable({
    root: this.shadowRoot!,
    call: (type, data) => this.ws(type, data),
    readOnly: () => !this.isAdmin,
    refusal: () => this.refusal(),
    refreshWrites: () => this.refreshWrites(),
    say: (text, kind) => this.say(text, kind),
    message: errorText,
    sent: () => {},
  });
  #mode: "table" | "one" = "table";
  #area: number | null = null; // the area the single-area form shows
  #changes: Params = {}; // what the form changed, not yet sent
  #pattern: number | null = null;
  #sending = false;
  #loaded = false;
  #linked: string | null = null; // the ?area= last acted on

  static getStubConfig() {
    return { type: "custom:yarbie-area-settings-card" };
  }

  connectedCallback() {
    window.addEventListener("location-changed", this.#follow);
    this.#follow();
  }

  disconnectedCallback() {
    window.removeEventListener("location-changed", this.#follow);
  }

  /** ?area=<id>: show that area's form (and tick it in the table). */
  #follow = () => {
    const area = queryParam("area");
    if (area === this.#linked) return;
    this.#linked = area;
    if (area === null || !Number.isFinite(Number(area))) return;
    this.#pick(Number(area));
    this.#mode = "one";
    this.#table.selected.add(Number(area));
    if (this.#loaded) this.render();
  };

  protected editing() {
    return this.#sending || this.#table.busy() || Object.keys(this.#changes).length > 0 || this.#pattern !== null;
  }

  protected blocked(control: HTMLElement) {
    if (this.#sending) return true;
    if (control.id === "params-save") return !Object.keys(this.#changes).length && this.#pattern === null;
    return false;
  }

  protected refusal() {
    return this.isAdmin ? super.refusal() : null; // others cannot write at all; the card says so
  }

  protected async load() {
    // The table reads every area; the form shows one of those rows.
    await Promise.all([this.#table.load(), this.refreshWrites()]);
    this.#loaded = true;
    if (this.#area === null || !this.#table.rows.some((r) => r.id === this.#area)) this.#area = this.#table.rows[0]?.id ?? null;
    this.render();
  }

  #pick(area: number) {
    this.#area = area;
    this.#changes = {};
    this.#pattern = null;
  }

  protected render() {
    const rows = this.#table.rows;
    const body = `<style>${TABLE_STYLE}</style>
      <div class="tabs">
        <button data-mode="table" class="${this.#mode === "table" ? "active" : ""}">All areas</button>
        <button data-mode="one" class="${this.#mode === "one" ? "active" : ""}">One area</button>
        ${
          this.#mode === "one" && rows.length
            ? `<select id="one-area">${rows
                .map((r) => `<option value="${r.id}" ${r.id === this.#area ? "selected" : ""}>${esc(r.name || `Area ${r.id}`)}</option>`)
                .join("")}</select>`
            : ""
        }
      </div>
      ${
        this.isAdmin
          ? ""
          : `<p class="hint">Only administrators can change mowing settings. This is what the robot holds.</p>`
      }
      ${
        this.#mode === "table"
          ? `<div id="area-body"></div>`
          : `<div id="one-body">${this.#oneArea()}</div>`
      }`;
    this.shadowRoot!.innerHTML = this.frame("Area settings", body);
    if (this.#mode === "table") this.#table.render();
    this.#wire();
    this.updateRails();
  }

  /** One area's pattern and settings, the form the Yard editor used to have. */
  #oneArea(): string {
    const row = this.#table.rows.find((r) => r.id === this.#area);
    if (!row) return `<p class="hint">${this.#loaded ? "The robot has no work areas." : "Reading the areas…"}</p>`;
    if (!row.params) return `<p class="hint">The robot did not send this area's settings.</p>`;
    const readOnly = !this.isAdmin;
    const pattern = this.#pattern ?? row.algorithm_type;
    const known = PATTERNS.some(([v]) => v === pattern);
    const others = this.#table.rows.filter((r) => r.id !== row.id).map((r) => ({ id: r.id, name: r.name || `Area ${r.id}` }));
    return `<fieldset><legend>Area</legend>
        <label class="field">Mowing pattern<select id="one-pattern" ${readOnly ? "disabled" : ""}>${PATTERNS.map(
          ([v, label]) => `<option value="${v}" ${v === pattern ? "selected" : ""}>${label}</option>`,
        ).join("")}${!known && pattern != null ? `<option value="${pattern}" selected>Type ${pattern}</option>` : ""}</select></label>
      </fieldset>
      ${settingsForm(row.params, this.#table.editable, others, readOnly)}`;
  }

  #wire() {
    const root = this.shadowRoot!;
    root.querySelectorAll<HTMLButtonElement>("button[data-mode]").forEach((b) =>
      b.addEventListener("click", () => {
        this.#mode = b.dataset.mode as "table" | "one";
        this.render();
      }),
    );
    root.querySelector<HTMLSelectElement>("#one-area")?.addEventListener("change", (e) => {
      this.#pick(Number((e.target as HTMLSelectElement).value));
      this.say("");
      this.render();
    });
    const box = root.querySelector<HTMLElement>("#one-body");
    if (!box || !this.isAdmin) return;
    const update = () => this.updateRails();
    root.querySelector<HTMLSelectElement>("#one-pattern")?.addEventListener("change", (e) => {
      const v = Number((e.target as HTMLSelectElement).value);
      const row = this.#table.rows.find((r) => r.id === this.#area);
      this.#pattern = row && v === row.algorithm_type ? null : v;
      update();
    });
    trackChanges(box, (changes) => {
      this.#changes = changes;
      update();
    });
    root.querySelector("#params-save")?.addEventListener("click", () => void this.#save());
  }

  async #save() {
    const area = this.#area;
    if (area === null) return;
    const also = [...this.shadowRoot!.querySelectorAll<HTMLInputElement>("input[data-also]:checked")].map((i) => Number(i.dataset.also));
    const change = (id: number): AreaChange => ({
      area_id: id,
      ...(this.#pattern !== null ? { algorithm_type: this.#pattern } : {}),
      ...(Object.keys(this.#changes).length ? { params: this.#changes } : {}),
    });
    this.#sending = true;
    this.updateRails();
    try {
      const result = await this.ws<AreaSettingsResult>("area_settings/apply", { changes: [area, ...also].map(change) });
      const failed = result.results.filter((r) => r.error);
      const changed = result.results.filter((r) => r.ok).length;
      this.message = failed.length
        ? { text: `Changed ${changed} of ${result.results.length}. ${failed.map((r) => `${r.name}: ${r.error}`).join("; ")}`, kind: "error" }
        : { text: `Saved to ${changed === 1 ? "this area" : `${changed} areas`}. A backup was kept.`, kind: "ok" };
      this.#changes = {};
      this.#pattern = null;
    } catch (err) {
      this.message = { text: errorText(err), kind: "error" };
    }
    this.#sending = false;
    await this.load();
  }
}

// The table's look, in Home Assistant's theme colours.
const TABLE_STYLE = `
  .tabs { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; margin: 0 0 12px; }
  .tabs button.active { background: var(--primary-color); color: var(--text-primary-color, #fff); border-color: transparent; }
  .tabs select { margin-left: 8px; }
  #area-body { display: flex; flex-direction: column; gap: 10px; }
  #one-body { max-width: 640px; }
  #one-body label.field { display: block; }
  #one-body label.field input, #one-body label.field select { display: block; width: 100%; margin-top: 4px; box-sizing: border-box; }
  .params-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 0 12px; }
  .unit { color: var(--secondary-text-color); font-size: 0.85em; }
  details { margin: 8px 0; }
  summary { cursor: pointer; color: var(--secondary-text-color); }
  .error { color: var(--error-color); }
  .area-toolbar, .area-actions { display: flex; flex-wrap: wrap; align-items: center; gap: 10px; }
  .area-toolbar .hint, .area-actions .hint { margin: 0; }
  .column-picker { position: relative; }
  .column-picker summary { padding: 4px 12px; border: 1px solid var(--divider-color); border-radius: 16px; list-style: none; color: var(--primary-color); }
  .column-picker > div {
    position: absolute; z-index: 5; top: 110%; left: 0; width: min(760px, 85vw); max-height: 60vh; overflow: auto;
    display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 8px; padding: 10px;
    background: var(--card-background-color, #fff); border: 1px solid var(--divider-color); border-radius: 8px;
    box-shadow: 0 8px 24px rgba(0,0,0,0.3); }
  .legend { display: inline-flex; align-items: center; gap: 6px; color: var(--secondary-text-color); font-size: 0.9em; }
  .legend i { display: inline-block; width: 14px; height: 14px; border-radius: 3px; margin-left: 8px; }
  .sw-differs { background: rgba(var(--rgb-warning-color, 255, 166, 0), 0.25); border: 1px solid var(--warning-color, #ffa600); }
  .sw-pending { border: 2px solid var(--primary-color); }
  .area-scroll { overflow: auto; max-height: 70vh; border: 1px solid var(--divider-color); border-radius: 6px; }
  .area-table { border-collapse: separate; border-spacing: 0; min-width: 100%; }
  .area-table th, .area-table td { padding: 4px 6px; border-bottom: 1px solid var(--divider-color);
    border-right: 1px solid var(--divider-color); text-align: left; white-space: nowrap; }
  .area-table thead th { position: sticky; top: 0; background: var(--card-background-color, #fff); z-index: 2;
    vertical-align: bottom; font-weight: 600; }
  .area-table thead tr.bulk-row th { top: 46px; font-weight: normal; background: var(--secondary-background-color); }
  .area-table .area-name { position: sticky; left: 0; background: var(--card-background-color, #fff); z-index: 1; min-width: 140px; }
  .area-table thead .area-name { z-index: 3; }
  .area-table .pick { width: 28px; text-align: center; }
  .area-table tr.selected > * { background: rgba(var(--rgb-primary-color, 3, 169, 244), 0.12); }
  .area-table input[type="number"], .area-table select { width: 92px; padding: 3px 6px; }
  .area-table th.bulk button { margin-left: 4px; padding: 2px 8px; }
  .area-table td.differs { background: rgba(var(--rgb-warning-color, 255, 166, 0), 0.2); }
  .area-table td.pending { outline: 2px solid var(--primary-color); outline-offset: -3px; }
  .area-table td.na { color: var(--secondary-text-color); }
  .differs-count { font-size: 0.8em; font-weight: normal; color: var(--warning-color, #b58100); min-height: 1em; }
  .area-review, .area-results { border: 1px solid var(--divider-color); border-radius: 6px; padding: 10px 12px; max-height: 40vh; overflow: auto; }
  .area-review h3 { margin: 0 0 8px; font-size: 1em; }
  .area-changes { list-style: none; margin: 0 0 8px; padding: 0; }
  .area-changes > li { padding: 6px 0; border-bottom: 1px solid var(--divider-color); }
  .area-changes > li .discard { float: right; padding: 0 8px; }
  .area-changes ul { margin: 2px 0 0 18px; padding: 0; }
  .area-results.ok { border-color: var(--success-color, #43a047); }
  .area-results.error { border-color: var(--error-color); }
  .area-results ul { margin: 6px 0 0; padding-left: 18px; }
`;

defineCard(
  "yarbie-area-settings-card",
  YarbieAreaSettingsCard,
  "Yarbo area settings",
  "Every work area's mowing settings side by side, to set a value on several at once; or one area's form.",
);
