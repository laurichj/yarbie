// A ready-made Yarbo dashboard: Settings > Dashboards > Add dashboard > Yarbo.
//
// A strategy, not a saved layout: it is rebuilt from the robot's entities each time it
// opens, so renamed entities and a second robot need no editing. One view per robot,
// in three balanced columns: the mower and its controls, the map, recent activity.
// Loaded on every page (HA's extra modules), so it imports nothing.

interface EntityEntry {
  entity_id: string;
  device_id?: string | null;
  platform?: string;
  translation_key?: string | null;
  hidden?: boolean;
}

interface Hass {
  entities: Record<string, EntityEntry>;
  devices: Record<
    string,
    {
      name?: string | null;
      name_by_user?: string | null;
      config_entries?: string[];
      primary_config_entry?: string | null;
    }
  >;
  states: Record<string, unknown>;
}

type Card = Record<string, unknown>;

const PLATFORM = "yarbie";
const EDITOR = "/yard-editor";

/** The robot's entities by what they are (their translation key), skipping hidden and
 * disabled ones (disabled entities have no state). */
function byKey(hass: Hass, deviceId: string): Map<string, string> {
  const out = new Map<string, string>();
  for (const entry of Object.values(hass.entities)) {
    if (entry.platform !== PLATFORM || entry.device_id !== deviceId || entry.hidden) continue;
    if (!entry.translation_key || !(entry.entity_id in hass.states)) continue;
    out.set(entry.translation_key, entry.entity_id);
  }
  return out;
}

/** The entity's name without the robot's, which the column heading already gives. */
function shortName(hass: Hass, entity: string, device: string): string | undefined {
  const full = (hass.states[entity] as { attributes?: { friendly_name?: string } } | undefined)?.attributes
    ?.friendly_name;
  if (!full || !full.startsWith(`${device} `)) return undefined;
  const rest = full.slice(device.length + 1);
  return rest.charAt(0).toUpperCase() + rest.slice(1);
}

function tiles(hass: Hass, found: Map<string, string>, device: string, keys: string[]): Card[] {
  return keys.flatMap((key) => {
    const entity = found.get(key);
    if (!entity) return [];
    const card: Card = { type: "tile", entity };
    const name = shortName(hass, entity, device);
    if (name) card.name = name;
    // A button's state is only when it was last pressed.
    if (entity.startsWith("button.")) card.hide_state = true;
    return [card];
  });
}

function view(hass: Hass, deviceId: string, robots: number): Card {
  const found = byKey(hass, deviceId);
  const device = hass.devices[deviceId];
  const name = device?.name_by_user || device?.name || "Yarbo";
  const mower = found.get("mower");
  const plan = found.get("plan_select");
  const map = found.get("map");

  const controls: Card[] = [{ type: "heading", heading: name, heading_style: "title" }];
  if (mower) {
    controls.push({
      type: "tile",
      entity: mower,
      vertical: false,
      features: [{ type: "lawn-mower-commands", commands: ["start_pause", "dock"] }],
      features_position: "bottom",
      grid_options: { columns: 12 },
    });
  }
  if (plan) {
    controls.push({
      type: "tile",
      entity: plan,
      name: shortName(hass, plan, name) ?? "Plan",
      features: [{ type: "select-options" }],
      features_position: "bottom",
      grid_options: { columns: 12 },
    });
  }
  controls.push(...tiles(hass, found, name, ["battery", "activity", "plan_progress", "time_remaining", "error", "rain"]));
  // Plans are edited here, beside the plan select (cards.ts). The select's options change
  // when the plans do, so the card watches it.
  const entry = device?.primary_config_entry ?? device?.config_entries?.[0];
  const robot = { ...(entry ? { entry_id: entry } : {}), ...(mower ? { mower } : {}) };
  controls.push({
    type: "custom:yarbie-plans-card",
    ...robot,
    ...(plan ? { entity: plan } : {}),
    grid_options: { columns: 12 },
  });

  const yard: Card[] = [{ type: "heading", heading: "Yard", heading_style: "title" }];
  if (map) {
    yard.push({
      type: "picture-entity",
      entity: map,
      show_name: false,
      show_state: false,
      tap_action: { action: "navigate", navigation_path: EDITOR },
      grid_options: { columns: 12 },
    });
  }
  yard.push({
    type: "button",
    name: "Open Yard editor",
    icon: "mdi:map-marker-path",
    show_state: false,
    tap_action: { action: "navigate", navigation_path: EDITOR },
    grid_options: { columns: 12, rows: 1 },
  });
  // Under the map rather than in the first column, so the columns come out even.
  yard.push(
    { type: "heading", heading: "Controls", heading_style: "subtitle" },
    ...tiles(hass, found, name, ["beep", "stop", "lights", "refresh_map"]),
  );
  // Schedules beside the plans' column, which is long enough already. The calendar's next
  // event changes when they do.
  const calendar = found.get("schedules");
  yard.push({
    type: "custom:yarbie-schedules-card",
    ...robot,
    ...(calendar ? { entity: calendar } : {}),
    grid_options: { columns: 12 },
  });

  const watched = [mower, found.get("plan_event"), found.get("error"), found.get("charging")].filter(
    (e): e is string => Boolean(e),
  );
  const recent: Card[] = [{ type: "heading", heading: "Recent", heading_style: "title" }];
  if (watched.length) {
    recent.push({ type: "logbook", target: { entity_id: watched }, hours_to_show: 48, grid_options: { columns: 12 } });
  }
  const battery = found.get("battery");
  if (battery) {
    recent.push({
      type: "history-graph",
      title: "Battery",
      hours_to_show: 48,
      entities: [{ entity: battery }],
      grid_options: { columns: 12 },
    });
  }

  return {
    title: robots > 1 ? name : "Yard",
    path: robots > 1 ? deviceId : "yard",
    icon: "mdi:robot-mower",
    type: "sections",
    max_columns: 3,
    sections: [
      { type: "grid", cards: controls },
      { type: "grid", cards: yard },
      { type: "grid", cards: recent },
    ],
  };
}

class YarbieDashboardStrategy extends HTMLElement {
  static async generate(_config: Card, hass: Hass): Promise<Card> {
    const devices = [
      ...new Set(
        Object.values(hass.entities)
          .filter((e) => e.platform === PLATFORM && e.device_id)
          .map((e) => e.device_id as string),
      ),
    ];
    if (!devices.length) {
      return {
        title: "Yarbo",
        views: [
          {
            title: "Yard",
            cards: [
              {
                type: "markdown",
                content: "No Yarbo is set up yet. Add one under **Settings > Devices & services > yarbie**.",
              },
            ],
          },
        ],
      };
    }
    return { title: "Yarbo", views: devices.map((id) => view(hass, id, devices.length)) };
  }
}

// HA loads this before its own code, which then swaps in a polyfilled element registry
// that forgets what was defined earlier. So define it again until it sticks there.
const TAG = "ll-strategy-dashboard-yarbie";
const define = () => {
  if (!customElements.get(TAG)) customElements.define(TAG, class extends YarbieDashboardStrategy {});
};
define();
let checks = 0;
const keep = window.setInterval(() => {
  define();
  if (++checks >= 60) window.clearInterval(keep);
}, 500);

const w = window as unknown as { customStrategies?: Record<string, unknown>[] };
w.customStrategies ??= [];
if (!w.customStrategies.some((s) => s.type === "yarbie" && s.strategyType === "dashboard")) {
  w.customStrategies.push({
    type: "yarbie",
    strategyType: "dashboard",
    name: "Yarbo",
    description: "The robot, its map and recent runs, laid out for you.",
  });
}
