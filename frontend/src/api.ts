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

interface Job<T> {
  id: string;
  status: "running" | "done" | "error";
  result: T | null;
  error: string | null;
}

/** Start (or join) a job and wait for it to end. Following it again after a reconnect
 * picks up the same job; the robot is not asked twice. */
async function job<T>(start: string, data: Record<string, unknown>): Promise<T> {
  const first = await call<Job<T>>(start, data);
  const finish = (j: Job<T>): T => {
    if (j.status === "error") throw new ApiError(502, j.error ?? "the robot refused");
    return j.result as T;
  };
  if (first.status !== "running") return finish(first);
  return await new Promise<T>((resolve, reject) => {
    let unsubscribe: (() => Promise<void>) | null = null;
    let settled = false;
    const done = (event: Job<T>) => {
      const j = structuredClone(event);
      if (j.status === "running" || settled) return;
      settled = true;
      void unsubscribe?.();
      try {
        resolve(finish(j));
      } catch (err) {
        reject(err);
      }
    };
    hass!.connection
      .subscribeMessage<Job<T>>(done, { type: "yarbie/job/subscribe", job_id: first.id })
      .then((unsub) => {
        unsubscribe = unsub;
        if (settled) void unsub();
      })
      .catch(reject);
  });
}

interface Info {
  source: Status["source"];
  connected: boolean;
  writable: boolean;
  read_only_reason: string | null;
  busy: boolean;
  firmware: string | null;
  activity: string | null;
  battery: number | null;
  position: Status["position"];
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
    };
  },
  map: () => call<MapResponse>("map"),
  preview: (features: MapCollection) =>
    call<{ ops: Op[]; writable: boolean }>("preview", { features }),
  apply: (features: MapCollection, revision: string) =>
    call<ApplyResult>("apply", { features, revision }),
  imagery: () => call<{ sources: Imagery[]; error?: string; hint?: string }>("imagery"),
  backups: () => call<{ name: string; bytes: number }[]>("backups/list"),
  backup: (name: string) => call<{ features: MapCollection }>("backups/get", { name }),
  progress: () => call<Progress>("progress"),
  heatmap: (days: number, refresh: boolean) => call<Heatmap>("heatmap", { days, refresh }),
  plans: () => call<Plan[]>("plans/list"),
  createPlan: (name: string, area_ids: number[]) =>
    call<{ id: number }>("plans/save", { name, area_ids }),
  updatePlan: (id: number, name: string, area_ids: number[]) =>
    call<{ id: number }>("plans/save", { plan_id: id, name, area_ids }),
  deletePlan: (id: number) => call<{ deleted: number }>("plans/delete", { plan_id: id }),
  route: (id: number) => job<RouteCollection>("route/start", { plan_id: id }),
  mowerParams: (areaId: number) =>
    call<{ params: Record<string, unknown>; editable: string[] }>("area_params/get", {
      area_id: areaId,
    }),
  updateMowerParams: (areaId: number, changes: Record<string, unknown>) =>
    call<{ sent: Record<string, unknown> }>("area_params/save", { area_id: areaId, changes }),
  runs: (refresh: boolean, planId?: number) =>
    call<{ runs: Run[]; plans: { id: number; name: string }[]; count: number }>("runs/list", {
      refresh,
      ...(planId === undefined ? {} : { plan_id: planId }),
    }),
  run: (key: string) => call<Run>("runs/get", { key }),
  log: (kind: string, detail: unknown) =>
    hass?.callService("system_log", "write", {
      logger: "custom_components.yarbie.panel",
      level: "warning",
      message: `${kind}: ${JSON.stringify(detail).slice(0, 2000)}`,
    }),
};
