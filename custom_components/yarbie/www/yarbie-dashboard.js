//#region src/dashboard.ts
var e = "yarbie", t = "/yard-editor";
function n(t, n) {
	let r = /* @__PURE__ */ new Map();
	for (let i of Object.values(t.entities)) i.platform !== e || i.device_id !== n || i.hidden || i.translation_key && i.entity_id in t.states && r.set(i.translation_key, i.entity_id);
	return r;
}
function r(e, t, n) {
	let r = e.states[t]?.attributes?.friendly_name;
	if (!r || !r.startsWith(`${n} `)) return;
	let i = r.slice(n.length + 1);
	return i.charAt(0).toUpperCase() + i.slice(1);
}
function i(e, t, n, i) {
	return i.flatMap((i) => {
		let a = t.get(i);
		if (!a) return [];
		let o = {
			type: "tile",
			entity: a
		}, s = r(e, a, n);
		return s && (o.name = s), a.startsWith("button.") && (o.hide_state = !0), [o];
	});
}
function a(e, a, o) {
	let s = n(e, a), c = e.devices[a], l = c?.name_by_user || c?.name || "Yarbo", u = s.get("mower"), d = s.get("plan_select"), f = s.get("map"), p = [{
		type: "heading",
		heading: l,
		heading_style: "title"
	}];
	u && p.push({
		type: "tile",
		entity: u,
		vertical: !1,
		features: [{
			type: "lawn-mower-commands",
			commands: ["start_pause", "dock"]
		}],
		features_position: "bottom",
		grid_options: { columns: 12 }
	}), d && p.push({
		type: "tile",
		entity: d,
		name: r(e, d, l) ?? "Plan",
		features: [{ type: "select-options" }],
		features_position: "bottom",
		grid_options: { columns: 12 }
	}), p.push(...i(e, s, l, [
		"battery",
		"activity",
		"plan_progress",
		"time_remaining",
		"error",
		"rain"
	]));
	let m = c?.primary_config_entry ?? c?.config_entries?.[0], h = {
		...m ? { entry_id: m } : {},
		...u ? { mower: u } : {}
	};
	p.push({
		type: "custom:yarbie-plans-card",
		...h,
		...d ? { entity: d } : {},
		grid_options: { columns: 12 }
	});
	let g = [{
		type: "heading",
		heading: "Yard",
		heading_style: "title"
	}];
	f && g.push({
		type: "picture-entity",
		entity: f,
		show_name: !1,
		show_state: !1,
		tap_action: {
			action: "navigate",
			navigation_path: t
		},
		grid_options: { columns: 12 }
	}), g.push({
		type: "button",
		name: "Open Yard editor",
		icon: "mdi:map-marker-path",
		show_state: !1,
		tap_action: {
			action: "navigate",
			navigation_path: t
		},
		grid_options: {
			columns: 12,
			rows: 1
		}
	}), g.push({
		type: "heading",
		heading: "Controls",
		heading_style: "subtitle"
	}, ...i(e, s, l, [
		"beep",
		"stop",
		"lights",
		"refresh_map"
	]));
	let _ = [
		u,
		s.get("plan_event"),
		s.get("error"),
		s.get("charging")
	].filter((e) => !!e), v = [{
		type: "heading",
		heading: "Recent",
		heading_style: "title"
	}];
	_.length && v.push({
		type: "logbook",
		target: { entity_id: _ },
		hours_to_show: 48,
		grid_options: { columns: 12 }
	});
	let y = s.get("battery");
	return y && v.push({
		type: "history-graph",
		title: "Battery",
		hours_to_show: 48,
		entities: [{ entity: y }],
		grid_options: { columns: 12 }
	}), {
		title: o > 1 ? l : "Yard",
		path: o > 1 ? a : "yard",
		icon: "mdi:robot-mower",
		type: "sections",
		max_columns: 3,
		sections: [
			{
				type: "grid",
				cards: p
			},
			{
				type: "grid",
				cards: g
			},
			{
				type: "grid",
				cards: v
			}
		]
	};
}
var o = class extends HTMLElement {
	static async generate(t, n) {
		let r = [...new Set(Object.values(n.entities).filter((t) => t.platform === e && t.device_id).map((e) => e.device_id))];
		return r.length ? {
			title: "Yarbo",
			views: r.map((e) => a(n, e, r.length))
		} : {
			title: "Yarbo",
			views: [{
				title: "Yard",
				cards: [{
					type: "markdown",
					content: "No Yarbo is set up yet. Add one under **Settings > Devices & services > yarbie**."
				}]
			}]
		};
	}
}, s = "ll-strategy-dashboard-yarbie", c = () => {
	customElements.get(s) || customElements.define(s, class extends o {});
};
c();
var l = 0, u = window.setInterval(() => {
	c(), ++l >= 60 && window.clearInterval(u);
}, 500), d = window;
d.customStrategies ??= [], d.customStrategies.some((e) => e.type === "yarbie" && e.strategyType === "dashboard") || d.customStrategies.push({
	type: "yarbie",
	strategyType: "dashboard",
	name: "Yarbo",
	description: "The robot, its map and recent runs, laid out for you."
});
//#endregion
