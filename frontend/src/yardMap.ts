// The yard on a MapLibre map, shared by the Yard editor and the dashboard's yard card:
// the photo sources and the alignment chosen for each, and the layers both draw (map
// shapes, labels, the robot, a run, a route, the wear heat map). Only the cards' yard
// map chunk and the editor import it, so MapLibre never loads with the cards bundle.

import * as maplibregl from "maplibre-gl";
import type { GeoJSONSource } from "maplibre-gl";
// Bundle MapLibre's worker (it renders GeoJSON) so it is served with the app.
import workerUrl from "maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url";
import type { Family, Heatmap, Imagery, MapFeature, Progress } from "./api";
import { metresToOffset, shiftGeometry, type Offset } from "./geo";

export const COLORS: Record<Family, `#${string}`> = {
  areas: "#3fb950",
  nogozones: "#f85149",
  novisionzones: "#a371f7",
  elec_fence: "#db61a2",
  pathways: "#e3b341",
  sidewalks: "#58a6ff",
  deadends: "#8b949e",
  dock: "#ffffff",
};

const ESRI = "Imagery © Esri, Maxar, Earthstar Geographics, and the GIS User Community";
export const IMAGERY: Imagery[] = [
  {
    id: "esri",
    label: "Esri World Imagery",
    tiles: "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
    maxzoom: 19,
    attribution: ESRI,
  },
  {
    id: "esri-clarity",
    label: "Esri Clarity (other capture)",
    tiles: "https://clarity.maptiles.arcgis.com/arcgis/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
    maxzoom: 19,
    attribution: ESRI,
  },
  {
    // 3-inch county orthophotos, rendered on request in web mercator. Outside the
    // county the layer is transparent and Esri shows through.
    id: "snoco-2024",
    label: "Snohomish County 2024 (3 in)",
    tiles:
      "https://gis.snoco.org/img/rest/services/Imagery/Aerial_2024/ImageServer/exportImage" +
      // 512 px for each 256 px tile, so Retina screens are not stretching the photo.
      "?bbox={bbox-epsg-3857}&bboxSR=3857&imageSR=3857&size=512,512&format=jpgpng&f=image",
    maxzoom: 22,
    attribution: "Imagery © Snohomish County",
    covers: [-122.47, 47.77, -120.9, 48.3], // Snohomish County, WA
  },
];
// Listed best first: with no choice saved, the first that covers the yard is used.
const BEST_FIRST = ["google", "snoco-2024", "esri", "esri-clarity"];

export const empty: GeoJSON.FeatureCollection = { type: "FeatureCollection", features: [] };
const CUT_WIDTH_M = 0.5; // drawn width of a mowed row

// -- the map and its photo

/** A map of the yard with every built-in photo source, Esri showing until one is chosen. */
export function createMap(container: HTMLElement): maplibregl.Map {
  maplibregl.setWorkerUrl(workerUrl);
  const map = new maplibregl.Map({
    container,
    style: {
      version: 8,
      glyphs: "https://demotiles.maplibre.org/font/{fontstack}/{range}.pbf",
      sources: Object.fromEntries(IMAGERY.map((i) => [i.id, rasterSource(i)])),
      // Esri stays underneath whatever is chosen, to fill gaps in local imagery.
      layers: IMAGERY.map((i) => ({
        id: `imagery-${i.id}`,
        type: "raster" as const,
        source: i.id,
        layout: { visibility: i.id === "esri" ? ("visible" as const) : ("none" as const) },
      })),
    },
    center: [0, 20],
    zoom: 2,
    maxZoom: 24, // the highest MapLibre supports
  });
  map.addControl(new maplibregl.NavigationControl({ visualizePitch: false }), "top-right");
  map.addControl(new maplibregl.ScaleControl({ unit: "metric" }), "bottom-right");
  return map;
}

function rasterSource(i: Imagery): maplibregl.RasterSourceSpecification {
  return {
    type: "raster",
    tiles: [i.tiles],
    tileSize: 256,
    maxzoom: i.maxzoom,
    attribution: i.attribution,
    ...(i.covers ? { bounds: i.covers } : {}),
  };
}

/** The photo under the yard and how far it is shifted to line up with the robot's RTK
 * positions (display only, never sent). The choice and each source's alignment are kept
 * in the browser, so the editor and the yard card show the same. */
export class YardImagery {
  readonly sources: Imagery[] = [...IMAGERY];
  chosen: string | null = load("yarbie.imagery", (v) => (typeof v === "string" ? v : null), null, true);
  id: string;
  metres: [number, number];
  lat = 0; // the yard's latitude, for turning metres into degrees

  constructor(private readonly map: maplibregl.Map) {
    this.id = this.chosen && this.sources.some((i) => i.id === this.chosen) ? this.chosen : "esri";
    this.metres = loadOffset(this.id);
  }

  /** The shift as degrees, to add to everything drawn. */
  offset(): Offset {
    return metresToOffset(this.metres[0], this.metres[1], this.lat);
  }

  /** Sources that need a key on the server (Google), just above the other photos. */
  add(source: Imagery) {
    if (this.sources.some((i) => i.id === source.id)) return;
    this.sources.push(source);
    this.map.addSource(source.id, rasterSource(source));
    const firstShape = this.map.getStyle().layers.find((l) => !l.id.startsWith("imagery-"))?.id;
    this.map.addLayer(
      { id: `imagery-${source.id}`, type: "raster", source: source.id, layout: { visibility: "none" } },
      firstShape,
    );
  }

  /** The sharpest source that covers the yard, for when none has been picked. */
  best(reference: [number, number]): string {
    const [lat, lon] = reference;
    const covering = this.sources.filter(
      (i) => !i.covers || (lon >= i.covers[0] && lat >= i.covers[1] && lon <= i.covers[2] && lat <= i.covers[3]),
    );
    return BEST_FIRST.find((id) => covering.some((i) => i.id === id)) ?? "esri";
  }

  /** The chosen source if it is here, else the best for the yard. */
  preferred(reference: [number, number] | null): string {
    if (this.chosen && this.sources.some((i) => i.id === this.chosen)) return this.chosen;
    return reference ? this.best(reference) : "esri";
  }

  /** Show ``id``; each source lines up with the robot differently, so each keeps its own
   * alignment. */
  show(id: string, remember = false) {
    this.id = id;
    for (const i of this.sources) {
      this.map.setLayoutProperty(`imagery-${i.id}`, "visibility", i.id === "esri" || i.id === id ? "visible" : "none");
    }
    if (remember) {
      this.chosen = id;
      save("yarbie.imagery", id, true);
    }
    this.metres = loadOffset(id);
  }

  nudge(metres: [number, number]) {
    this.metres = metres;
    save(`yarbie.offset.${this.id}`, metres);
  }
}

export function loadOffset(source: string): [number, number] {
  return load(
    `yarbie.offset.${source}`,
    (v) => (Array.isArray(v) && v.length === 2 ? [Number(v[0]), Number(v[1])] : null),
    [0, 0],
  );
}

function load<T>(key: string, parse: (v: unknown) => T | null, fallback: T, raw = false): T {
  try {
    const text = localStorage.getItem(key);
    if (text === null) return fallback;
    return parse(raw ? text : JSON.parse(text)) ?? fallback;
  } catch {
    return fallback; // storage unavailable
  }
}

function save(key: string, value: unknown, raw = false) {
  try {
    localStorage.setItem(key, raw ? String(value) : JSON.stringify(value));
  } catch {
    // storage unavailable; the choice just won't persist
  }
}

// -- layers: added once the map has loaded, filled with setData

export function setData(map: maplibregl.Map, source: string, features: GeoJSON.Feature[]) {
  (map.getSource(source) as GeoJSONSource | undefined)?.setData({ type: "FeatureCollection", features });
}

function sources(map: maplibregl.Map, ...names: string[]) {
  for (const name of names) if (!map.getSource(name)) map.addSource(name, { type: "geojson", data: empty });
}

/** One square per grid cell, coloured by rank (t, 0 to 1): a blurred heatmap of a dense
 * grid averages itself into one flat colour. */
export function addHeatLayer(map: maplibregl.Map) {
  sources(map, "heat");
  map.addLayer({
    id: "heat",
    type: "fill",
    source: "heat",
    layout: { visibility: "none" },
    paint: {
      "fill-opacity": 0.6,
      "fill-antialias": false, // no seams between neighbouring cells
      "fill-color": [
        "interpolate", ["linear"], ["get", "t"],
        0, "#2166ac",
        0.35, "#67a9cf",
        0.6, "#fddb6d",
        0.8, "#f46d43",
        1, "#b2182b",
      ],
    },
  });
}

/** A route preview: its rows thin and white, the edge lap dashed blue, the plan's areas
 * outlined. */
export function addRouteLayers(map: maplibregl.Map) {
  sources(map, "route", "route-areas");
  map.addLayer({
    id: "route-areas",
    type: "line",
    source: "route-areas",
    paint: { "line-color": "#f2cc60", "line-width": 4, "line-opacity": 0.9 },
  });
  map.addLayer({
    id: "route-fill",
    type: "line",
    source: "route",
    filter: ["==", ["get", "kind"], "fill"],
    paint: { "line-color": "#ffffff", "line-width": 1, "line-opacity": 0.7 },
  });
  map.addLayer({
    id: "route-edge",
    type: "line",
    source: "route",
    filter: ["==", ["get", "kind"], "edge"],
    paint: { "line-color": "#79c0ff", "line-width": 2, "line-dasharray": [2, 1] },
  });
}

/** Map shapes drawn as they are (the editor uses it for what it cannot edit): polygons
 * filled, lines dashed, the dock a white dot. Features carry their ``color``. */
export function addShapeLayers(map: maplibregl.Map, source = "static", dashed = true) {
  sources(map, source);
  map.addLayer({
    id: `${source}-fill`,
    type: "fill",
    source,
    filter: ["==", ["geometry-type"], "Polygon"],
    paint: { "fill-color": ["get", "color"], "fill-opacity": 0.2 },
  });
  map.addLayer({
    id: `${source}-line`,
    type: "line",
    source,
    filter: ["!=", ["geometry-type"], "Point"],
    paint: { "line-color": ["get", "color"], "line-width": 2, ...(dashed ? { "line-dasharray": [3, 2] } : {}) },
  });
  map.addLayer({
    id: source === "static" ? "dock" : `${source}-dock`,
    type: "circle",
    source,
    filter: ["==", ["get", "family"], "dock"],
    paint: {
      "circle-radius": 7,
      "circle-color": "#ffffff",
      "circle-stroke-color": "#0d1117",
      "circle-stroke-width": 3,
    },
  });
}

/** The run: the route mowed so far as a swath about a cut wide, the rest as a thin line. */
export function addRunLayers(map: maplibregl.Map) {
  sources(map, "run");
  map.addLayer({
    id: "run-left",
    type: "line",
    source: "run",
    filter: ["==", ["get", "part"], "left"],
    layout: { visibility: "none", "line-cap": "round", "line-join": "round" },
    paint: { "line-color": "#ffffff", "line-width": 1, "line-opacity": 0.6, "line-dasharray": [2, 2] },
  });
  map.addLayer({
    id: "run-done",
    type: "line",
    source: "run",
    filter: ["==", ["get", "part"], "done"],
    layout: { visibility: "none", "line-cap": "round", "line-join": "round" },
    paint: { "line-color": "#7ee787", "line-width": 3, "line-opacity": 0.55 },
  });
}

export function addRobotLayer(map: maplibregl.Map) {
  sources(map, "robot");
  map.addLayer({
    id: "robot",
    type: "circle",
    source: "robot",
    paint: {
      "circle-radius": 8,
      "circle-color": "#2f81f7",
      "circle-stroke-color": "#ffffff",
      "circle-stroke-width": 2,
    },
  });
}

export function addLabelLayer(map: maplibregl.Map) {
  sources(map, "labels");
  map.addLayer({
    id: "labels",
    type: "symbol",
    source: "labels",
    layout: {
      "text-field": ["get", "name"],
      "text-font": ["Open Sans Semibold"],
      "text-size": 12,
      "text-allow-overlap": false,
    },
    paint: { "text-color": "#ffffff", "text-halo-color": "#000000", "text-halo-width": 1.5 },
  });
}

/** A past run's areas, shaded by what the robot finished. */
export function addPastRunLayers(map: maplibregl.Map) {
  sources(map, "past-run");
  const color = ["match", ["get", "state"], "done", "#3fb950", "edge", "#d29922", "#f85149"] as maplibregl.ExpressionSpecification;
  map.addLayer({ id: "past-run-fill", type: "fill", source: "past-run", paint: { "fill-color": color, "fill-opacity": 0.35 } });
  map.addLayer({ id: "past-run-line", type: "line", source: "past-run", paint: { "line-color": color, "line-width": 3 } });
}

// -- what goes into them

/** Map shapes, shifted onto the photo and coloured by family. */
export function coloured(features: MapFeature[], offset: Offset): GeoJSON.Feature[] {
  return features.map((f) => ({
    type: "Feature",
    geometry: shiftGeometry(f.geometry, offset),
    properties: { ...f.properties, color: COLORS[f.properties.family] },
  }));
}

export function labels(features: MapFeature[], offset: Offset): GeoJSON.Feature[] {
  return features
    .filter((f) => f.properties.name)
    .map((f) => ({
      type: "Feature",
      geometry: shiftGeometry({ type: "Point", coordinates: labelPoint(f) }, offset),
      properties: { name: f.properties.name },
    }));
}

function labelPoint(f: MapFeature): GeoJSON.Position {
  const g = f.geometry;
  const pts = g.type === "Polygon" ? g.coordinates[0].slice(0, -1) : g.type === "LineString" ? g.coordinates : [g.coordinates];
  if (g.type === "LineString") return pts[Math.floor(pts.length / 2)];
  const n = pts.length || 1;
  return [pts.reduce((s, p) => s + p[0], 0) / n, pts.reduce((s, p) => s + p[1], 0) / n];
}

export function robot(position: { lon: number; lat: number } | null | undefined, offset: Offset): GeoJSON.Feature[] {
  if (!position) return [];
  return [
    {
      type: "Feature",
      geometry: shiftGeometry({ type: "Point", coordinates: [position.lon, position.lat] }, offset),
      properties: {},
    },
  ];
}

/** The run's route split into what was mowed and what is left. */
export function drawRun(map: maplibregl.Map, run: Progress | null, offset: Offset, lat: number) {
  const features: GeoJSON.Feature[] = [];
  for (const p of run?.paths ?? []) {
    for (const part of ["done", "left"] as const) {
      if (p[part].length > 1) {
        features.push({
          type: "Feature",
          geometry: shiftGeometry({ type: "LineString", coordinates: p[part] }, offset),
          properties: { part, area: p.area_id, kind: p.kind },
        });
      }
    }
  }
  setData(map, "run", features);
  // A swath CUT_WIDTH_M wide at every zoom (pixels per metre double with each level).
  const mpp0 = 156543.03 * Math.cos((lat * Math.PI) / 180);
  map.setPaintProperty("run-done", "line-width", [
    "interpolate", ["exponential", 2], ["zoom"], 10, (CUT_WIDTH_M * 2 ** 10) / mpp0, 24, (CUT_WIDTH_M * 2 ** 24) / mpp0,
  ]);
}

export function showRun(map: maplibregl.Map, on: boolean) {
  for (const id of ["run-done", "run-left"]) map.setLayoutProperty(id, "visibility", on ? "visible" : "none");
}

/** Estimated wear, one square per cell. Coloured by rank among the distinct counts, so
 * the busiest cells are red and the quietest blue whatever the spread (pathways run to
 * hundreds, lawns to dozens). */
export function drawHeat(map: maplibregl.Map, heat: Heatmap | null, offset: Offset) {
  if (!heat) return setData(map, "heat", []);
  const levels = [...new Set(heat.cells.map((c) => c[2]))].sort((a, b) => a - b);
  const rank = new Map(levels.map((w, i) => [w, levels.length > 1 ? i / (levels.length - 1) : 1]));
  const lat0 = heat.cells[0]?.[1] ?? 0;
  const halfLat = heat.cell_m / 2 / 111320;
  const halfLon = halfLat / Math.cos((lat0 * Math.PI) / 180);
  setData(
    map,
    "heat",
    heat.cells.map(([lon, lat, w]) => {
      const [x, y] = [lon + offset[0], lat + offset[1]];
      const square = [
        [x - halfLon, y - halfLat],
        [x + halfLon, y - halfLat],
        [x + halfLon, y + halfLat],
        [x - halfLon, y + halfLat],
        [x - halfLon, y - halfLat],
      ];
      return {
        type: "Feature" as const,
        geometry: { type: "Polygon" as const, coordinates: [square] },
        properties: { w, t: rank.get(w) ?? 0 },
      };
    }),
  );
}

export function showHeat(map: maplibregl.Map, on: boolean) {
  map.setLayoutProperty("heat", "visibility", on ? "visible" : "none");
}
