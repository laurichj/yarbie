//#region src/cardBase.ts
var e = (e) => String(e ?? "").replace(/[&<>"']/g, (e) => ({
	"&": "&amp;",
	"<": "&lt;",
	">": "&gt;",
	"\"": "&quot;",
	"'": "&#39;"
})[e]), t = class extends Error {
	code;
	constructor(e, t) {
		super(e), this.code = t;
	}
}, n = class extends HTMLElement {
	config = { type: "" };
	hassObj = null;
	writes = null;
	message = {
		text: "",
		kind: ""
	};
	#e = "";
	#t = "";
	constructor() {
		super(), this.attachShadow({ mode: "open" });
	}
	setConfig(e) {
		this.config = { ...e }, this.hassObj && this.load();
	}
	set hass(e) {
		let t = !this.hassObj;
		this.hassObj = e;
		let n = this.config.entity ? JSON.stringify(e.states[this.config.entity]?.attributes ?? null) : "", r = this.config.mower ? e.states[this.config.mower]?.state ?? "" : "";
		t ? (this.#e = n, this.#t = r, this.load()) : (r !== this.#t && (this.#t = r, this.refreshWrites()), n !== this.#e && (this.#e = n, this.editing() || this.load()));
	}
	getCardSize() {
		return 4;
	}
	getGridOptions() {
		return {
			columns: 12,
			min_columns: 6
		};
	}
	get isAdmin() {
		return this.hassObj?.user?.is_admin !== !1;
	}
	async ws(e, n = {}) {
		if (!this.hassObj) throw new t("not connected to Home Assistant");
		let r = this.config.entry_id ? { entry_id: this.config.entry_id } : {};
		try {
			return await this.hassObj.callWS({
				type: `yarbie/${e}`,
				...r,
				...n
			});
		} catch (e) {
			let n = e;
			throw new t(n.message ?? String(e), n.code);
		}
	}
	async refreshWrites() {
		try {
			this.writes = await this.ws("info");
		} catch {
			this.writes = null;
		}
		this.editing() ? this.updateRails() : this.render();
	}
	refusal() {
		let e = this.writes;
		return e ? e.connected ? e.writable ? e.busy ? "The robot is running or paused in a plan. Stop or finish it first." : null : `Read-only: ${e.read_only_reason ?? "writes are off"}.` : "Not connected to the robot." : "No answer from yarbie.";
	}
	updateRails() {
		let e = this.refusal(), t = this.shadowRoot.querySelector(".rails");
		t && (t.textContent = e ?? "", t.hidden = !e), this.shadowRoot.querySelectorAll("button[data-write]").forEach((t) => t.disabled = !!e);
	}
	say(e, t = "") {
		this.message = {
			text: e,
			kind: t
		};
		let n = this.shadowRoot.querySelector(".message");
		n && (n.textContent = e, n.className = `message ${t}`);
	}
	frame(t, n) {
		let r = this.refusal();
		return `<style>${i}</style>
      <ha-card header="${e(this.config.title ?? t)}">
        <div class="card-content">
          <p class="rails" ${r ? "" : "hidden"}>${e(r ?? "")}</p>
          ${n}
          <p class="message ${this.message.kind}">${e(this.message.text)}</p>
        </div>
      </ha-card>`;
	}
	navigate(e) {
		history.pushState(null, "", e), window.dispatchEvent(new CustomEvent("location-changed", { detail: { replace: !1 } }));
	}
};
function r(e) {
	return e instanceof t && e.code === "unauthorized" ? "Only administrators can change this." : e instanceof Error ? e.message : String(e);
}
var i = "\n  :host { display: block; }\n  .card-content { padding: 0 16px 16px; }\n  ul.list { list-style: none; margin: 0; padding: 0; }\n  ul.list > li { display: flex; justify-content: space-between; align-items: flex-start; gap: 8px;\n    padding: 8px 0; border-bottom: 1px solid var(--divider-color); }\n  ul.list > li:last-child { border-bottom: none; }\n  .meta, .hint { color: var(--secondary-text-color); font-size: 0.9em; }\n  .hint { margin: 6px 0; }\n  .rails { margin: 0 0 8px; padding: 8px; border-radius: 6px; font-size: 0.9em;\n    background: rgba(var(--rgb-warning-color, 255, 166, 0), 0.15); }\n  .message { margin: 8px 0 0; font-size: 0.9em; min-height: 1em; }\n  .message.error { color: var(--error-color); }\n  .message.ok { color: var(--success-color, var(--primary-color)); }\n  .row { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; margin-top: 8px; }\n  .buttons { display: flex; gap: 6px; flex-shrink: 0; }\n  button { font: inherit; font-size: 0.9em; padding: 4px 12px; border-radius: 16px; cursor: pointer;\n    border: 1px solid var(--divider-color); background: none; color: var(--primary-color); }\n  button.primary { background: var(--primary-color); color: var(--text-primary-color, #fff); border-color: transparent; }\n  button.danger { color: var(--error-color); }\n  button.confirm { background: var(--error-color); color: #fff; border-color: transparent; }\n  button.icon { padding: 2px 8px; }\n  button:disabled { opacity: 0.45; cursor: default; }\n  label.field { display: flex; flex-direction: column; gap: 4px; margin: 8px 0; font-size: 0.9em; }\n  label.check { display: flex; gap: 8px; align-items: center; margin: 4px 0; }\n  input[type=\"text\"], input[type=\"time\"], input[type=\"number\"], select {\n    font: inherit; padding: 6px 8px; border-radius: 4px; border: 1px solid var(--divider-color);\n    background: var(--card-background-color, transparent); color: var(--primary-text-color); }\n  fieldset { border: 1px solid var(--divider-color); border-radius: 6px; margin: 8px 0; padding: 4px 10px 8px; }\n  legend { font-size: 0.9em; color: var(--secondary-text-color); }\n  .form { border-top: 1px solid var(--divider-color); margin-top: 8px; padding-top: 4px; }\n  .order li { align-items: center; padding: 4px 0; }\n  .unconfirmed { color: var(--warning-color, #b58100); font-size: 0.85em; }\n  .days { display: flex; flex-wrap: wrap; gap: 4px 12px; }\n  a { color: var(--primary-color); cursor: pointer; }\n";
function a(e, t, n, r) {
	let i = () => {
		customElements.get(e) || customElements.define(e, class extends t {});
	};
	i();
	let a = 0, o = window.setInterval(() => {
		i(), ++a >= 60 && window.clearInterval(o);
	}, 500);
	window.customCards ??= [], window.customCards.some((t) => t.type === e) || window.customCards.push({
		type: e,
		name: n,
		description: r
	});
}
a("yarbie-plans-card", class extends n {
	#e = null;
	#t = [];
	#n = null;
	#r = null;
	#i = !1;
	static getStubConfig() {
		return { type: "custom:yarbie-plans-card" };
	}
	editing() {
		return this.#n !== null;
	}
	async load() {
		if (!this.isAdmin) this.#r = "Only administrators can manage plans. The plan select still picks which one Start runs.", this.render();
		else {
			try {
				let [e, t] = await Promise.all([this.ws("plans/list"), this.ws("areas/list")]);
				this.#e = e, this.#t = t, this.#r = null;
			} catch (e) {
				this.#r = r(e);
			}
			await this.refreshWrites();
		}
	}
	#a(e) {
		return this.#t.find((t) => t.id === e)?.name ?? `Area ${e}`;
	}
	render() {
		let t = this.shadowRoot, n;
		if (this.#r) n = `<p class="hint">${e(this.#r)}</p>`;
		else if (this.#e === null) n = "<p class=\"hint\">Reading the plans…</p>";
		else {
			let t = this.refusal(), r = !!this.writes?.connected && !this.writes?.busy;
			n = `<ul class="list">${this.#e.length ? this.#e.map((t) => `<li>
                <div><b>${e(t.name)}</b><div class="meta">${t.area_ids.map((t) => e(this.#a(t))).join(" → ")}</div></div>
                <span class="buttons">
                  <button data-start="${t.id}" ${r && !this.#i ? "" : "disabled"} title="Start this plan now">Start</button>
                  <button data-edit="${t.id}" ${this.#n ? "disabled" : ""}>Edit</button>
                </span>
              </li>`).join("") : "<li class=\"meta\">No plans yet.</li>"}</ul>
        ${this.#n ? this.#o() : `<div class="row"><button data-new data-write ${t ? "disabled" : ""}>New plan</button>
          <a data-editor class="hint">Preview a route in the Yard editor</a></div>`}`;
		}
		t.innerHTML = this.frame("Plans", n), this.#s();
	}
	#o() {
		let t = this.#n, n = this.refusal(), r = new Set(t.area_ids);
		return `<div class="form">
      <h3>${t.id === null ? "New plan" : "Edit plan"}</h3>
      <label class="field">Name<input type="text" id="name" value="${e(t.name)}" /></label>
      <fieldset><legend>Areas</legend>
        ${this.#t.map((t) => `<label class="check"><input type="checkbox" data-area="${t.id}" ${r.has(t.id) ? "checked" : ""}/> ${e(t.name)}</label>`).join("") || "<p class=\"hint\">The robot has no areas.</p>"}
      </fieldset>
      ${t.area_ids.length > 1 ? `<fieldset><legend>Order</legend><ul class="list order">${t.area_ids.map((n, r) => `<li><span>${r + 1}. ${e(this.#a(n))}</span><span class="buttons">
                  <button class="icon" data-up="${r}" ${r === 0 ? "disabled" : ""} title="Earlier">▲</button>
                  <button class="icon" data-down="${r}" ${r === t.area_ids.length - 1 ? "disabled" : ""} title="Later">▼</button></span></li>`).join("")}</ul>
            <p class="hint">Sent in this order. Like the app, yarbie lets the robot order the areas itself
              (<code>enable_self_order</code>), so it may choose another.</p></fieldset>` : ""}
      <div class="row">
        <button class="primary" data-save data-write ${n || this.#i ? "disabled" : ""}>Save to robot</button>
        ${t.id === null ? "" : `<button class="danger" data-delete data-write ${n || this.#i ? "disabled" : ""}>Delete</button>`}
        <button data-cancel>Cancel</button>
      </div>
      ${t.id === null ? "" : "<p class=\"hint\">Deleting a plan also deletes its schedules (the robot does that).</p>"}
    </div>`;
	}
	#s() {
		let e = this.shadowRoot, t = (t, n) => e.querySelectorAll(t).forEach((e) => e.addEventListener("click", () => n(e)));
		if (t("button[data-start]", (e) => void this.#f(Number(e.dataset.start))), t("button[data-edit]", (e) => {
			let t = this.#e?.find((t) => t.id === Number(e.dataset.edit));
			t && this.#c({
				id: t.id,
				name: t.name,
				area_ids: [...t.area_ids]
			});
		}), t("button[data-new]", () => this.#c({
			id: null,
			name: "",
			area_ids: []
		})), t("a[data-editor]", () => this.navigate("/yard-editor")), !this.#n) return;
		let n = this.#n;
		e.querySelector("#name")?.addEventListener("input", (e) => n.name = e.target.value), e.querySelectorAll("input[data-area]").forEach((e) => e.addEventListener("change", () => {
			let t = Number(e.dataset.area);
			n.area_ids = e.checked ? [...n.area_ids.filter((e) => e !== t), t] : n.area_ids.filter((e) => e !== t), this.render();
		}));
		let r = (e, t) => {
			let r = n.area_ids;
			[r[e], r[e + t]] = [r[e + t], r[e]], this.render();
		};
		t("button[data-up]", (e) => r(Number(e.dataset.up), -1)), t("button[data-down]", (e) => r(Number(e.dataset.down), 1)), t("button[data-cancel]", () => {
			this.#n = null, this.say(""), this.load();
		}), t("button[data-save]", () => void this.#l()), t("button[data-delete]", (e) => {
			e.classList.contains("confirm") ? this.#u() : (e.classList.add("confirm"), e.textContent = "Confirm delete");
		});
	}
	#c(e) {
		this.#n = e, this.say(""), this.render(), this.shadowRoot.querySelector("#name")?.focus();
	}
	async #l() {
		let e = this.#n;
		if (!e) return;
		let t = e.name.trim();
		if (!t || !e.area_ids.length) return this.say("A plan needs a name and at least one area.", "error");
		await this.#d(async () => (await this.ws("plans/save", {
			name: t,
			area_ids: e.area_ids,
			...e.id === null ? {} : { plan_id: e.id }
		}), "Plan saved."));
	}
	async #u() {
		let e = this.#n;
		e && e.id !== null && await this.#d(async () => (await this.ws("plans/delete", { plan_id: e.id }), "Plan deleted."));
	}
	async #d(e) {
		this.#i = !0, this.render();
		try {
			let t = await e();
			this.#n = null, this.message = {
				text: t,
				kind: "ok"
			};
		} catch (e) {
			this.message = {
				text: r(e),
				kind: "error"
			};
		}
		this.#i = !1, await this.load();
	}
	async #f(e) {
		let t = this.#e?.find((t) => t.id === e);
		this.#i = !0, this.render();
		try {
			await this.hassObj.callService("yarbie", "start_plan", {
				plan_id: e,
				...this.config.entry_id ? { config_entry_id: this.config.entry_id } : {}
			}), this.message = {
				text: `Started ${t?.name ?? "the plan"}.`,
				kind: "ok"
			};
		} catch (e) {
			this.message = {
				text: r(e),
				kind: "error"
			};
		}
		this.#i = !1, await this.refreshWrites();
	}
}, "Yarbo plans", "The robot's plans: create, edit (areas and their order), delete and start them.");
//#endregion
