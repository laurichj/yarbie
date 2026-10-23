// custom:yarbie-plans-card: the robot's plans, with New, Edit (name, areas, their order),
// Delete and Start. Plans are a robot setting, not map geometry, so they are edited here
// beside the robot's other controls; the Yard editor keeps only the route preview.

import { YarbieCard, defineCard, errorText, esc } from "./cardBase";
import type { Plan } from "./api";

interface Area {
  id: number;
  name: string;
}

interface Draft {
  id: number | null;
  name: string;
  area_ids: number[]; // in the order they are sent
}

class YarbiePlansCard extends YarbieCard {
  #plans: Plan[] | null = null;
  #areas: Area[] = [];
  #draft: Draft | null = null;
  #loadError: string | null = null;
  #sending = false;

  static getStubConfig() {
    return { type: "custom:yarbie-plans-card" };
  }

  protected editing() {
    return this.#draft !== null;
  }

  protected blocked() {
    return this.#sending;
  }

  protected async load() {
    if (!this.isAdmin) {
      this.#loadError = "Only administrators can manage plans. The plan select still picks which one Start runs.";
      this.render();
      return;
    }
    try {
      const [plans, areas] = await Promise.all([this.ws<Plan[]>("plans/list"), this.ws<Area[]>("areas/list")]);
      this.#plans = plans;
      this.#areas = areas;
      this.#loadError = null;
    } catch (err) {
      this.#loadError = errorText(err);
    }
    await this.refreshWrites();
  }

  #areaName(id: number): string {
    return this.#areas.find((a) => a.id === id)?.name ?? `Area ${id}`;
  }

  protected render() {
    const root = this.shadowRoot!;
    let body: string;
    if (this.#loadError) {
      body = `<p class="hint">${esc(this.#loadError)}</p>`;
    } else if (this.#plans === null) {
      body = `<p class="hint">Reading the plans…</p>`;
    } else {
      const refusal = this.refusal();
      const startable = !!this.writes?.connected && !this.writes?.busy;
      const rows = this.#plans.length
        ? this.#plans
            .map(
              (p) => `<li>
                <div><b>${esc(p.name)}</b><div class="meta">${p.area_ids.map((a) => esc(this.#areaName(a))).join(" → ")}</div></div>
                <span class="buttons">
                  <button data-start="${p.id}" ${startable && !this.#sending ? "" : "disabled"} title="Start this plan now">Start</button>
                  <button data-edit="${p.id}" ${this.#draft ? "disabled" : ""}>Edit</button>
                </span>
              </li>`,
            )
            .join("")
        : `<li class="meta">No plans yet.</li>`;
      body = `<ul class="list">${rows}</ul>
        ${this.#draft ? this.#form() : `<div class="row"><button data-new data-write ${refusal ? "disabled" : ""}>New plan</button>
          <a data-editor class="hint">Preview a route in the Yard editor</a></div>`}`;
    }
    root.innerHTML = this.frame("Plans", body);
    this.#wire();
  }

  #form(): string {
    const d = this.#draft!;
    const refusal = this.refusal();
    const chosen = new Set(d.area_ids);
    return `<div class="form">
      <h3>${d.id === null ? "New plan" : "Edit plan"}</h3>
      <label class="field">Name<input type="text" id="name" value="${esc(d.name)}" /></label>
      <fieldset><legend>Areas</legend>
        ${this.#areas
          .map(
            (a) => `<label class="check"><input type="checkbox" data-area="${a.id}" ${chosen.has(a.id) ? "checked" : ""}/> ${esc(a.name)}</label>`,
          )
          .join("") || `<p class="hint">The robot has no areas.</p>`}
      </fieldset>
      ${
        d.area_ids.length > 1
          ? `<fieldset><legend>Order</legend><ul class="list order">${d.area_ids
              .map(
                (id, i) => `<li><span>${i + 1}. ${esc(this.#areaName(id))}</span><span class="buttons">
                  <button class="icon" data-up="${i}" ${i === 0 ? "disabled" : ""} title="Earlier">▲</button>
                  <button class="icon" data-down="${i}" ${i === d.area_ids.length - 1 ? "disabled" : ""} title="Later">▼</button></span></li>`,
              )
              .join("")}</ul>
            <p class="hint">Sent in this order. Like the app, yarbie lets the robot order the areas itself
              (<code>enable_self_order</code>), so it may choose another.</p></fieldset>`
          : ""
      }
      <div class="row">
        <button class="primary" data-save data-write ${refusal || this.#sending ? "disabled" : ""}>Save to robot</button>
        ${d.id !== null ? `<button class="danger" data-delete data-write ${refusal || this.#sending ? "disabled" : ""}>Delete</button>` : ""}
        <button data-cancel>Cancel</button>
      </div>
      ${d.id !== null ? `<p class="hint">Deleting a plan also deletes its schedules (the robot does that).</p>` : ""}
    </div>`;
  }

  #wire() {
    const root = this.shadowRoot!;
    const on = (selector: string, fn: (b: HTMLElement) => void) =>
      root.querySelectorAll<HTMLElement>(selector).forEach((el) => el.addEventListener("click", () => fn(el)));
    on("button[data-start]", (b) => void this.#start(Number(b.dataset.start)));
    on("button[data-edit]", (b) => {
      const plan = this.#plans?.find((p) => p.id === Number(b.dataset.edit));
      if (plan) this.#open({ id: plan.id, name: plan.name, area_ids: [...plan.area_ids] });
    });
    on("button[data-new]", () => this.#open({ id: null, name: "", area_ids: [] }));
    on("a[data-editor]", () => this.navigate("/yard-editor"));
    if (!this.#draft) return;
    const d = this.#draft;
    root.querySelector<HTMLInputElement>("#name")?.addEventListener("input", (e) => (d.name = (e.target as HTMLInputElement).value));
    root.querySelectorAll<HTMLInputElement>("input[data-area]").forEach((box) =>
      box.addEventListener("change", () => {
        const id = Number(box.dataset.area);
        d.area_ids = box.checked ? [...d.area_ids.filter((a) => a !== id), id] : d.area_ids.filter((a) => a !== id);
        this.render();
      }),
    );
    const move = (i: number, by: number) => {
      const ids = d.area_ids;
      [ids[i], ids[i + by]] = [ids[i + by], ids[i]];
      this.render();
    };
    on("button[data-up]", (b) => move(Number(b.dataset.up), -1));
    on("button[data-down]", (b) => move(Number(b.dataset.down), 1));
    on("button[data-cancel]", () => {
      this.#draft = null;
      this.say("");
      void this.load();
    });
    on("button[data-save]", () => void this.#save());
    on("button[data-delete]", (b) => {
      if (!b.classList.contains("confirm")) {
        b.classList.add("confirm");
        b.textContent = "Confirm delete";
        return;
      }
      void this.#delete();
    });
  }

  #open(draft: Draft) {
    this.#draft = draft;
    this.say("");
    this.render();
    this.shadowRoot!.querySelector<HTMLInputElement>("#name")?.focus();
  }

  async #save() {
    const d = this.#draft;
    if (!d) return;
    const name = d.name.trim();
    if (!name || !d.area_ids.length) return this.say("A plan needs a name and at least one area.", "error");
    await this.#send(async () => {
      await this.ws("plans/save", { name, area_ids: d.area_ids, ...(d.id === null ? {} : { plan_id: d.id }) });
      return "Plan saved.";
    });
  }

  async #delete() {
    const d = this.#draft;
    if (!d || d.id === null) return;
    await this.#send(async () => {
      await this.ws("plans/delete", { plan_id: d.id });
      return "Plan deleted.";
    });
  }

  async #send(work: () => Promise<string>) {
    this.#sending = true;
    this.render();
    try {
      const done = await work();
      this.#draft = null;
      this.message = { text: done, kind: "ok" };
    } catch (err) {
      this.message = { text: errorText(err), kind: "error" };
    }
    this.#sending = false;
    await this.load();
  }

  async #start(planId: number) {
    const plan = this.#plans?.find((p) => p.id === planId);
    this.#sending = true;
    this.render();
    try {
      // The action, not a websocket command: starting is a control, open to every user.
      await this.hassObj!.callService("yarbie", "start_plan", {
        plan_id: planId,
        ...(this.config.entry_id ? { config_entry_id: this.config.entry_id } : {}),
      });
      this.message = { text: `Started ${plan?.name ?? "the plan"}.`, kind: "ok" };
    } catch (err) {
      this.message = { text: errorText(err), kind: "error" };
    }
    this.#sending = false;
    await this.refreshWrites();
  }
}

defineCard(
  "yarbie-plans-card",
  YarbiePlansCard,
  "Yarbo plans",
  "The robot's plans: create, edit (areas and their order), delete and start them.",
);
