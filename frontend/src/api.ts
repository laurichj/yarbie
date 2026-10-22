// Typed calls to the yarbie backend. Geometry is GeoJSON in WGS84 throughout.

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
  map?: MapResponse;
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

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
  }
}

async function call<T>(method: string, path: string, body?: unknown): Promise<T> {
  const response = await fetch(path, {
    method,
    headers: body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!response.ok) {
    let message = response.statusText;
    try {
      const detail = (await response.json()).detail;
      message = typeof detail === "string" ? detail : JSON.stringify(detail);
    } catch {
      // keep the status text
    }
    throw new ApiError(response.status, message);
  }
  return (await response.json()) as T;
}

export const api = {
  status: () => call<Status>("GET", "/api/status"),
  map: () => call<MapResponse>("GET", "/api/map"),
  preview: (features: MapCollection) =>
    call<{ ops: Op[]; writable: boolean }>("POST", "/api/map/preview", features),
  apply: (features: MapCollection, revision: string) =>
    call<ApplyResult>("POST", "/api/map/apply", { features, revision }),
  imagery: () =>
    call<{ sources: Imagery[]; error?: string; hint?: string }>("GET", "/api/imagery"),
  backups: () => call<{ name: string; bytes: number }[]>("GET", "/api/backups"),
  backup: (name: string) =>
    call<{ features: MapCollection }>("GET", `/api/backups/${encodeURIComponent(name)}`),
  progress: () => call<Progress>("GET", "/api/progress"),
  heatmap: (days: number, refresh: boolean) =>
    call<Heatmap>("GET", `/api/heatmap?days=${days}&refresh=${refresh}`),
  plans: () => call<Plan[]>("GET", "/api/plans"),
  createPlan: (name: string, area_ids: number[]) =>
    call<{ id: number }>("POST", "/api/plans", { name, area_ids }),
  updatePlan: (id: number, name: string, area_ids: number[]) =>
    call<{ id: number }>("PUT", `/api/plans/${id}`, { name, area_ids }),
  deletePlan: (id: number) => call<{ deleted: number }>("DELETE", `/api/plans/${id}`),
  route: (id: number) => call<RouteCollection>("GET", `/api/plans/${id}/route`),
  mowerParams: (areaId: number) =>
    call<{ params: Record<string, unknown>; editable: string[] }>(
      "GET",
      `/api/areas/${areaId}/mower-params`,
    ),
  updateMowerParams: (areaId: number, changes: Record<string, unknown>) =>
    call<{ sent: Record<string, unknown> }>("PATCH", `/api/areas/${areaId}/mower-params`, changes),
};
