import "maplibre-gl/dist/maplibre-gl.css";
import "./style.css";

import * as maplibregl from "maplibre-gl";
import type { GeoJSONSource } from "maplibre-gl";
// Bundle MapLibre's worker (it renders GeoJSON) so it is served with the app.
import workerUrl from "maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url";
import { api, ApiError, type Family, type Heatmap, type Imagery, type Progress, type MapFeature, type MapResponse, type Op, type Plan, type Status } from "./api";
import { COLORS, Editor, EDITABLE, type Tool } from "./editor";
import { debugLog, installDebug } from "./debug";
import { settingsForm, trackChanges } from "./areaSettings";
import { bounds, circle, circleFit, metresToOffset, shiftGeometry, type Offset } from "./geo";


const ESRI = "Imagery © Esri, Maxar, Earthstar Geographics, and the GIS User Community";
const IMAGERY: Imagery[] = [
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
let imageryChosen = loadImagery();
let imagery = imageryChosen ?? "esri";
const STATIC_FAMILIES: Family[] = ["novisionzones", "elec_fence", "deadends", "dock"];
const PATTERNS: [number, string][] = [
  [0, "Rows"],
  [4, "Rows, fewer turns"],
  [1, "Spiral (contour)"],
];
const NUDGE_M = 0.25;

const $ = <T extends HTMLElement = HTMLElement>(id: string) => document.getElementById(id) as T;
const esc = (s: string) =>
  s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]!);

// -- state

let loaded: MapResponse | null = null;
let status: Status | null = null;
let ops: Op[] = [];
let plans: Plan[] = [];
let shownRoute: number | null = null;
let selectedId: string | null = null;
let armedApply = false;
let offsetMetres: [number, number] = loadOffset(imagery);

// -- map

maplibregl.setWorkerUrl(workerUrl);

const map = new maplibregl.Map({
  container: "map",
  style: {
    version: 8,
    glyphs: "https://demotiles.maplibre.org/font/{fontstack}/{range}.pbf",
    sources: Object.fromEntries(
      IMAGERY.map((i) => [
        i.id,
        {
          type: "raster" as const,
          tiles: [i.tiles],
          tileSize: 256,
          maxzoom: i.maxzoom,
          attribution: i.attribution,
          ...(i.covers ? { bounds: i.covers } : {}),
        },
      ]),
    ),
    // Esri stays underneath whatever is chosen, to fill gaps in local imagery.
    layers: IMAGERY.map((i) => ({
      id: `imagery-${i.id}`,
      type: "raster" as const,
      source: i.id,
      layout: { visibility: i.id === "esri" || i.id === imagery ? ("visible" as const) : ("none" as const) },
    })),
  },
  center: [0, 20],
  zoom: 2,
  maxZoom: 24, // the highest MapLibre supports
});
installDebug($("map"));
map.addControl(new maplibregl.NavigationControl({ visualizePitch: false }), "top-right");
map.addControl(new maplibregl.ScaleControl({ unit: "metric" }), "bottom-right");

const empty: GeoJSON.FeatureCollection = { type: "FeatureCollection", features: [] };
let editor: Editor;

map.on("load", async () => {
  map.addSource("static", { type: "geojson", data: empty });
  map.addSource("route", { type: "geojson", data: empty });
  map.addSource("labels", { type: "geojson", data: empty });
  map.addSource("robot", { type: "geojson", data: empty });
  map.addSource("problems", { type: "geojson", data: empty });
  map.addSource("heat", { type: "geojson", data: empty });
  map.addSource("run", { type: "geojson", data: empty });
  // One square per grid cell, coloured by rank (t, 0 to 1): a blurred heatmap of a dense
  // grid averages itself into one flat colour.
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
  map.addLayer({
    id: "static-fill",
    type: "fill",
    source: "static",
    filter: ["==", ["geometry-type"], "Polygon"],
    paint: { "fill-color": ["get", "color"], "fill-opacity": 0.2 },
  });
  map.addLayer({
    id: "static-line",
    type: "line",
    source: "static",
    filter: ["!=", ["geometry-type"], "Point"],
    paint: { "line-color": ["get", "color"], "line-width": 2, "line-dasharray": [3, 2] },
  });
  map.addLayer({
    id: "dock",
    type: "circle",
    source: "static",
    filter: ["==", ["get", "family"], "dock"],
    paint: {
      "circle-radius": 7,
      "circle-color": "#ffffff",
      "circle-stroke-color": "#0d1117",
      "circle-stroke-width": 3,
    },
  });
  map.addLayer({
    id: "problems",
    type: "circle",
    source: "problems",
    paint: {
      "circle-radius": ["interpolate", ["linear"], ["zoom"], 16, 7, 22, 14],
      "circle-color": "rgba(0,0,0,0)",
      "circle-stroke-color": "#ff4d4f",
      "circle-stroke-width": 3,
    },
  });
  // The run: the route mowed so far as a swath about a cut wide, the rest as a thin line.
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

  editor = new Editor(map);
  if (import.meta.env.DEV) Object.assign(window, { yarbie: { map, editor } }); // for local debugging
  editor.onChange(onEdit);
  editor.onSelect((id) => {
    selectedId = id;
    renderSelection();
  });
  buildLayerToggles();
  const picker = $("imagery") as HTMLSelectElement;
  picker.innerHTML = IMAGERY.map(
    (i) => `<option value="${i.id}" ${i.id === imagery ? "selected" : ""}>${esc(i.label)}</option>`,
  ).join("");
  picker.addEventListener("change", () => setImagery(picker.value));
  wireControls();
  await reload(true);
  await refreshPlans();
  await refreshHistory();
  pollStatus();
});

// -- loading

async function reload(fit: boolean) {
  try {
    loaded = await api.map();
  } catch (err) {
    say(`Could not read the map: ${message(err)}`, "error");
    return;
  }
  editor.load(loaded.features);
  await addKeyedImagery();
  if (!imageryChosen && loaded.reference) setImagery(bestImagery(loaded.reference), false);
  applyOffset();
  if (fit) fitToMap();
  const locked = editor.locked();
  if (locked.length) {
    say(
      `Loaded ${loaded.features.features.length} map objects. ${locked.length} could not be opened ` +
        `for editing and are kept unchanged (dashed): ${locked.map((f) => f.properties.name).join(", ")}.`,
      "error",
    );
  } else {
    say(`Loaded ${loaded.features.features.length} map objects.`, "ok");
  }
}

function staticFeatures(): GeoJSON.Feature[] {
  if (!loaded) return [];
  const offset = currentOffset();
  const locked = editor.locked();
  return [...loaded.features.features, ...locked]
    .filter(
      (f) =>
        (STATIC_FAMILIES.includes(f.properties.family) || locked.includes(f)) &&
        !hidden.has(f.properties.family),
    )
    .map((f) => ({
      type: "Feature",
      geometry: shiftGeometry(f.geometry, offset),
      properties: { ...f.properties, color: COLORS[f.properties.family] },
    }));
}

function fitToMap() {
  const box = loaded && bounds(loaded.features.features);
  if (box) map.fitBounds(box, { padding: 60, maxZoom: 21, duration: 0 });
}

// -- imagery offset (display only)

function currentOffset(): Offset {
  const lat = loaded?.reference?.[0] ?? 0;
  return metresToOffset(offsetMetres[0], offsetMetres[1], lat);
}

function applyOffset() {
  editor.setOffset(currentOffset());
  (map.getSource("static") as GeoJSONSource).setData({ type: "FeatureCollection", features: staticFeatures() });
  updateLabels();
  drawProblems();
  drawHeat();
  drawRun();
  if (shownRoute !== null) void showRoute(shownRoute);
  $("offset").textContent = `${offsetMetres[0].toFixed(2)} m E, ${offsetMetres[1].toFixed(2)} m N`;
  try {
    localStorage.setItem(`yarbie.offset.${imagery}`, JSON.stringify(offsetMetres));
  } catch {
    // storage unavailable; the offset just won't persist
  }
}

function loadImagery(): string | null {
  try {
    const saved = localStorage.getItem("yarbie.imagery");
    if (saved && IMAGERY.some((i) => i.id === saved)) return saved;
  } catch {
    // storage unavailable
  }
  return null;
}

/** Sources that need a key on the server (Google), added once the map is known. */
let keyedImageryAdded = false;
async function addKeyedImagery() {
  if (keyedImageryAdded) return;
  keyedImageryAdded = true;
  let extra: { sources: Imagery[]; error?: string; hint?: string };
  try {
    extra = await api.imagery();
  } catch {
    return;
  }
  if (extra.error || extra.hint) {
    // Say why Google is missing instead of leaving it out silently.
    const option = document.createElement("option");
    option.disabled = true;
    option.textContent = extra.error ? "Google satellite (unavailable, see message)" : "Google satellite (add GOOGLE_MAPS_API_KEY to .env)";
    $("imagery").prepend(option);
    if (extra.error) say(extra.error, "error");
  }
  for (const source of extra.sources) {
    if (IMAGERY.some((i) => i.id === source.id)) continue;
    IMAGERY.push(source);
    map.addSource(source.id, {
      type: "raster",
      tiles: [source.tiles],
      tileSize: 256,
      maxzoom: source.maxzoom,
      attribution: source.attribution,
    });
    // Just above the other imagery, below every map shape.
    const firstShape = map.getStyle().layers.find((l) => !l.id.startsWith("imagery-"))?.id;
    map.addLayer(
      {
        id: `imagery-${source.id}`,
        type: "raster",
        source: source.id,
        layout: { visibility: source.id === imagery ? "visible" : "none" },
      },
      firstShape,
    );
    const option = document.createElement("option");
    option.value = source.id;
    option.textContent = source.label;
    $("imagery").prepend(option);
  }
  if (imageryChosen && IMAGERY.some((i) => i.id === imageryChosen)) {
    ($("imagery") as HTMLSelectElement).value = imageryChosen;
  }
}

/** The sharpest imagery that covers the yard, for when none has been picked. */
function bestImagery(reference: [number, number]): string {
  const [lat, lon] = reference;
  const covering = IMAGERY.filter(
    (i) => !i.covers || (lon >= i.covers[0] && lat >= i.covers[1] && lon <= i.covers[2] && lat <= i.covers[3]),
  );
  return BEST_FIRST.find((id) => covering.some((i) => i.id === id)) ?? "esri";
}

function setImagery(id: string, remember = true) {
  imagery = id;
  debugLog("imagery", {
    id,
    remember,
    offset: loadOffset(id),
    layers: map.getStyle().layers.map((l) => `${l.id}:${l.layout?.visibility ?? "visible"}`),
  });
  ($("imagery") as HTMLSelectElement).value = id;
  for (const i of IMAGERY) {
    map.setLayoutProperty(`imagery-${i.id}`, "visibility", i.id === "esri" || i.id === id ? "visible" : "none");
  }
  if (remember) {
    imageryChosen = id;
    try {
      localStorage.setItem("yarbie.imagery", id);
    } catch {
      // storage unavailable
    }
  }
  // Each source lines up with the robot differently, so each keeps its own alignment.
  offsetMetres = loadOffset(id);
  applyOffset();
}

function loadOffset(source: string): [number, number] {
  try {
    const saved = JSON.parse(localStorage.getItem(`yarbie.offset.${source}`) ?? "null");
    if (Array.isArray(saved) && saved.length === 2) return [Number(saved[0]), Number(saved[1])];
  } catch {
    // ignore
  }
  return [0, 0];
}

// -- edits and the change list

let previewTimer: number | undefined;

function onEdit() {
  ($("undo") as HTMLButtonElement).disabled = !editor.canUndo();
  ($("redo") as HTMLButtonElement).disabled = !editor.canRedo();
  disarmApply();
  updateLabels();
  if (selectedId) renderSelection();
  window.clearTimeout(previewTimer);
  previewTimer = window.setTimeout(refreshOps, 350);
}

async function refreshOps() {
  try {
    ops = (await api.preview(editor.collection())).ops;
  } catch (err) {
    say(`Could not compare with the robot: ${message(err)}`, "error");
    return;
  }
  renderOps();
}

function renderOps() {
  const list = $("ops");
  $("change-count").textContent = String(ops.length);
  ($("discard-all") as HTMLButtonElement).disabled = !ops.length;
  if (!ops.length) {
    list.innerHTML = `<li class="empty">No changes. Draw or edit something on the map.</li>`;
  } else {
    list.innerHTML = ops
      .map(
        (op, i) => `<li data-i="${i}" class="${op.supported ? "" : "unsupported"}">
          <button class="discard" data-discard="${i}" title="Discard this change">✕</button>
          <span class="kind ${op.kind}">${op.kind}</span> ${esc(familyLabel(op.family))}
          <b>${esc(op.name)}</b>${op.changes.length ? ` <span class="hint">(${esc(op.changes.join(", "))})</span>` : ""}
          ${op.supported ? "" : `<div class="reason">Export only: ${esc(op.reason ?? "")}</div>`}
          ${op.warnings
            .map(
              (w, j) => `<div class="warn">⚠ ${esc(w.text)}${
                w.points.length ? ` <button class="show" data-op="${i}" data-warn="${j}">Show</button>` : ""
              }</div>`,
            )
            .join("")}
        </li>`,
      )
      .join("");
    list.querySelectorAll<HTMLButtonElement>("button[data-discard]").forEach((b) =>
      b.addEventListener("click", (e) => {
        e.stopPropagation();
        editor.revert(ops[Number(b.dataset.discard)].key);
        say("Change discarded.");
      }),
    );
    list.querySelectorAll<HTMLButtonElement>("button.show").forEach((b) =>
      b.addEventListener("click", (e) => {
        e.stopPropagation();
        showProblem(ops[Number(b.dataset.op)].warnings[Number(b.dataset.warn)].points);
      }),
    );
    list.querySelectorAll("li[data-i]").forEach((li) =>
      li.addEventListener("click", () => {
        const op = ops[Number((li as HTMLElement).dataset.i)];
        if (op.kind !== "delete") editor.select(op.key);
      }),
    );
  }
  drawProblems();
  updateApplyButton();
}

/** Ring every spot a warning points at, so problems can be found on the map. */
function drawProblems() {
  const offset = currentOffset();
  const features: GeoJSON.Feature[] = ops.flatMap((op) =>
    op.warnings.flatMap((w) =>
      w.points.map((p) => ({
        type: "Feature" as const,
        geometry: shiftGeometry({ type: "Point" as const, coordinates: p }, offset),
        properties: { text: w.text },
      })),
    ),
  );
  (map.getSource("problems") as GeoJSONSource | undefined)?.setData({ type: "FeatureCollection", features });
  if (map.getLayer("problems")) map.moveLayer("problems"); // above the shapes being edited
}

function showProblem(points: [number, number][]) {
  const offset = currentOffset();
  const shifted = points.map((p) => shiftGeometry({ type: "Point" as const, coordinates: p }, offset).coordinates);
  if (shifted.length === 1) {
    map.flyTo({ center: shifted[0] as [number, number], zoom: Math.max(map.getZoom(), 22) });
  } else {
    const box = bounds(shifted.map((c) => ({ geometry: { type: "Point" as const, coordinates: c } })));
    if (box) map.fitBounds(box, { padding: 120, maxZoom: 22 });
  }
}

function updateApplyButton() {
  const button = $("apply") as HTMLButtonElement;
  const sendable = ops.filter((o) => o.supported).length;
  const writable = !!status?.writable;
  button.disabled = !writable || sendable === 0 || !!status?.busy;
  button.textContent = armedApply ? `Confirm: send ${sendable} to robot` : "Apply to robot";
  button.className = armedApply ? "confirm" : "primary";
  const hint = $("apply-hint");
  if (!writable) {
    hint.textContent =
      status?.source === "cloud"
        ? "Read-only: the cloud source cannot change the robot. Export your edits, or run yarbie on the robot's network."
        : "Not connected to the robot.";
  } else if (status?.busy) {
    hint.textContent = "The robot is in a plan. Stop or finish it before changing the map.";
  } else if (ops.length > sendable) {
    hint.textContent = `${ops.length - sendable} change(s) can only be exported, not sent.`;
  } else {
    hint.textContent = sendable ? "A backup of the robot's map is saved before anything is sent." : "";
  }
}

function disarmApply() {
  if (armedApply) {
    armedApply = false;
    updateApplyButton();
  }
}

async function apply() {
  if (!loaded) return;
  if (!armedApply) {
    armedApply = true;
    updateApplyButton();
    return;
  }
  armedApply = false;
  ($("apply") as HTMLButtonElement).disabled = true;
  say("Sending to the robot…");
  try {
    const result = await api.apply(editor.collection(), loaded.revision);
    if (result.map) {
      loaded = result.map;
      editor.load(loaded.features);
      applyOffset();
    }
    await refreshOps();
    if (result.error) {
      say(`Stopped after ${result.applied.length} change(s): ${result.error}`, "error");
    } else {
      say(`Sent ${result.applied.length} change(s) to the robot.`, "ok");
    }
    if (result.backup) offerUndoApply(result.backup);
    await refreshHistory();
  } catch (err) {
    say(message(err), "error");
    updateApplyButton();
  }
}

// -- the current run: what has been mowed, what is left

const CUT_WIDTH_M = 0.5; // drawn width of a mowed row
let runTimer: number | undefined;
let run: Progress | null = null;

let runActive = false;

/** Show the Current run section only while the robot is in a plan (working, or paused
 * mid-run to charge); hide it, and everything it drew, otherwise. */
function setRunActive(active: boolean) {
  if (active === runActive) return;
  runActive = active;
  $("run-section").hidden = !active;
  void refreshRun();
}

async function refreshRun() {
  window.clearTimeout(runTimer);
  const on = runActive && ($("run-on") as HTMLInputElement).checked;
  for (const id of ["run-done", "run-left"]) map.setLayoutProperty(id, "visibility", on ? "visible" : "none");
  if (!on) {
    run = null;
    $("run-info").innerHTML = "";
    (map.getSource("run") as GeoJSONSource | undefined)?.setData(empty);
    return;
  }
  try {
    run = await api.progress();
  } catch (err) {
    $("run-info").textContent = `Could not read the run: ${message(err)}`;
    runTimer = window.setTimeout(refreshRun, 30_000);
    return;
  }
  drawRun();
  runTimer = window.setTimeout(refreshRun, 10_000);
}

function drawRun() {
  const source = map.getSource("run") as GeoJSONSource | undefined;
  if (!source || !run) return;
  const offset = currentOffset();
  const features: GeoJSON.Feature[] = [];
  for (const p of run.paths ?? []) {
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
  source.setData({ type: "FeatureCollection", features });
  // A swath CUT_WIDTH_M wide at every zoom (pixels per metre double with each level).
  const lat = loaded?.reference?.[0] ?? 0;
  const mpp0 = 156543.03 * Math.cos((lat * Math.PI) / 180);
  map.setPaintProperty("run-done", "line-width", [
    "interpolate", ["exponential", 2], ["zoom"], 10, (CUT_WIDTH_M * 2 ** 10) / mpp0, 24, (CUT_WIDTH_M * 2 ** 24) / mpp0,
  ]);
  renderRunInfo();
}

function renderRunInfo() {
  const info = $("run-info");
  if (!run || !run.plan) {
    info.textContent = "The robot has no run to show.";
    return;
  }
  const name = (id: number | null | undefined) =>
    loaded?.features.features.find((f) => f.properties.family === "areas" && f.properties.id === id)?.properties.name ?? `#${id}`;
  const hours = (s: number | null | undefined) =>
    s == null ? "?" : s >= 3600 ? `${Math.floor(s / 3600)} h ${Math.round((s % 3600) / 60)} min` : `${Math.round(s / 60)} min`;
  const rows = (run.area_ids ?? []).map((id) => {
    const parts = (run!.paths ?? []).filter((p) => p.area_id === id);
    const fill = parts.find((p) => p.kind === "fill")?.fraction ?? 0;
    const edge = parts.find((p) => p.kind === "edge")?.fraction ?? 0;
    const state =
      fill >= 1 && edge >= 1 ? "mowed" : fill > 0 || edge > 0 ? `mowing, ${Math.round(fill * 100)}% of rows${edge >= 1 ? ", edge done" : ""}` : "not started";
    return `<li class="${id === run!.current_area ? "now" : ""}"><span>${esc(name(id))}</span><span>${state}</span></li>`;
  });
  const pct = Math.round(run.progress ?? 0);
  info.innerHTML = `<b>${esc(run.plan.name ?? `Plan ${run.plan.id}`)}</b> · ${
    run.running ? "running" : "last run, not running now"
  }
    <div class="bar"><i style="width:${pct}%"></i></div>
    ${pct}% of the plan's area${run.running ? ` · about ${hours(run.remaining_s)} left` : ""}
    <ul class="run-areas">${rows.join("")}</ul>
    <span>Green: mowed. Dashed: still to mow.</span>`;
}

// -- wear heat map: run history spread over the map (the robot records no tracks)

let heat: Heatmap | null = null;

async function loadHeat(refresh = false) {
  const on = ($("heat-on") as HTMLInputElement).checked;
  map.setLayoutProperty("heat", "visibility", on ? "visible" : "none");
  if (!on) return;
  const info = $("heat-info");
  info.textContent = "Reading the robot's run history…";
  try {
    heat = await api.heatmap(Number(($("heat-days") as HTMLSelectElement).value), refresh);
  } catch (err) {
    info.textContent = `No heat map: ${message(err)}`;
    return;
  }
  drawHeat();
  const day = (t: number | null) => (t ? new Date(t * 1000).toLocaleDateString(undefined, { dateStyle: "medium" }) : "?");
  info.innerHTML = heat.runs
    ? `<div class="scale"></div>
       <div class="row" style="margin:0;justify-content:space-between"><span>rarely</span><span>most driven</span></div>
       ${heat.runs} runs, ${day(heat.first)} – ${day(heat.last)}, ${heat.trips} trips out and home.
       Busiest pathways: ${heat.pathways.map((p) => `${esc(p.name)} (~${p.passes})`).join(", ") || "none"}.
       <br/>An estimate: the robot keeps no record of where it drove, so each run's areas, edge laps and
       recharge trips are spread over today's map.`
    : "No runs in this period.";
}

function drawHeat() {
  const source = map.getSource("heat") as GeoJSONSource | undefined;
  if (!source || !heat) return;
  const offset = currentOffset();
  // Colour by rank among the distinct counts, so the busiest cells are red and the
  // quietest blue whatever the spread (pathways run to hundreds, lawns to dozens).
  const levels = [...new Set(heat.cells.map((c) => c[2]))].sort((a, b) => a - b);
  const rank = new Map(levels.map((w, i) => [w, levels.length > 1 ? i / (levels.length - 1) : 1]));
  const lat0 = heat.cells[0]?.[1] ?? 0;
  const halfLat = heat.cell_m / 2 / 111320;
  const halfLon = halfLat / Math.cos((lat0 * Math.PI) / 180);
  source.setData({
    type: "FeatureCollection",
    features: heat.cells.map(([lon, lat, w]) => {
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
  });
}

// -- undoing what was sent: every apply first saves the robot's map

function offerUndoApply(backup: string) {
  const box = $("undo-apply-box");
  box.hidden = false;
  box.innerHTML = `<button id="undo-apply">Undo this apply</button>`;
  $("undo-apply").addEventListener("click", () => revertTo(backup));
}

async function refreshHistory() {
  let backups: { name: string }[];
  try {
    backups = await api.backups();
  } catch {
    return;
  }
  $("history").innerHTML = backups.length
    ? backups
        .slice(0, 20)
        .map(
          (b, i) => `<li><div><b>${esc(backupTime(b.name))}</b>
            <div class="meta">${i === 0 ? "Before your last apply" : "Before an apply"}</div></div>
            <button data-revert="${esc(b.name)}">Revert to this</button></li>`,
        )
        .join("")
    : `<li class="meta">Nothing sent yet.</li>`;
  $("history").querySelectorAll<HTMLButtonElement>("button[data-revert]").forEach((b) =>
    b.addEventListener("click", () => revertTo(b.dataset.revert!)),
  );
}

/** Load an older map as pending changes; nothing is sent until it is applied. */
async function revertTo(backup: string) {
  try {
    const { features } = await api.backup(backup);
    editor.replaceAll(features.features);
    $("undo-apply-box").hidden = true;
    say(`Loaded the map from ${backupTime(backup)}. Review the changes, then apply them to revert.`, "ok");
  } catch (err) {
    say(`Could not load that backup: ${message(err)}`, "error");
  }
}

function backupTime(name: string): string {
  const m = /^(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})/.exec(name);
  if (!m) return name;
  const when = new Date(Date.UTC(+m[1], +m[2] - 1, +m[3], +m[4], +m[5], +m[6]));
  return when.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

// -- selection panel

function renderSelection() {
  const section = $("selection");
  const feature = selectedId ? editor.feature(selectedId) : undefined;
  section.hidden = !feature;
  if (!feature) return;
  const p = feature.properties;
  const isNew = p.id === null;
  const body = $("selection-body");
  body.innerHTML = `
    <p class="facts"><i class="sw ${p.family}"></i> ${esc(familyLabel(p.family))}
      ${isNew ? "· new" : `· #${p.id}`}${p.area_m2 ? ` · ${Math.round(p.area_m2)} m²` : ""}
      ${p.family === "pathways" && p.start_id === 0 ? " · linked to dock" : ""}</p>
    <label class="field">Name<input type="text" id="sel-name" value="${esc(p.name)}" /></label>
    ${
      p.family === "nogozones"
        ? `<label class="check"><input type="checkbox" id="sel-enabled" ${p.enabled !== false ? "checked" : ""}/> Enabled</label>`
        : ""
    }
    ${
      p.family === "areas"
        ? `<label class="field">Mowing pattern<select id="sel-pattern">${PATTERNS.map(
            ([v, label]) => `<option value="${v}" ${p.algorithm_type === v ? "selected" : ""}>${label}</option>`,
          ).join("")}${
            p.algorithm_type != null && !PATTERNS.some(([v]) => v === p.algorithm_type)
              ? `<option value="${p.algorithm_type}" selected>Type ${p.algorithm_type}</option>`
              : ""
          }</select></label>`
        : ""
    }
    ${
      p.family === "nogozones" && feature.geometry.type === "Polygon"
        ? `<fieldset><legend>Make it a circle</legend>
            <p class="facts" id="circle-info"></p>
            <label class="field">Radius <span class="unit">m</span><input type="number" id="circle-r" step="0.1" min="0.2"/></label>
            <div class="row"><button id="circle-apply">Make circle</button></div></fieldset>`
        : ""
    }
    ${
      p.family === "nogozones" && feature.geometry.type === "Polygon"
        ? `<fieldset><legend>Move</legend>
            <div class="nudge" style="margin-top:0">
              <div class="nudge-grid">
                <span></span><button data-move="0,1" title="North">▲</button><span></span>
                <button data-move="-1,0" title="West">◀</button><span></span><button data-move="1,0" title="East">▶</button>
                <span></span><button data-move="0,-1" title="South">▼</button><span></span>
              </div>
              <label class="field" style="margin:0">Step<select id="move-step">${MOVE_STEPS.map(
                (s) => `<option value="${s}" ${s === moveStep ? "selected" : ""}>${s} m</option>`,
              ).join("")}</select></label>
            </div>
            <p class="hint">Arrow keys move it too; Shift+arrow moves five steps.</p></fieldset>`
        : ""
    }
    <div class="row"><button id="sel-delete" class="danger">Delete</button></div>
    ${p.family === "areas" && !isNew ? `<div id="params"><p class="hint">Loading mowing settings…</p></div>` : ""}
  `;
  const id = feature.id;
  ($("sel-name") as HTMLInputElement).addEventListener("change", (e) =>
    editor.updateProperties(id, { name: (e.target as HTMLInputElement).value.trim() }),
  );
  $("sel-enabled")?.addEventListener("change", (e) =>
    editor.updateProperties(id, { enabled: (e.target as HTMLInputElement).checked }),
  );
  $("sel-pattern")?.addEventListener("change", (e) =>
    editor.updateProperties(id, { algorithm_type: Number((e.target as HTMLSelectElement).value) }),
  );
  if (p.family === "nogozones" && feature.geometry.type === "Polygon") {
    const fit = circleFit(feature.geometry.coordinates[0]);
    const radius = $("circle-r") as HTMLInputElement;
    radius.value = fit.cover.toFixed(1);
    $("circle-info").textContent =
      `A circle of ${fit.cover.toFixed(1)} m covers the whole shape; its corners average ${fit.average.toFixed(1)} m from the centre.`;
    $("circle-apply").addEventListener("click", () => {
      const r = Number(radius.value);
      if (!(r > 0)) return say("Give the radius in metres.", "error");
      editor.setGeometry(id, circle(fit.center, r));
      say(`Made ${p.name || "the zone"} a circle of ${r} m. It is a change to review and apply like any other.`, "ok");
      renderSelection();
    });
  }
  body.querySelectorAll<HTMLButtonElement>("button[data-move]").forEach((b) =>
    b.addEventListener("click", () => {
      const [east, north] = b.dataset.move!.split(",").map(Number);
      moveSelected(east, north);
    }),
  );
  $("move-step")?.addEventListener("change", (e) => {
    moveStep = Number((e.target as HTMLSelectElement).value);
    try {
      localStorage.setItem("yarbie.moveStep", String(moveStep));
    } catch {
      // storage unavailable
    }
  });
  $("sel-delete").addEventListener("click", () => {
    editor.remove(id);
    selectedId = null;
    renderSelection();
  });
  if (p.family === "areas" && p.id !== null) void renderParams(p.id);
}

// -- moving a no-go zone by steps, like the imagery alignment

const MOVE_STEPS = [0.1, 0.25, 0.5, 1];
let moveStep = (() => {
  try {
    const saved = Number(localStorage.getItem("yarbie.moveStep"));
    if (MOVE_STEPS.includes(saved)) return saved;
  } catch {
    // storage unavailable
  }
  return 0.25;
})();
let moved: [number, number] = [0, 0]; // since this zone was selected, for the message
let movedId: string | null = null;

/** Shift the selected no-go zone by whole steps east and north. */
function moveSelected(east: number, north: number) {
  const feature = selectedId ? editor.feature(selectedId) : undefined;
  if (!feature || feature.properties.family !== "nogozones" || feature.geometry.type !== "Polygon") return;
  const lat = feature.geometry.coordinates[0][0][1];
  const shift = metresToOffset(east * moveStep, north * moveStep, lat);
  editor.setGeometry(feature.id, shiftGeometry(feature.geometry, shift));
  if (movedId !== feature.id) moved = [0, 0];
  movedId = feature.id;
  moved = [moved[0] + east * moveStep, moved[1] + north * moveStep];
  const part = (v: number, pos: string, neg: string) => (Math.abs(v) < 1e-9 ? "" : `${Math.abs(v).toFixed(2)} m ${v > 0 ? pos : neg}`);
  say(`Moved ${feature.properties.name || "the zone"} ${[part(moved[1], "north", "south"), part(moved[0], "east", "west")].filter(Boolean).join(", ")} so far.`);
}

// Mowing settings are saved straight to the robot (they are not part of the map diff).
async function renderParams(areaId: number) {
  const box = $("params");
  if (!box) return;
  if (status && !status.writable) {
    box.innerHTML = `<p class="hint">Mowing settings need a connection to the robot.</p>`;
    return;
  }
  let params: Record<string, unknown>;
  let editable: string[];
  try {
    ({ params, editable } = await api.mowerParams(areaId));
  } catch (err) {
    box.innerHTML = `<p class="hint">${esc(message(err))}</p>`;
    return;
  }
  const others = (loaded?.features.features ?? [])
    .filter((f) => f.properties.family === "areas" && f.properties.id !== null && f.properties.id !== areaId)
    .map((f) => ({ id: f.properties.id!, name: f.properties.name }));
  box.innerHTML = settingsForm(params, editable, others);
  const save = $("params-save") as HTMLButtonElement;
  let changes: Record<string, unknown> = {};
  trackChanges(box, (c) => {
    changes = c;
    save.disabled = false;
  });
  save.addEventListener("click", async () => {
    save.disabled = true;
    const targets = [areaId, ...[...box.querySelectorAll<HTMLInputElement>("input[data-also]:checked")].map((i) => Number(i.dataset.also))];
    const done: number[] = [];
    try {
      for (const target of targets) {
        await api.updateMowerParams(target, changes);
        done.push(target);
      }
      say(`Mowing settings saved to ${done.length === 1 ? "this area" : `${done.length} areas`}.`, "ok");
      if (selectedId && editor.feature(selectedId)?.properties.id === areaId) void renderParams(areaId);
    } catch (err) {
      say(`Saved to ${done.length} of ${targets.length} areas, then: ${message(err)}`, "error");
      save.disabled = false;
    }
  });
}

// -- plans

async function refreshPlans() {
  try {
    plans = await api.plans();
  } catch (err) {
    $("plans").innerHTML = `<li class="meta">${esc(message(err))}</li>`;
    return;
  }
  const areaName = (id: number) =>
    loaded?.features.features.find((f) => f.properties.family === "areas" && f.properties.id === id)?.properties
      .name ?? `#${id}`;
  $("plans").innerHTML = plans.length
    ? plans
        .map(
          (p) => `<li data-plan="${p.id}" class="${shownRoute === p.id ? "showing" : ""}">
            <div><b>${esc(p.name)}</b><div class="meta">${p.area_ids.map((a) => esc(areaName(a))).join(", ")}</div></div>
            <span class="row" style="margin:0">
              <button data-route="${p.id}" title="Show the route the robot would take">${shownRoute === p.id ? "Hide" : "Route"}</button>
              <button data-edit="${p.id}">Edit</button>
            </span>
          </li>`,
        )
        .join("")
    : `<li class="meta">No plans.</li>`;
  $("plans").querySelectorAll<HTMLButtonElement>("button[data-route]").forEach((b) =>
    b.addEventListener("click", () => toggleRoute(Number(b.dataset.route))),
  );
  $("plans").querySelectorAll<HTMLButtonElement>("button[data-edit]").forEach((b) =>
    b.addEventListener("click", () => editPlan(plans.find((p) => p.id === Number(b.dataset.edit)) ?? null)),
  );
}

async function toggleRoute(planId: number) {
  if (shownRoute === planId) {
    shownRoute = null;
    (map.getSource("route") as GeoJSONSource).setData(empty);
  } else {
    await showRoute(planId);
  }
  await refreshPlans();
}

async function showRoute(planId: number) {
  try {
    const route = await api.route(planId);
    const offset = currentOffset();
    shownRoute = planId;
    (map.getSource("route") as GeoJSONSource).setData({
      type: "FeatureCollection",
      features: route.features.map((f) => ({ ...f, geometry: shiftGeometry(f.geometry, offset) })),
    });
    if (route.total_s) say(`Estimated time: ${Math.round(route.total_s / 60)} min.`, "ok");
  } catch (err) {
    say(`No route: ${message(err)}`, "error");
  }
}

function editPlan(plan: Plan | null) {
  const box = $("plan-editor");
  box.hidden = false;
  const areas = (loaded?.features.features ?? []).filter((f) => f.properties.family === "areas" && f.properties.id !== null);
  box.innerHTML = `
    <h2 style="margin-top:12px">${plan ? "Edit plan" : "New plan"}</h2>
    <label class="field">Name<input type="text" id="plan-name" value="${esc(plan?.name ?? "")}" /></label>
    ${areas
      .map(
        (a) => `<label class="check"><input type="checkbox" data-area="${a.properties.id}"
          ${plan?.area_ids.includes(a.properties.id!) ? "checked" : ""}/> ${esc(a.properties.name)}</label>`,
      )
      .join("")}
    <div class="row">
      <button id="plan-save" class="primary">Save to robot</button>
      ${plan ? `<button id="plan-delete" class="danger">Delete</button>` : ""}
      <button id="plan-cancel">Cancel</button>
    </div>
    ${plan ? `<p class="hint">Deleting a plan also deletes its schedules.</p>` : ""}`;
  const close = () => {
    box.hidden = true;
    box.innerHTML = "";
  };
  $("plan-cancel").addEventListener("click", close);
  $("plan-save").addEventListener("click", async () => {
    const name = ($("plan-name") as HTMLInputElement).value.trim();
    const ids = [...box.querySelectorAll<HTMLInputElement>("input[data-area]:checked")].map((i) => Number(i.dataset.area));
    if (!name || !ids.length) return say("A plan needs a name and at least one area.", "error");
    try {
      if (plan) await api.updatePlan(plan.id, name, ids);
      else await api.createPlan(name, ids);
      close();
      say("Plan saved.", "ok");
      await refreshPlans();
    } catch (err) {
      say(message(err), "error");
    }
  });
  $("plan-delete")?.addEventListener("click", async (e) => {
    const button = e.currentTarget as HTMLButtonElement;
    if (!button.classList.contains("confirm")) {
      button.classList.add("confirm");
      button.textContent = "Confirm delete";
      return;
    }
    try {
      await api.deletePlan(plan!.id);
      close();
      say("Plan deleted.", "ok");
      await refreshPlans();
    } catch (err) {
      say(message(err), "error");
    }
  });
}

// -- status

async function pollStatus() {
  try {
    status = await api.status();
  } catch {
    status = null;
  }
  const pill = $("status");
  if (!status) {
    pill.textContent = "backend offline";
    pill.className = "pill bad";
  } else {
    const parts = [status.source.toUpperCase()];
    if (status.activity) parts.push(status.activity);
    if (status.battery != null) parts.push(`${status.battery}%`);
    pill.textContent = parts.join(" · ");
    pill.className = `pill ${!status.connected ? "bad" : status.writable ? "ok" : "ro"}`;
    pill.title = status.writable ? "Connected: edits can be sent" : "Read-only";
    $("demo-banner").hidden = status.source !== "sim";
    setRunActive(!!status.connected && !!status.busy);
    const robot = map.getSource("robot") as GeoJSONSource | undefined;
    const pos = status.position;
    robot?.setData(
      pos
        ? {
            type: "FeatureCollection",
            features: [
              {
                type: "Feature",
                geometry: shiftGeometry({ type: "Point", coordinates: [pos.lon, pos.lat] }, currentOffset()),
                properties: {},
              },
            ],
          }
        : empty,
    );
  }
  updateApplyButton();
  window.setTimeout(pollStatus, 4000);
}

// -- layers, labels, controls

const hidden = new Set<Family>();

function buildLayerToggles() {
  const families: Family[] = [...EDITABLE, ...STATIC_FAMILIES];
  $("layers").innerHTML =
    families
      .map(
        (f) => `<label><input type="checkbox" data-layer="${f}" checked /> <i class="sw ${f}"></i>${esc(familyLabel(f))}</label>`,
      )
      .join("") + `<label><input type="checkbox" data-layer="labels" checked /> Labels</label>`;
  $("layers").querySelectorAll<HTMLInputElement>("input[data-layer]").forEach((input) =>
    input.addEventListener("change", () => {
      const layer = input.dataset.layer!;
      if (layer === "labels") {
        map.setLayoutProperty("labels", "visibility", input.checked ? "visible" : "none");
        return;
      }
      const fam = layer as Family;
      if (input.checked) hidden.delete(fam);
      else hidden.add(fam);
      if (EDITABLE.includes(fam)) editor.setHidden(fam, !input.checked);
      applyOffset();
    }),
  );
}

function updateLabels() {
  const offset = currentOffset();
  const features: GeoJSON.Feature[] = editor
    .features()
    .filter((f) => !hidden.has(f.properties.family) && f.properties.name)
    .map((f) => ({
      type: "Feature",
      geometry: shiftGeometry({ type: "Point", coordinates: labelPoint(f) }, offset),
      properties: { name: f.properties.name },
    }));
  (map.getSource("labels") as GeoJSONSource | undefined)?.setData({ type: "FeatureCollection", features });
}

function labelPoint(f: MapFeature): GeoJSON.Position {
  const g = f.geometry;
  const pts = g.type === "Polygon" ? g.coordinates[0].slice(0, -1) : g.type === "LineString" ? g.coordinates : [g.coordinates];
  if (g.type === "LineString") return pts[Math.floor(pts.length / 2)];
  const n = pts.length || 1;
  return [pts.reduce((s, p) => s + p[0], 0) / n, pts.reduce((s, p) => s + p[1], 0) / n];
}

function wireControls() {
  const toolButtons = $("tools").querySelectorAll<HTMLButtonElement>("button[data-tool]");
  const setTool = (tool: Tool) => {
    editor.setTool(tool);
    toolButtons.forEach((b) => b.classList.toggle("active", b.dataset.tool === tool));
    $("tool-hint").textContent =
      tool === "select"
        ? "Click a shape to select it. Drag its corners; drag a midpoint to add one; right-click a corner to delete it."
        : tool === "areas"
          ? "Click to add corners; click the first corner to finish. New areas can be exported but not sent: the robot maps areas by driving them."
          : POLYGON_TOOLS.includes(tool)
            ? "Click to add corners; click the first corner to finish. Esc cancels."
            : "Click to add points; click the last point again to finish. Start a pathway on the dock to link it.";
  };
  toolButtons.forEach((b) => b.addEventListener("click", () => setTool(b.dataset.tool as Tool)));
  setTool("select");

  $("run-on").addEventListener("change", () => refreshRun());
  $("heat-on").addEventListener("change", () => loadHeat());
  $("heat-days").addEventListener("change", () => loadHeat());
  $("heat-refresh").addEventListener("click", () => loadHeat(true));
  $("undo").addEventListener("click", () => editor.undo());
  $("redo").addEventListener("click", () => editor.redo());
  $("discard-all").addEventListener("click", (e) => {
    const button = e.currentTarget as HTMLButtonElement;
    if (!button.classList.contains("confirm")) {
      button.classList.add("confirm");
      button.textContent = `Discard ${ops.length}?`;
      window.setTimeout(() => {
        button.classList.remove("confirm");
        button.textContent = "Discard all";
      }, 3000);
      return;
    }
    button.classList.remove("confirm");
    button.textContent = "Discard all";
    editor.revertAll();
    say("All unsent changes discarded.");
  });
  $("reload").addEventListener("click", async () => {
    await reload(false);
    await refreshPlans();
  });
  $("fit").addEventListener("click", fitToMap);
  $("apply").addEventListener("click", apply);
  $("export").addEventListener("click", exportGeoJSON);
  $("import").addEventListener("change", importGeoJSON);
  document.querySelectorAll<HTMLButtonElement>("button[data-nudge]").forEach((b) =>
    b.addEventListener("click", () => {
      const v = b.dataset.nudge!;
      if (v === "reset") offsetMetres = [0, 0];
      else {
        const [e, n] = v.split(",").map(Number);
        offsetMetres = [offsetMetres[0] + e * NUDGE_M, offsetMetres[1] + n * NUDGE_M];
      }
      applyOffset();
    }),
  );
  // Arrows move a selected no-go zone. Caught before the map sees them (it would pan).
  window.addEventListener(
    "keydown",
    (e) => {
      if (!e.key.startsWith("Arrow") || e.metaKey || e.ctrlKey || e.altKey) return;
      if (e.target instanceof HTMLInputElement || e.target instanceof HTMLSelectElement) return;
      if (!selectedId || editor.feature(selectedId)?.properties.family !== "nogozones") return;
      e.preventDefault();
      e.stopPropagation();
      const times = e.shiftKey ? 5 : 1;
      const [east, north] = { ArrowUp: [0, 1], ArrowDown: [0, -1], ArrowLeft: [-1, 0], ArrowRight: [1, 0] }[e.key] ?? [0, 0];
      moveSelected(east * times, north * times);
    },
    true,
  );
  document.addEventListener("keydown", (e) => {
    const typing = e.target instanceof HTMLInputElement || e.target instanceof HTMLSelectElement;
    if (typing) return;
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "z") {
      e.preventDefault();
      if (e.shiftKey) editor.redo();
      else editor.undo();
    } else if (e.key === "s") setTool("select");
    else if (e.key === "n") setTool("nogozones");
    else if (e.key === "p") setTool("pathways");
    else if ((e.key === "Delete" || e.key === "Backspace") && selectedId) {
      editor.remove(selectedId);
      selectedId = null;
      renderSelection();
    }
  });
}

const POLYGON_TOOLS: Tool[] = ["nogozones", "areas"];

function exportGeoJSON() {
  const blob = new Blob([JSON.stringify(editor.collection(), null, 2)], { type: "application/geo+json" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = `yarbo-map-${new Date().toISOString().slice(0, 19).replace(/[:T]/g, "-")}.geojson`;
  a.click();
  URL.revokeObjectURL(a.href);
}

async function importGeoJSON(e: Event) {
  const input = e.target as HTMLInputElement;
  const file = input.files?.[0];
  input.value = "";
  if (!file) return;
  try {
    const data = JSON.parse(await file.text());
    const features = (data.features ?? []) as MapFeature[];
    const usable = features.filter(
      (f) =>
        f && typeof f.id === "string" && EDITABLE.includes(f.properties?.family) &&
        (f.geometry?.type === "Polygon" || f.geometry?.type === "LineString"),
    );
    if (!usable.length) throw new Error("no yarbie map objects in that file");
    editor.replaceAll(usable);
    say(`Imported ${usable.length} objects. Review the changes before applying.`, "ok");
  } catch (err) {
    say(`Import failed: ${message(err)}`, "error");
  }
}

// -- helpers

function familyLabel(f: Family): string {
  return (
    {
      areas: "Area",
      nogozones: "No-go zone",
      novisionzones: "No-vision zone",
      elec_fence: "Virtual fence",
      pathways: "Pathway",
      sidewalks: "Sidewalk",
      deadends: "Dead end",
      dock: "Dock",
    } as Record<Family, string>
  )[f];
}

function message(err: unknown): string {
  if (err instanceof ApiError) return err.message;
  return err instanceof Error ? err.message : String(err);
}

function say(text: string, kind: "ok" | "error" | "" = "") {
  const footer = $("message");
  footer.textContent = text;
  footer.className = kind;
}
