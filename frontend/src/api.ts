// Typed calls to the yarbie integration over Home Assistant's own websocket connection.
// Geometry is GeoJSON in WGS84 throughout.

export type Family =
  | "areas"
  | "nogozones"
  | "novisionzones"
  | "elec_fence"
  | "pathways"
  | "sidewalks"
  | "deadends"
  | "dock";

export interface MapFeature {
  type: "Feature";
  id: string;
  geometry: GeoJSON.Polygon | GeoJSON.LineString | GeoJSON.Point;
  properties: {
    family: Family;
    id: number | null;
    name: string;
    enabled?: boolean;
    algorithm_type?: number | null;
    area_m2?: number | null;
    start_id?: number | null;
    end_id?: number | null;
  };
}

export interface MapCollection {
  type: "FeatureCollection";
  features: MapFeature[];
}

export interface MapResponse {
  revision: string;
  reference: [number, number] | null;
  features: MapCollection;
}

export interface Status {
  source: "sim" | "lan" | "cloud";
  writable: boolean;
  connected: boolean;
  activity?: string | null;
  battery?: number | null;
  busy?: boolean;
  firmware?: string | null;
  position?: { lon: number; lat: number; phi: number } | null;
  error?: string;
  smoothing?: Smoothing;
}

/** The smoothing tolerance from the integration options, and the unit to show it in
 * (inches when Home Assistant uses US customary units, else centimetres). */
export interface Smoothing {
  tolerance_m: number;
  unit: "in" | "cm";
  unit_m: number;
  limits: [number, number]; // what the options form accepts, in ``unit``
}

export interface Smoothed {
  id: string;
  geometry: GeoJSON.Polygon;
  stats: {
    before: number;
    after: number;
    area_before: number;
    area_after: number;
    max_offset: number;
    arcs: number;
    circle: boolean;
    pinned: number;
    notes: string[];
  };
}

export interface Op {
  kind: "create" | "update" | "delete";
  family: Family;
  key: string;
  id: number | null;
  name: string;
  supported: boolean;
  reason: string | null;
  warnings: { text: string; points: [number, number][] }[]; // points as [lon, lat]
  changes: string[];
}

export interface ApplyResult {
  applied: Op[];
  skipped: Op[];
  error?: string | null;
  backup: string | null;
  map?: MapResponse | null;
  reload?: string; // sent, but the map could not be read back
}

/** A photo source for the map background. */
export interface Imagery {
  id: string;
  label: string;
  tiles: string;
  maxzoom: number; // the deepest level the source has; the map stretches it beyond
  attribution: string;
  covers?: [number, number, number, number]; // west, south, east, north; omitted = world
}

/** Estimated wear: [lon, lat, passes] per grid cell, from the robot's run history. */
export interface Heatmap {
  runs: number;
  first: number | null;
  last: number | null;
  trips: number;
  max: number;
  cell_m: number;
  cells: [number, number, number][];
  pathways: { id: number; name: string; passes: number }[];
  areas: { id: number; name: string; fills: number }[];
}

/** The run in progress (or the last one): each area's route, mowed part and the rest. */
export interface Progress {
  running: boolean;
  state?: number | null;
  plan: { id: number | null; name: string | undefined } | null;
  current_area?: number | null;
  progress?: number | null;
  remaining_s?: number | null;
  total_s?: number | null;
  duration_s?: number | null;
  started?: number | null;
  area_ids?: number[];
  paths?: { area_id: number | null; kind: "fill" | "edge"; done: number[][]; left: number[][]; fraction: number }[];
}

export interface Plan {
  id: number;
  name: string;
  area_ids: number[];
  self_order: boolean | null;
}

export interface RouteCollection {
  type: "FeatureCollection";
  features: GeoJSON.Feature<GeoJSON.LineString, { area_id: number | null; kind: "fill" | "edge" }>[];
  total_s: number | null;
}

/** One run: the robot's record joined with yarbie's own log where it watched. */
export interface Run {
  key: string;
  plan_id: number | null;
  plan: string | null;
  area_ids: number[];
  started: number;
  ended: number | null;
  work_s: number | null;
  progress: number | null;
  mowed_m2: number | null;
  total_m2: number | null;
  charge_trips: number | null;
  battery_used: number | null;
  outcome: string;
  source: ("robot" | "yarbie")[];
  reason?: string | null;
  started_by?: string | null;
  faults?: number[];
  rain?: boolean;
  events?: { at: number; kind: string; reason?: string; fault_code?: number; fault?: string; progress?: number }[];
  segments?: { start: number; end: number | null; battery_start: number | null; battery_end: number | null }[];
  area_states?: Record<string, "done" | "edge" | "left">;
}

/** One work area in the Area settings table: its pattern and its mowing settings (null
 * when the robot would not give them). */
export interface AreaSettings {
  id: number;
  name: string;
  algorithm_type: number | null;
  params: Record<string, unknown> | null;
}

/** What to change on one area; ``params`` is a partial record, nested values merged. */
export interface AreaChange {
  area_id: number;
  algorithm_type?: number;
  params?: Record<string, unknown>;
}

export interface AreaSettingsResult {
  // ok: something was sent; skipped: there was nothing to send; error: it was refused.
  results: { area_id: number; name: string; ok: boolean; error: string | null; skipped: string | null }[];
  backup: string;
  areas: AreaSettings[] | null; // the areas sent to, read again; null if that read failed
  read_error: string | null;
}

/** What the panel needs from the ``hass`` object Home Assistant hands it. */
export interface Hass {
  callWS<T>(msg: Record<string, unknown>): Promise<T>;
  callService(domain: string, service: string, data?: Record<string, unknown>): Promise<unknown>;
  connection: {
    subscribeMessage<T>(cb: (event: T) => void, msg: Record<string, unknown>): Promise<() => Promise<void>>;
  };
}

let hass: Hass | null = null;

export function setHass(next: Hass) {
  hass = next;
}

// The integration's error codes, as the HTTP statuses the page used to see.
const STATUS: Record<string, number> = {
  conflict: 409,
  read_only: 403,
  invalid: 400,
  not_found: 404,
  robot: 502,
  unauthorized: 401,
};

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
    readonly code?: string,
  ) {
    super(message);
  }
}

async function call<T>(type: string, data: Record<string, unknown> = {}): Promise<T> {
  if (!hass) throw new ApiError(503, "not connected to Home Assistant");
  try {
    // The reply is built in HA's window, outside this iframe. MapLibre's worker only
    // takes plain objects of its own window, so copy it over.
    return structuredClone(await hass.callWS<T>({ type: `yarbie/${type}`, ...data }));
  } catch (err) {
    const e = err as { code?: string; message?: string };
    throw new ApiError(STATUS[e.code ?? ""] ?? 500, e.message ?? String(err), e.code);
  }
}

interface Info {
  device_id: string | null;
  robots: number;
  source: Status["source"];
  connected: boolean;
  writable: boolean;
  read_only_reason: string | null;
  busy: boolean;
  firmware: string | null;
  activity: string | null;
  battery: number | null;
  position: Status["position"];
  smoothing: Smoothing;
}

/** The Yarbo dashboard's path (the one built by the yarbie strategy), if one was added. */
async function yarboDashboard(): Promise<string | null> {
  if (!hass) return null;
  const boards = await hass.callWS<{ url_path: string; mode: string }[]>({ type: "lovelace/dashboards/list" });
  for (const board of boards.filter((b) => b.mode === "storage")) {
    try {
      const config = await hass.callWS<{ strategy?: { type?: string } }>({ type: "lovelace/config", url_path: board.url_path });
      if (config.strategy?.type === "custom:yarbie") return `/${board.url_path}`;
    } catch {
      // an empty or broken dashboard: not ours
    }
  }
  return null;
}

export const api = {
  status: async (): Promise<Status> => {
    const info = await call<Info>("info");
    return {
      source: info.source,
      writable: info.writable,
      connected: info.connected,
      activity: info.activity,
      battery: info.battery,
      busy: info.busy,
      firmware: info.firmware,
      position: info.position,
      error: info.read_only_reason ?? undefined,
      smoothing: info.smoothing,
    };
  },
  info: () => call<Info>("info"),
  map: () => call<MapResponse>("map"),
  preview: (features: MapCollection) =>
    call<{ ops: Op[]; writable: boolean }>("preview", { features }),
  /** Proposed outlines for ``ids``, worked out from the whole edited map (pathway ends and
   * neighbouring areas are kept). Nothing is sent to the robot. */
  smooth: (features: MapCollection, ids: string[], tolerance_m: number, arcs: boolean, keep_shared: boolean) =>
    call<{ tolerance_m: number; results: Smoothed[] }>("smooth", { features, ids, tolerance_m, arcs, keep_shared }),
  apply: (features: MapCollection, revision: string) =>
    call<ApplyResult>("apply", { features, revision }),
  imagery: () => call<{ sources: Imagery[]; error?: string; hint?: string }>("imagery"),
  backups: () => call<{ name: string; bytes: number; kind: "map" | "area_settings" }[]>("backups/list"),
  backup: (name: string) => call<{ features: MapCollection }>("backups/get", { name }),
  dashboard: yarboDashboard,
  log: (kind: string, detail: unknown) =>
    hass?.callService("system_log", "write", {
      logger: "custom_components.yarbie.panel",
      level: "warning",
      message: `${kind}: ${JSON.stringify(detail).slice(0, 2000)}`,
    }),
};
