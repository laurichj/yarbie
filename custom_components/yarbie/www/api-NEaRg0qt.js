//#region src/api.ts
var e = null;
function t(t) {
	e = t;
}
var n = {
	conflict: 409,
	read_only: 403,
	invalid: 400,
	not_found: 404,
	robot: 502,
	unauthorized: 401
}, r = class extends Error {
	status;
	code;
	constructor(e, t, n) {
		super(t), this.status = e, this.code = n;
	}
};
async function i(t, i = {}) {
	if (!e) throw new r(503, "not connected to Home Assistant");
	try {
		return structuredClone(await e.callWS({
			type: `yarbie/${t}`,
			...i
		}));
	} catch (e) {
		let t = e;
		throw new r(n[t.code ?? ""] ?? 500, t.message ?? String(e), t.code);
	}
}
async function a(t, n) {
	let a = await i(t, n), o = (e) => {
		if (e.status === "error") throw new r(502, e.error ?? "the robot refused");
		return e.result;
	};
	return a.status === "running" ? await new Promise((t, n) => {
		let r = null, i = !1;
		e.connection.subscribeMessage((e) => {
			let a = structuredClone(e);
			if (!(a.status === "running" || i)) {
				i = !0, r?.();
				try {
					t(o(a));
				} catch (e) {
					n(e);
				}
			}
		}, {
			type: "yarbie/job/subscribe",
			job_id: a.id
		}).then((e) => {
			r = e, i && e();
		}).catch(n);
	}) : o(a);
}
var o = {
	status: async () => {
		let e = await i("info");
		return {
			source: e.source,
			writable: e.writable,
			connected: e.connected,
			activity: e.activity,
			battery: e.battery,
			busy: e.busy,
			firmware: e.firmware,
			position: e.position,
			error: e.read_only_reason ?? void 0
		};
	},
	map: () => i("map"),
	preview: (e) => i("preview", { features: e }),
	apply: (e, t) => i("apply", {
		features: e,
		revision: t
	}),
	imagery: () => i("imagery"),
	backups: () => i("backups/list"),
	backup: (e) => i("backups/get", { name: e }),
	progress: () => i("progress"),
	heatmap: (e, t) => i("heatmap", {
		days: e,
		refresh: t
	}),
	plans: () => i("plans/list"),
	createPlan: (e, t) => i("plans/save", {
		name: e,
		area_ids: t
	}),
	updatePlan: (e, t, n) => i("plans/save", {
		plan_id: e,
		name: t,
		area_ids: n
	}),
	deletePlan: (e) => i("plans/delete", { plan_id: e }),
	route: (e) => a("route/start", { plan_id: e }),
	mowerParams: (e) => i("area_params/get", { area_id: e }),
	updateMowerParams: (e, t) => i("area_params/save", {
		area_id: e,
		changes: t
	}),
	log: (t, n) => e?.callService("system_log", "write", {
		logger: "custom_components.yarbie.panel",
		level: "warning",
		message: `${t}: ${JSON.stringify(n).slice(0, 2e3)}`
	})
};
//#endregion
export { o as n, t as r, r as t };
