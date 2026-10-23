// custom:yarbie-yard-card: the yard on a map, as the Yard editor shows it (same photo and
// alignment) but with nothing to edit. It shows what you ask to see: the run in progress,
// a route preview, the wear heat map, and a past run picked on the runs card (or with
// ?run=<key> in the address).
//
// This module is in the cards bundle every page loads, so it holds only the card. The map
// itself (MapLibre, yardView.ts) is imported once the card is on screen.

import type { Heatmap, Imagery, MapResponse, Plan, Progress, RouteCollection, Run } from "./api";
import {
  RUN_EVENT,
  STYLE,
  YarbieCard,
  defineCard,
  errorText,
  esc,
  queryParam,
  type CardConfig,
  type CardHass,
  type RunShown,
} from "./cardBase";
import type { YardView } from "./yardView";

interface YardConfig extends CardConfig {
  tracker?: string; // the robot's device_tracker: where to draw it
}

interface Job<T> {
  id: string;
  status: "running" | "done" | "error";
  result: T | null;
  error: string | null;
}

const RUN_POLL_MS = 10_000;

class YarbieYardCard extends YarbieCard {
  protected declare config: YardConfig;
  #view: YardView | null = null;
  #viewLoading = false;
  #visible: IntersectionObserver | null = null;
  #built = false;
  #yard: MapResponse | null = null;
  #loadError: string | null = null;
  #plans: Plan[] = [];
  // What is shown.
  #runOn = true;
  #run: Progress | null = null;
  #runTimer: number | undefined;
  #heatOn = false;
  #heatDays = 0;
  #heat: Heatmap | null = null;
  #heatInfo = "";
  #routePlan: number | null = null;
  #routeShown: number | null = null;
  #routeStatus: { kind: "computing" | "ok" | "error"; text: string; since: number } | null = null;
  #routeTicker: number | undefined;
  #past: Run | null = null;
  #linkedRun: string | null = null;

  static getStubConfig() {
    return { type: "custom:yarbie-yard-card" };
  }

  set hass(hass: CardHass) {
    super.hass = hass;
    const tracker = this.config.tracker ? hass.states[this.config.tracker] : undefined;
    const lat = Number(tracker?.attributes.latitude);
    const lon = Number(tracker?.attributes.longitude);
    this.#view?.setRobot(tracker && Number.isFinite(lat) && Number.isFinite(lon) ? { lat, lon } : null);
  }

  connectedCallback() {
    window.addEventListener(RUN_EVENT, this.#onRun);
    window.addEventListener("location-changed", this.#follow);
    // The map (and MapLibre with it) is loaded only once the card is on screen.
    if ("IntersectionObserver" in window) {
      this.#visible = new IntersectionObserver((entries) => {
        if (entries.some((e) => e.isIntersecting)) void this.#startMap();
      });
      this.#visible.observe(this);
    } else {
      void this.#startMap();
    }
    this.#syncRun();
  }

  disconnectedCallback() {
    window.removeEventListener(RUN_EVENT, this.#onRun);
    window.removeEventListener("location-changed", this.#follow);
    this.#visible?.disconnect();
    window.clearTimeout(this.#runTimer);
    this.#runTimer = undefined;
  }

  getCardSize() {
    return 8;
  }

  protected editing() {
    return false;
  }

  protected async load() {
    try {
      const [yard, plans] = await Promise.all([this.ws<MapResponse>("map"), this.ws<Plan[]>("plans/list").catch(() => [])]);
      this.#yard = yard;
      this.#plans = plans;
      this.#loadError = null;
      this.#view?.setYard(yard);
    } catch (err) {
      this.#loadError = errorText(err);
    }
    await this.refreshWrites();
    this.#follow();
  }

  // -- the map, loaded on first sight

  async #startMap() {
    if (this.#view || this.#viewLoading) return;
    this.#viewLoading = true;
    this.#build();
    const root = this.shadowRoot!;
    try {
      const { YardView, MAP_CSS } = await import("./yardView");
      const style = document.createElement("style");
      style.textContent = MAP_CSS;
      root.prepend(style);
      const container = root.querySelector<HTMLElement>(".map")!;
      const view = new YardView(container);
      this.#view = view;
      new ResizeObserver(() => view.resize()).observe(container);
      root.querySelector(".map-note")?.remove();
    } catch (err) {
      root.querySelector(".map-note")!.textContent = `The map could not load: ${errorText(err)}`;
      return;
    } finally {
      this.#viewLoading = false;
    }
    if (this.#yard) this.#view.setYard(this.#yard);
    if (this.hassObj) this.hass = this.hassObj; // the robot's position
    this.#view.setRun(this.#runOn ? this.#run : null);
    this.#view.setHeat(this.#heatOn ? this.#heat : null);
    if (this.#past) this.#view.setPastRun(this.#past.area_states ?? {});
    if (this.isAdmin) {
      // Google satellite needs the key from the integration's options: admins only.
      try {
        const extra = await this.ws<{ sources: Imagery[] }>("imagery");
        this.#view.addImagery(extra.sources);
      } catch {
        // the free photos still show
      }
    }
  }

  /** The card's frame, once: the map must survive every redraw of the controls. */
  #build() {
    if (this.#built) return;
    this.#built = true;
    this.shadowRoot!.innerHTML = `<style>${STYLE}${YARD_STYLE}</style>
      <ha-card ${this.config.title ? `header="${esc(this.config.title)}"` : ""}>
        <div class="map-wrap"><div class="map"></div><p class="map-note hint">Loading the map…</p></div>
        <div class="card-content"><div class="controls"></div><p class="message"></p></div>
      </ha-card>`;
  }

  // -- the controls under the map

  protected render() {
    this.#build();
    const box = this.shadowRoot!.querySelector<HTMLElement>(".controls")!;
    if (this.#loadError) {
      box.innerHTML = `<p class="hint">${esc(this.#loadError)}</p>`;
      return;
    }
    const busy = !!this.writes?.busy;
    const admin = this.isAdmin;
    const plans = this.#plans
      .map((p) => `<option value="${p.id}" ${p.id === this.#routePlan ? "selected" : ""}>${esc(p.name)}</option>`)
      .join("");
    const computing = this.#routeStatus?.kind === "computing";
    box.innerHTML = `
      <div class="layer">
        <label class="check"><input type="checkbox" id="run-on" ${this.#runOn ? "checked" : ""} ${busy ? "" : "disabled"}/>
          Current run${busy ? "" : " <span class=\"hint\">(the robot is not in a plan)</span>"}</label>
        ${busy && this.#runOn ? `<div class="info">${this.#runInfo()}</div>` : ""}
      </div>
      <div class="layer">
        <div class="row" style="margin-top:0">
          <label class="check"><input type="checkbox" id="heat-on" ${this.#heatOn ? "checked" : ""}/> Wear (estimated)</label>
          <select id="heat-days">${[
            [0, "All history"],
            [365, "Last year"],
            [90, "Last 90 days"],
            [30, "Last 30 days"],
          ]
            .map(([v, label]) => `<option value="${v}" ${v === this.#heatDays ? "selected" : ""}>${label}</option>`)
            .join("")}</select>
          <button class="icon" id="heat-refresh" title="Read the run history from the robot again">↻</button>
        </div>
        ${this.#heatOn ? `<div class="info">${this.#heatInfo}</div>` : ""}
      </div>
      <div class="layer">
        ${
          admin
            ? `<div class="row" style="margin-top:0">
                <span>Route preview</span>
                <select id="route-plan">${plans || `<option value="">No plans</option>`}</select>
                <button id="route" ${!this.#plans.length || computing || (busy && this.#routeShown === null) ? "disabled" : ""}
                  title="${busy ? "Not while a plan runs or is paused" : "The route the robot would take, with the plan's areas outlined"}">${
                    computing ? "Computing…" : this.#routeShown !== null && this.#routeShown === this.#routePlan ? "Hide route" : "Show route"
                  }</button>
              </div>
              ${this.#routeStatus ? `<div class="info route ${this.#routeStatus.kind}" id="route-status">${esc(this.#routeLine())}</div>` : ""}`
            : `<p class="hint">Route previews ask the robot to plan a route, so only administrators can start them.</p>`
        }
      </div>
      ${
        this.#past
          ? `<div class="layer"><div class="row" style="margin-top:0">
              <span>Run of <b>${esc(new Date(this.#past.started * 1000).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" }))}</b>, ${esc(this.#past.plan ?? "unknown plan")}</span>
              <button class="icon" id="past-clear" title="Stop showing this run">✕</button></div>
              <div class="info">Green: finished. Amber: only the edge pass. Red: not finished. Drawn on today's map.</div></div>`
          : ""
      }
      <div class="row">
        <button id="fit">Zoom to yard</button>
        ${admin ? `<a id="editor">Edit the map in the Yard editor</a>` : ""}
      </div>`;
    this.#wire(box);
    this.#syncRun();
  }

  #wire(box: HTMLElement) {
    const on = (id: string, type: string, fn: (el: HTMLInputElement & HTMLSelectElement) => void) =>
      box.querySelector(`#${id}`)?.addEventListener(type, (e) => fn(e.currentTarget as HTMLInputElement & HTMLSelectElement));
    on("run-on", "change", (el) => {
      this.#runOn = el.checked;
      this.#view?.setRun(this.#runOn ? this.#run : null);
      this.render();
    });
    on("heat-on", "change", (el) => {
      this.#heatOn = el.checked;
      void this.#loadHeat(false);
    });
    on("heat-days", "change", (el) => {
      this.#heatDays = Number(el.value);
      if (this.#heatOn) void this.#loadHeat(false);
    });
    on("heat-refresh", "click", () => {
      this.#heatOn = true;
      void this.#loadHeat(true);
    });
    on("route-plan", "change", (el) => {
      this.#routePlan = el.value ? Number(el.value) : null;
      this.render();
    });
    on("route", "click", () => void this.#toggleRoute());
    on("past-clear", "click", () => this.#showPast(null));
    on("fit", "click", () => this.#view?.fit());
    on("editor", "click", () => this.navigate("/yard-editor"));
  }

  // -- the run in progress: what has been mowed, what is left

  /** Poll the run while the robot is in a plan and the layer is on; stop otherwise. */
  #syncRun() {
    const want = this.isConnected && this.#runOn && !!this.writes?.busy;
    if (want && this.#runTimer === undefined) {
      void this.#pollRun();
    } else if (!want) {
      window.clearTimeout(this.#runTimer);
      this.#runTimer = undefined;
      if (this.#run) {
        this.#run = null;
        this.#view?.setRun(null);
      }
    }
  }

  async #pollRun() {
    this.#runTimer = window.setTimeout(() => void this.#pollRun(), RUN_POLL_MS);
    try {
      this.#run = await this.ws<Progress>("progress");
    } catch (err) {
      this.say(`Could not read the run: ${errorText(err)}`, "error");
      return;
    }
    if (!this.#runOn || !this.writes?.busy) return;
    this.#view?.setRun(this.#run);
    const info = this.shadowRoot!.querySelector<HTMLElement>(".controls .layer .info");
    if (info) info.innerHTML = this.#runInfo();
    else this.render();
  }

  #runInfo(): string {
    const run = this.#run;
    if (!run) return "Reading the run…";
    if (!run.plan) return "The robot has no run to show.";
    const name = (id: number | null | undefined) =>
      this.#yard?.features.features.find((f) => f.properties.family === "areas" && f.properties.id === id)?.properties.name ?? `#${id}`;
    const hours = (s: number | null | undefined) =>
      s == null ? "?" : s >= 3600 ? `${Math.floor(s / 3600)} h ${Math.round((s % 3600) / 60)} min` : `${Math.round(s / 60)} min`;
    const rows = (run.area_ids ?? []).map((id) => {
      const parts = (run.paths ?? []).filter((p) => p.area_id === id);
      const fill = parts.find((p) => p.kind === "fill")?.fraction ?? 0;
      const edge = parts.find((p) => p.kind === "edge")?.fraction ?? 0;
      const state =
        fill >= 1 && edge >= 1 ? "mowed" : fill > 0 || edge > 0 ? `mowing, ${Math.round(fill * 100)}% of rows${edge >= 1 ? ", edge done" : ""}` : "not started";
      return `<li class="${id === run.current_area ? "now" : ""}"><span>${esc(name(id))}</span><span>${state}</span></li>`;
    });
    const pct = Math.round(run.progress ?? 0);
    return `<b>${esc(run.plan.name ?? `Plan ${run.plan.id}`)}</b> · ${run.running ? "running" : "paused"}
      <div class="bar"><i style="width:${pct}%"></i></div>
      ${pct}% of the plan's area${run.running ? ` · about ${hours(run.remaining_s)} left` : ""}
      <ul class="run-areas">${rows.join("")}</ul>
      <span>Green: mowed. Dashed: still to mow.</span>`;
  }

  // -- wear: run history spread over the map (the robot records no tracks)

  async #loadHeat(refresh: boolean) {
    if (!this.#heatOn) {
      this.#view?.setHeat(null);
      this.render();
      return;
    }
    this.#heatInfo = "Reading the robot's run history…";
    this.render();
    try {
      this.#heat = await this.ws<Heatmap>("heatmap", { days: this.#heatDays, refresh });
    } catch (err) {
      this.#heatInfo = `No heat map: ${esc(errorText(err))}`;
      this.render();
      return;
    }
    const heat = this.#heat;
    const day = (t: number | null) => (t ? new Date(t * 1000).toLocaleDateString(undefined, { dateStyle: "medium" }) : "?");
    this.#heatInfo = heat.runs
      ? `<div class="scale"></div>
         <div class="row" style="margin:0;justify-content:space-between"><span>rarely</span><span>most driven</span></div>
         ${heat.runs} runs, ${day(heat.first)} – ${day(heat.last)}, ${heat.trips} trips out and home.
         Busiest pathways: ${heat.pathways.map((p) => `${esc(p.name)} (~${p.passes})`).join(", ") || "none"}.
         An estimate: the robot keeps no record of where it drove, so each run's areas, edge laps and
         recharge trips are spread over today's map.`
      : "No runs in this period.";
    if (this.#heatOn) this.#view?.setHeat(heat);
    this.render();
  }

  // -- route preview: a job on the robot that can take a minute or more

  #routeLine(): string {
    const s = this.#routeStatus!;
    return s.kind === "computing" ? `${s.text} (${Math.round((Date.now() - s.since) / 1000)} s)` : s.text;
  }

  async #toggleRoute() {
    const planId = this.#routePlan ?? this.#plans[0]?.id;
    if (planId === undefined) return;
    this.#routePlan = planId;
    if (this.#routeShown === planId) {
      this.#routeShown = null;
      this.#routeStatus = null;
      this.#view?.setRoute(null);
      this.render();
      return;
    }
    this.#routeStatus = {
      kind: "computing",
      text: "The robot is working out the route; a big yard can take a minute or more.",
      since: Date.now(),
    };
    window.clearInterval(this.#routeTicker);
    this.#routeTicker = window.setInterval(() => {
      const line = this.shadowRoot!.querySelector("#route-status");
      if (line && this.#routeStatus?.kind === "computing") line.textContent = this.#routeLine();
    }, 1000);
    this.render();
    try {
      const route = await this.#job<RouteCollection>("route/start", { plan_id: planId });
      if (!route.features.length) throw new Error("the robot returned no route for this plan");
      const plan = this.#plans.find((p) => p.id === planId);
      this.#routeShown = planId;
      this.#view?.setRoute(route, plan?.area_ids ?? []);
      const minutes = route.total_s ? `about ${Math.round(route.total_s / 60)} min` : "no time estimate";
      const areas = new Set(route.features.map((f) => `${f.properties.area_id}`)).size;
      this.#routeStatus = {
        kind: "ok",
        text: `${areas} area${areas === 1 ? "" : "s"}, ${minutes}. Dashed blue is the edge lap; the plan's areas are outlined.`,
        since: Date.now(),
      };
    } catch (err) {
      this.#routeStatus = { kind: "error", text: `No route: ${errorText(err)}`, since: Date.now() };
    }
    window.clearInterval(this.#routeTicker);
    this.render();
  }

  /** Start (or join) a job and wait for it to end. Following it again after a reconnect
   * picks up the same job; the robot is not asked twice. */
  async #job<T>(start: string, data: Record<string, unknown>): Promise<T> {
    const finish = (j: Job<T>): T => {
      if (j.status === "error") throw new Error(j.error ?? "the robot refused");
      return j.result as T;
    };
    const first = await this.ws<Job<T>>(start, data);
    if (first.status !== "running") return finish(first);
    const connection = this.hassObj?.connection;
    if (!connection) throw new Error("not connected to Home Assistant");
    return await new Promise<T>((resolve, reject) => {
      let unsubscribe: (() => Promise<void>) | null = null;
      let settled = false;
      const done = (j: Job<T>) => {
        if (j.status === "running" || settled) return;
        settled = true;
        void unsubscribe?.();
        try {
          resolve(finish(j));
        } catch (err) {
          reject(err);
        }
      };
      const entry = this.config.entry_id ? { entry_id: this.config.entry_id } : {};
      connection
        .subscribeMessage<Job<T>>(done, { type: "yarbie/job/subscribe", job_id: first.id, ...entry })
        .then((unsub) => {
          unsubscribe = unsub;
          if (settled) void unsub();
        })
        .catch(reject);
    });
  }

  // -- a past run, from the runs card or the address

  #onRun = (e: Event) => {
    const { entry_id, run } = (e as CustomEvent<RunShown>).detail;
    if ((entry_id ?? null) !== (this.config.entry_id ?? null)) return;
    this.#showPast(run);
  };

  /** ?run=<key> (``last``: the newest that ended) shows that run's areas. */
  #follow = () => {
    const key = queryParam("run");
    if (key === this.#linkedRun || !this.hassObj) return;
    this.#linkedRun = key;
    if (!key) return;
    void (async () => {
      try {
        let found = key;
        if (key === "last") {
          const { runs } = await this.ws<{ runs: Run[] }>("runs/list", { limit: 5 });
          found = runs.find((r) => r.outcome !== "running")?.key ?? "";
        }
        if (found) this.#showPast(await this.ws<Run>("runs/get", { key: found }));
      } catch (err) {
        this.say(`Cannot show that run: ${errorText(err)}`, "error");
      }
    })();
  };

  #showPast(run: Run | null) {
    this.#past = run;
    this.#view?.setPastRun(run ? (run.area_states ?? {}) : null);
    this.render();
  }
}

const YARD_STYLE = `
  .map-wrap { position: relative; height: 380px; margin: 0 0 8px; overflow: hidden; }
  .map { position: absolute; inset: 0; }
  .map-note { position: absolute; inset: auto 16px 8px; margin: 0; }
  .layer { padding: 6px 0; border-bottom: 1px solid var(--divider-color); }
  .layer:last-of-type { border-bottom: none; }
  .layer label.check { margin: 0; }
  .info { font-size: 0.9em; color: var(--secondary-text-color); margin: 4px 0 2px; }
  .info.route.computing { color: var(--warning-color, #b58100); }
  .info.route.error { color: var(--error-color); }
  .bar { height: 6px; border-radius: 3px; background: var(--divider-color); overflow: hidden; margin: 6px 0; }
  .bar > i { display: block; height: 100%; background: #3fb950; }
  .run-areas { list-style: none; margin: 6px 0; padding: 0; display: grid; gap: 2px; }
  .run-areas li { display: flex; justify-content: space-between; gap: 8px; }
  .run-areas .now { color: var(--primary-text-color); font-weight: 600; }
  .scale { height: 8px; border-radius: 4px; margin: 6px 0 2px;
    background: linear-gradient(90deg, rgba(33,102,172,0.5), #67a9cf, #fddb6d, #f46d43, #b2182b); }
  .maplibregl-ctrl-attrib { font-size: 10px; }
`;

defineCard(
  "yarbie-yard-card",
  YarbieYardCard,
  "Yarbo yard",
  "The yard on a satellite map: the run in progress, route previews, wear, and past runs. Nothing to edit.",
);
