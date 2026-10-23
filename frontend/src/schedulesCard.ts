// custom:yarbie-schedules-card: the robot's schedules, with Add, Edit, on/off and Delete.
//
// What schedule_type, week_day and return_method mean when a schedule fires is not
// known: only a disabled schedule has been stored on a real robot. So the card shows and
// sends the robot's own numbers, labels them unconfirmed, and starts new schedules with
// the numbers the Yarbo app sent. The day picker is one reading of week_day (a bitmask,
// Monday = 1), the same the calendar uses, and says so.

import { YarbieCard, defineCard, errorText, esc } from "./cardBase";

interface Schedule {
  id: number;
  name: string;
  plan_id: number | null;
  plan: string | null;
  enabled: boolean;
  start_time: string;
  end_time: string;
  week_day: number | null;
  schedule_type: number | null;
  return_method: number | null;
  resume_progress: boolean;
  last_progress: number;
}

interface Reply {
  schedules: Schedule[];
  plans: { id: number; name: string }[];
  defaults: { schedule_type: number; week_day: number; return_method: number };
  reading: string;
}

interface Draft {
  schedule_id: number | null;
  name: string;
  plan_id: number | null;
  start_time: string;
  end_time: string;
  week_day: number;
  schedule_type: number;
  return_method: number;
  enabled: boolean;
  resume_progress: boolean;
}

const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

/** The days a week_day number would be if it is a bitmask with Monday as 1 (unconfirmed). */
function guessDays(weekDay: number | null): string {
  if (weekDay === null || weekDay <= 0) return "no day";
  const days = DAYS.filter((_, i) => weekDay & (1 << i));
  return days.length ? days.join(", ") : "no day";
}

const hhmm = (t: string) => t.slice(0, 5);

class YarbieSchedulesCard extends YarbieCard {
  #data: Reply | null = null;
  #draft: Draft | null = null;
  #loadError: string | null = null;
  #sending = false;

  static getStubConfig() {
    return { type: "custom:yarbie-schedules-card" };
  }

  protected editing() {
    return this.#draft !== null;
  }

  protected blocked(control: HTMLElement) {
    if (this.#sending) return true;
    if (control.dataset.new !== undefined) return !this.#data?.plans.length;
    if (control.dataset.toggle !== undefined) return this.#draft !== null;
    return false;
  }

  protected async load() {
    if (!this.isAdmin) {
      this.#loadError = "Only administrators can manage schedules.";
      this.render();
      return;
    }
    try {
      this.#data = await this.ws<Reply>("schedules/list");
      this.#loadError = null;
    } catch (err) {
      this.#loadError = errorText(err);
    }
    await this.refreshWrites();
  }

  protected render() {
    const root = this.shadowRoot!;
    let body: string;
    if (this.#loadError) {
      body = `<p class="hint">${esc(this.#loadError)}</p>`;
    } else if (!this.#data) {
      body = `<p class="hint">Reading the schedules…</p>`;
    } else {
      const refusal = this.refusal();
      const off = refusal || this.#sending || this.#draft ? "disabled" : "";
      const rows = this.#data.schedules.length
        ? this.#data.schedules
            .map(
              (s) => `<li>
                <div><b>${esc(s.name || `Schedule ${s.id}`)}</b>
                  <div class="meta">${esc(s.plan ?? `plan ${s.plan_id}`)} · ${esc(hhmm(s.start_time))}–${esc(hhmm(s.end_time))}
                    · week_day ${esc(s.week_day)} <span class="unconfirmed" title="${esc(this.#data!.reading)}">(${esc(guessDays(s.week_day))}?)</span></div></div>
                <span class="buttons">
                  <label class="check" title="On or off (sent to the robot)"><input type="checkbox" data-toggle="${s.id}" data-write ${s.enabled ? "checked" : ""} ${off}/></label>
                  <button data-edit="${s.id}" ${this.#draft ? "disabled" : ""}>Edit</button>
                </span>
              </li>`,
            )
            .join("")
        : `<li class="meta">No schedules.</li>`;
      body = `<p class="hint unconfirmed">Unconfirmed: what the robot does with <code>week_day</code>,
          <code>schedule_type</code> and <code>return_method</code> when a schedule fires is not known yet
          (only a disabled schedule has been tried on a real robot). They are shown and sent as the robot's
          own numbers; captures from the Yarbo app are still needed.</p>
        <ul class="list">${rows}</ul>
        ${
          this.#draft
            ? this.#form()
            : `<div class="row"><button data-new data-write ${refusal || !this.#data.plans.length ? "disabled" : ""}>Add schedule</button>
                <button data-reload title="Read the schedules from the robot again">↻</button></div>`
        }`;
    }
    root.innerHTML = this.frame("Schedules", body);
    this.#wire();
  }

  #form(): string {
    const d = this.#draft!;
    const data = this.#data!;
    const refusal = this.refusal();
    const off = refusal || this.#sending ? "disabled" : "";
    const days = DAYS.map(
      (day, i) =>
        `<label class="check"><input type="checkbox" data-day="${i}" ${d.week_day & (1 << i) ? "checked" : ""}/> ${day}</label>`,
    ).join("");
    const app = data.defaults;
    return `<div class="form">
      <h3>${d.schedule_id === null ? "New schedule" : "Edit schedule"}</h3>
      <label class="field">Name<input type="text" id="name" value="${esc(d.name)}" /></label>
      <label class="field">Plan<select id="plan">${
        // A plan the robot no longer has (or none) is not shown as the first one: pick one.
        data.plans.some((p) => p.id === d.plan_id) ? "" : `<option value="" selected disabled>Choose a plan</option>`
      }${data.plans
        .map((p) => `<option value="${p.id}" ${p.id === d.plan_id ? "selected" : ""}>${esc(p.name)}</option>`)
        .join("")}</select></label>
      <div class="row">
        <label class="field">Start<input type="time" id="start" value="${esc(hhmm(d.start_time))}" /></label>
        <label class="field">End<input type="time" id="end" value="${esc(hhmm(d.end_time))}" /></label>
      </div>
      <p class="hint">Times are sent as written. Whether the robot reads them as local time or UTC is not known.</p>
      <fieldset><legend>Days</legend>
        <label class="field">week_day (the robot's number)<input type="number" id="week_day" min="0" step="1" value="${d.week_day}" /></label>
        <div class="days">${days}</div>
        <p class="hint unconfirmed">Unconfirmed: the day boxes read week_day as a bitmask with Monday as 1,
          Tuesday 2, Wednesday 4…, as the calendar does. It may instead be a single day. The app sent ${app.week_day}.</p>
      </fieldset>
      <fieldset><legend>When it fires (meanings unconfirmed)</legend>
        <label class="field">return_method <span class="unconfirmed">meaning not known; the app sent ${app.return_method}</span>
          <input type="number" id="return_method" min="0" step="1" value="${d.return_method}" /></label>
        <label class="field">schedule_type <span class="unconfirmed">meaning not known; the app sent ${app.schedule_type}</span>
          <input type="number" id="schedule_type" min="0" step="1" value="${d.schedule_type}" /></label>
        <label class="check"><input type="checkbox" id="resume" ${d.resume_progress ? "checked" : ""}/> Resume from last progress
          <span class="meta">(<code>enable_last_progress</code>)</span></label>
      </fieldset>
      <label class="check"><input type="checkbox" id="enabled" ${d.enabled ? "checked" : ""}/> On</label>
      <div class="row">
        <button class="primary" data-save data-write ${off}>Save to robot</button>
        ${d.schedule_id !== null ? `<button class="danger" data-delete data-write ${off}>Delete</button>` : ""}
        <button data-cancel>Cancel</button>
      </div>
    </div>`;
  }

  #wire() {
    const root = this.shadowRoot!;
    const on = (selector: string, fn: (el: HTMLElement) => void) =>
      root.querySelectorAll<HTMLElement>(selector).forEach((el) => el.addEventListener("click", () => fn(el)));
    on("button[data-reload]", () => void this.load());
    on("button[data-new]", () => this.#open(null));
    on("button[data-edit]", (b) => this.#open(this.#data?.schedules.find((s) => s.id === Number(b.dataset.edit)) ?? null));
    root.querySelectorAll<HTMLInputElement>("input[data-toggle]").forEach((box) =>
      box.addEventListener("change", () => void this.#toggle(Number(box.dataset.toggle), box.checked)),
    );
    const d = this.#draft;
    if (!d) return;
    const input = (id: string) => root.querySelector<HTMLInputElement>(`#${id}`)!;
    input("name").addEventListener("input", () => (d.name = input("name").value));
    root.querySelector<HTMLSelectElement>("#plan")!.addEventListener("change", (e) => (d.plan_id = Number((e.target as HTMLSelectElement).value)));
    input("start").addEventListener("change", () => (d.start_time = `${input("start").value}:00`));
    input("end").addEventListener("change", () => (d.end_time = `${input("end").value}:00`));
    input("return_method").addEventListener("change", () => (d.return_method = Number(input("return_method").value)));
    input("schedule_type").addEventListener("change", () => (d.schedule_type = Number(input("schedule_type").value)));
    input("resume").addEventListener("change", () => (d.resume_progress = input("resume").checked));
    input("enabled").addEventListener("change", () => (d.enabled = input("enabled").checked));
    // The number and the boxes follow each other.
    input("week_day").addEventListener("change", () => {
      d.week_day = Math.max(0, Math.trunc(Number(input("week_day").value) || 0));
      root.querySelectorAll<HTMLInputElement>("input[data-day]").forEach((box) => (box.checked = !!(d.week_day & (1 << Number(box.dataset.day)))));
    });
    root.querySelectorAll<HTMLInputElement>("input[data-day]").forEach((box) =>
      box.addEventListener("change", () => {
        const bit = 1 << Number(box.dataset.day);
        d.week_day = box.checked ? d.week_day | bit : d.week_day & ~bit;
        input("week_day").value = String(d.week_day);
      }),
    );
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

  #open(s: Schedule | null) {
    const app = this.#data!.defaults;
    this.#draft = s
      ? {
          schedule_id: s.id,
          name: s.name,
          plan_id: s.plan_id,
          start_time: s.start_time,
          end_time: s.end_time,
          week_day: s.week_day ?? app.week_day,
          schedule_type: s.schedule_type ?? app.schedule_type,
          return_method: s.return_method ?? app.return_method,
          enabled: s.enabled,
          resume_progress: s.resume_progress,
        }
      : {
          schedule_id: null,
          name: "",
          plan_id: this.#data!.plans[0]?.id ?? null,
          start_time: "09:00:00",
          end_time: "11:00:00",
          ...app,
          enabled: true,
          resume_progress: false,
        };
    this.say("");
    this.render();
    this.shadowRoot!.querySelector<HTMLInputElement>("#name")?.focus();
  }

  async #save() {
    const d = this.#draft;
    if (!d) return;
    const name = d.name.trim();
    if (!name || !this.#data!.plans.some((p) => p.id === d.plan_id)) return this.say("A schedule needs a name and a plan.", "error");
    const { schedule_id, ...rest } = d;
    await this.#send(async () => {
      await this.ws("schedules/save", { ...rest, name, ...(schedule_id === null ? {} : { schedule_id }) });
      return "Schedule saved.";
    });
  }

  async #delete() {
    const id = this.#draft?.schedule_id;
    if (id === null || id === undefined) return;
    await this.#send(async () => {
      await this.ws("schedules/delete", { schedule_id: id });
      return "Schedule deleted.";
    });
  }

  async #toggle(id: number, enabled: boolean) {
    const s = this.#data?.schedules.find((x) => x.id === id);
    if (!s || s.plan_id === null) return;
    await this.#send(async () => {
      // The robot's record back as it is, with only "on" changed.
      await this.ws("schedules/save", {
        schedule_id: s.id,
        name: s.name || `Schedule ${s.id}`,
        plan_id: s.plan_id,
        start_time: s.start_time,
        end_time: s.end_time,
        week_day: s.week_day ?? this.#data!.defaults.week_day,
        schedule_type: s.schedule_type ?? this.#data!.defaults.schedule_type,
        return_method: s.return_method ?? this.#data!.defaults.return_method,
        resume_progress: s.resume_progress,
        enabled,
      });
      return `${s.name || "Schedule"} turned ${enabled ? "on" : "off"}.`;
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
}

defineCard(
  "yarbie-schedules-card",
  YarbieSchedulesCard,
  "Yarbo schedules",
  "The robot's schedules: add, edit, turn on or off and delete them (field meanings still unconfirmed).",
);
