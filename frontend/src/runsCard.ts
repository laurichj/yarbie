// custom:yarbie-runs-card: the run history (the robot's records joined with what yarbie
// saw), filtered by plan, and a run's timeline. Showing a run also shades its areas on
// the yard card, which listens for RUN_EVENT; ?run=<key> (or ?run=last) and ?plan=<id>
// in the address select them, so entities and notifications can link here.

import type { Run } from "./api";
import {
  RUN_EVENT,
  YarbieCard,
  defineCard,
  errorText,
  esc,
  loadRun,
  queryParam,
  setQueryParam,
  type RunShown,
} from "./cardBase";

const OUTCOMES: Record<string, string> = {
  completed: "Completed",
  partial: "Ended early",
  returned_to_dock: "Sent home",
  stopped: "Stopped",
  superseded: "Replaced",
  running: "Running",
  unknown: "Unknown end",
};
const PAGE = 10;

function when(t: number | null | undefined): string {
  if (!t) return "";
  return new Date(t * 1000).toLocaleString(undefined, { weekday: "short", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

function minutes(s: number | null | undefined): string {
  if (s == null) return "";
  const m = Math.round(s / 60);
  return m >= 60 ? `${Math.floor(m / 60)} h ${m % 60} min` : `${m} min`;
}

interface Listed {
  runs: Run[];
  plans: { id: number; name: string }[];
  count: number;
}

class YarbieRunsCard extends YarbieCard {
  #list: Listed | null = null;
  #loadError: string | null = null;
  #plan: number | null = null;
  #shown: Run | null = null;
  #limit = PAGE;
  #linked: string | null = null; // the ?run= and ?plan= last acted on

  static getStubConfig() {
    return { type: "custom:yarbie-runs-card" };
  }

  connectedCallback() {
    window.addEventListener("location-changed", this.#follow);
    window.addEventListener(RUN_EVENT, this.#onRun);
    if (this.hassObj) this.#follow();
  }

  disconnectedCallback() {
    window.removeEventListener("location-changed", this.#follow);
    window.removeEventListener(RUN_EVENT, this.#onRun);
  }

  /** The ?run= and ?plan= in the address now. */
  #address(): string {
    return `${queryParam("run")}|${queryParam("plan")}`;
  }

  /** ?plan= filters the list; ?run= opens that run (``last``: the newest that ended).
   * Followed when they differ from what the card last showed, so following the same
   * link again after hiding the run opens it again. */
  #follow = () => {
    const linked = this.#address();
    if (linked === this.#linked) return;
    this.#linked = linked;
    const run = queryParam("run");
    const plan = queryParam("plan") ?? "";
    this.#plan = /^\d+$/.test(plan) ? Number(plan) : null;
    void this.load().then(() => {
      if (run && run !== this.#shown?.key) void this.#show(run);
      else if (!run && this.#shown) void this.#show(null); // the address no longer names it
    });
  };

  /** The yard card stopped showing the run: close it here too. */
  #onRun = (e: Event) => {
    const { entry_id, run } = (e as CustomEvent<RunShown>).detail;
    if (run !== null || (entry_id ?? null) !== (this.config.entry_id ?? null) || !this.#shown) return;
    this.#shown = null;
    this.#linked = this.#address();
    this.render();
  };

  protected editing() {
    return false;
  }

  protected refusal() {
    return null; // it only reads: no write rails to show
  }

  protected async load(refresh = false) {
    if (this.#linked === null) {
      this.#follow(); // the first load: the address may name a run or plan
      return;
    }
    try {
      this.#list = await this.ws<Listed>("runs/list", {
        refresh,
        limit: 200,
        ...(this.#plan === null ? {} : { plan_id: this.#plan }),
      });
      this.#loadError = null;
    } catch (err) {
      this.#loadError = errorText(err);
    }
    this.render();
  }

  protected render() {
    let body: string;
    if (this.#loadError) {
      body = `<p class="hint">${esc(this.#loadError)}</p>`;
    } else if (!this.#list) {
      body = `<p class="hint">Reading the run history…</p>`;
    } else {
      const { runs, plans } = this.#list;
      const rows = runs.slice(0, this.#limit).map((r) => {
        const facts = [
          r.work_s != null ? minutes(r.work_s) : "",
          r.progress != null ? `${r.progress}%` : "",
          r.charge_trips ? `${r.charge_trips} charge${r.charge_trips === 1 ? "" : "s"}` : "",
          r.battery_used != null ? `${r.battery_used}% battery` : "",
        ].filter(Boolean);
        const open = this.#shown?.key === r.key;
        return `<li class="${open ? "showing" : ""}"><div class="run">
            <div><b>${esc(when(r.started))}</b> <span class="outcome ${esc(r.outcome)}">${esc(OUTCOMES[r.outcome] ?? r.outcome)}</span>
              <div class="meta">${esc(r.plan ?? "Unknown plan")}${facts.length ? " · " + esc(facts.join(" · ")) : ""}</div></div>
            <button data-run="${esc(r.key)}">${open ? "Hide" : "Show"}</button></div>
            ${open ? this.#detail(this.#shown!) : ""}
          </li>`;
      });
      body = `<div class="row" style="margin-top:0">
          <select id="plan"><option value="">All plans</option>${plans
            .map((p) => `<option value="${p.id}" ${p.id === this.#plan ? "selected" : ""}>${esc(p.name)}</option>`)
            .join("")}</select>
          ${this.isAdmin ? `<button data-refresh title="Read the run history from the robot again">↻</button>` : ""}
        </div>
        <ul class="list runs">${rows.join("") || `<li class="meta">No runs yet.</li>`}</ul>
        ${runs.length > this.#limit ? `<div class="row"><button data-more>Show more (${runs.length - this.#limit})</button></div>` : ""}`;
    }
    this.shadowRoot!.innerHTML = this.frame("Runs", `<style>${STYLE}</style>${body}`);
    this.#wire();
  }

  /** A run's timeline; its areas are shaded on the yard card. */
  #detail(run: Run): string {
    const lines: string[] = [];
    lines.push(`<div class="meta">${esc(run.plan ?? "Unknown plan")}, started ${esc(when(run.started))}${run.started_by ? ` by ${esc(run.started_by)}` : ""}.</div>`);
    if (run.mowed_m2 != null && run.total_m2) {
      lines.push(`<div class="meta">Mowed ${Math.round(run.mowed_m2)} of ${Math.round(run.total_m2)} m²${run.rain ? "; rain was detected" : ""}.</div>`);
    }
    const timeline: [number, string][] = [];
    for (const s of run.segments ?? []) {
      const battery = s.battery_start != null && s.battery_end != null ? ` (battery ${s.battery_start}% → ${s.battery_end}%)` : "";
      timeline.push([s.start, `Working${battery}`]);
      if (s.end) timeline.push([s.end, "Back to the dock"]);
    }
    for (const e of run.events ?? []) {
      const what = {
        plan_started: "Started",
        plan_paused: `Paused${e.reason ? `: ${e.reason}` : ""}${e.fault ? ` (${e.fault})` : e.fault_code ? ` (code ${e.fault_code})` : ""}`,
        plan_resumed: "Resumed",
        plan_finished: `Finished${e.reason ? `: ${e.reason.replaceAll("_", " ")}` : ""}${e.progress != null ? ` at ${Math.round(e.progress)}%` : ""}`,
      }[e.kind] ?? e.kind;
      timeline.push([e.at, what]);
    }
    timeline.sort((a, b) => a[0] - b[0]);
    if (timeline.length) {
      lines.push(
        `<ul class="timeline">${timeline
          .map(([t, what]) => `<li><span>${esc(new Date(t * 1000).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" }))}</span> ${esc(what)}</li>`)
          .join("")}</ul>`,
      );
    }
    if (!run.source.includes("yarbie")) {
      lines.push(`<p class="hint">From the robot's own record only: yarbie was not watching this run, so it cannot say why it ended.</p>`);
    }
    lines.push(`<p class="hint">Its areas are shaded on the yard map: green finished, amber only the edge pass, red not finished.</p>`);
    return `<div class="detail">${lines.join("")}</div>`;
  }

  #wire() {
    const root = this.shadowRoot!;
    root.querySelector<HTMLSelectElement>("#plan")?.addEventListener("change", (e) => {
      const v = (e.target as HTMLSelectElement).value;
      this.#plan = v ? Number(v) : null;
      this.#limit = PAGE;
      setQueryParam("plan", v || null);
      this.#linked = this.#address();
      void this.load();
    });
    root.querySelector("button[data-refresh]")?.addEventListener("click", () => void this.load(true));
    root.querySelector("button[data-more]")?.addEventListener("click", () => {
      this.#limit += PAGE;
      this.render();
    });
    root.querySelectorAll<HTMLButtonElement>("button[data-run]").forEach((b) =>
      b.addEventListener("click", () => void this.#show(this.#shown?.key === b.dataset.run ? null : b.dataset.run!)),
    );
  }

  async #show(key: string | null) {
    if (key === null) {
      this.#shown = null;
    } else {
      try {
        this.#shown = await loadRun((type, data) => this.ws(type, data), this.config.entry_id, key);
      } catch (err) {
        this.say(`Cannot show that run: ${errorText(err)}`, "error");
        return;
      }
      if (!this.#shown) return this.say("No run has ended yet.");
      // A linked run further down the list than it shows.
      const at = this.#list?.runs.findIndex((r) => r.key === this.#shown!.key) ?? -1;
      if (at >= this.#limit) this.#limit = at + 1;
    }
    setQueryParam("run", this.#shown?.key ?? null);
    this.#linked = this.#address();
    window.dispatchEvent(
      new CustomEvent<RunShown>(RUN_EVENT, { detail: { entry_id: this.config.entry_id ?? null, run: this.#shown } }),
    );
    this.render();
  }
}

const STYLE = `
  ul.list.runs > li { display: block; }
  .runs .run { display: flex; justify-content: space-between; align-items: flex-start; gap: 8px; }
  .runs li.showing { background: rgba(var(--rgb-primary-color, 3, 169, 244), 0.06); }
  .outcome { font-size: 0.8em; padding: 1px 6px; border-radius: 9px; margin-left: 4px;
    background: var(--secondary-background-color); color: var(--secondary-text-color); }
  .outcome.completed { background: rgba(var(--rgb-success-color, 67, 160, 71), 0.18); color: var(--success-color, #43a047); }
  .outcome.partial, .outcome.stopped, .outcome.returned_to_dock { background: rgba(var(--rgb-warning-color, 255, 166, 0), 0.18); color: var(--warning-color, #b58100); }
  .outcome.unknown, .outcome.superseded { background: rgba(var(--rgb-error-color, 219, 68, 55), 0.15); color: var(--error-color); }
  .detail { margin-top: 8px; padding: 8px 10px; border: 1px solid var(--divider-color); border-radius: 6px; }
  .timeline { list-style: none; margin: 6px 0; padding: 0; font-size: 0.9em; }
  .timeline li { padding: 2px 0; }
  .timeline span { color: var(--secondary-text-color); display: inline-block; min-width: 50px; }
`;

defineCard(
  "yarbie-runs-card",
  YarbieRunsCard,
  "Yarbo runs",
  "The robot's run history by plan, with each run's timeline; its areas show on the yard map card.",
);
