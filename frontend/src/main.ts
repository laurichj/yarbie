import { api, ApiError, type Family, type Imagery, type MapFeature, type MapResponse, type Op, type Smoothed, type Smoothing, type Status } from "./api";
import { Editor, EDITABLE, type Tool } from "./editor";
import { debugLog, installDebug } from "./debug";
import { PATTERNS } from "./areaSettings";
import { bounds, circle, circleFit, metresToOffset, shiftGeometry, type Offset } from "./geo";
import { changes, choices, controls, DEFAULT_SMOOTHING, readControls, SMOOTHABLE, summary, tolerance } from "./smoothing";
import {
  addLabelLayer,
  addRobotLayer,
  addShapeLayers,
  coloured,
  createMap,
  empty,
  labels,
  robot,
  setData,
  YardImagery,
} from "./yardMap";

// What the editor shows but cannot edit: drawn as they are.
const STATIC_FAMILIES: Family[] = ["novisionzones", "elec_fence", "deadends", "dock"];
const NUDGE_M = 0.25;

const $ = <T extends HTMLElement = HTMLElement>(id: string) => document.getElementById(id) as T;
const esc = (s: string) =>
  s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]!);

// -- state

let loaded: MapResponse | null = null;
let status: Status | null = null;
let ops: Op[] = [];
let selectedId: string | null = null;
let armedApply = false;
// Where the robot's settings, runs and route previews are now: the Yarbo dashboard.
let dashboard: { path: string | null; robots: number; device: string | null } = { path: null, robots: 1, device: null };
// Smoothed outlines on show, not yet taken as edits; and those ticked in "Smooth all".
let smoothPending: Smoothed[] = [];
let smoothFrom = new Map<string, string>(); // the outline each was worked out from
// Bumped by every request and every clear, so a reply that comes back after the selection
// changed, the panel closed or a newer request went out is dropped.
let smoothAsked = 0;
const smoothTicked = new Set<string>();
let smoothAllOpen = false;

// -- map

const map = createMap($("map"));
const imagery = new YardImagery(map);
installDebug($("map"));

let editor: Editor;

map.on("load", async () => {
  addShapeLayers(map);
  addRobotLayer(map);
  map.addSource("problems", { type: "geojson", data: empty });
  map.addSource("smooth", { type: "geojson", data: empty });
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
  // A smoothed outline being previewed, over the one it would replace.
  map.addLayer({
    id: "smooth-line",
    type: "line",
    source: "smooth",
    filter: ["==", ["geometry-type"], "LineString"],
    paint: { "line-color": "#f2cc60", "line-width": 2.5, "line-dasharray": [2, 1] },
  });
  map.addLayer({
    id: "smooth-points",
    type: "circle",
    source: "smooth",
    filter: ["==", ["geometry-type"], "Point"],
    minzoom: 18,
    paint: { "circle-radius": 3, "circle-color": "#f2cc60", "circle-stroke-color": "#0d1117", "circle-stroke-width": 1 },
  });
  addLabelLayer(map);

  editor = new Editor(map);
  if (import.meta.env.DEV) Object.assign(window, { yarbie: { map, editor } }); // for local debugging
  editor.onChange(onEdit);
  editor.onSelect((id) => {
    selectedId = id;
    if (!smoothAllOpen) clearSmooth();
    renderSelection();
  });
  buildLayerToggles();
  fillImageryPicker();
  $("imagery").addEventListener("change", () => setImagery(($("imagery") as HTMLSelectElement).value, true));
  wireControls();
  await reload(true);
  void linkDashboard();
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
  imagery.lat = loaded.reference?.[0] ?? 0;
  await addKeyedImagery();
  setImagery(imagery.preferred(loaded.reference));
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
  return coloured(
    [...loaded.features.features, ...locked].filter(
      (f) =>
        (STATIC_FAMILIES.includes(f.properties.family) || locked.includes(f)) &&
        !hidden.has(f.properties.family),
    ),
    offset,
  );
}

function fitToMap() {
  const box = loaded && bounds(loaded.features.features);
  if (box) map.fitBounds(box, { padding: 60, maxZoom: 21, duration: 0 });
}

// -- imagery and its alignment (display only; yardMap.ts keeps both for the yard card too)

function currentOffset(): Offset {
  return imagery.offset();
}

function applyOffset() {
  editor.setOffset(currentOffset());
  setData(map, "static", staticFeatures());
  updateLabels();
  drawProblems();
  drawSmooth();
  drawRobot();
  $("offset").textContent = `${imagery.metres[0].toFixed(2)} m E, ${imagery.metres[1].toFixed(2)} m N`;
}

function fillImageryPicker() {
  ($("imagery") as HTMLSelectElement).innerHTML = imagery.sources
    .map((i) => `<option value="${i.id}" ${i.id === imagery.id ? "selected" : ""}>${esc(i.label)}</option>`)
    .join("");
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
    option.textContent = extra.error ? "Google satellite (unavailable, see message)" : "Google satellite (add a key in the yarbie options)";
    $("imagery").prepend(option);
    if (extra.error) say(extra.error, "error");
  }
  for (const source of extra.sources) {
    if (imagery.sources.some((i) => i.id === source.id)) continue;
    imagery.add(source);
    const option = document.createElement("option");
    option.value = source.id;
    option.textContent = source.label;
    $("imagery").prepend(option);
  }
  ($("imagery") as HTMLSelectElement).value = imagery.id;
}

function setImagery(id: string, remember = false) {
  debugLog("imagery", {
    id,
    remember,
    layers: map.getStyle().layers.map((l) => `${l.id}:${l.layout?.visibility ?? "visible"}`),
  });
  imagery.show(id, remember);
  ($("imagery") as HTMLSelectElement).value = id;
  applyOffset();
}

// -- edits and the change list

let previewTimer: number | undefined;

function onEdit() {
  ($("undo") as HTMLButtonElement).disabled = !editor.canUndo();
  ($("redo") as HTMLButtonElement).disabled = !editor.canRedo();
  disarmApply();
  updateLabels();
  dropStaleSmooth();
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
  setData(map, "problems", features);
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
    // The integration says why (untested firmware, observe only, writes turned off).
    hint.textContent = !status?.connected
      ? "Not connected to the robot."
      : status.error
        ? `Read-only: ${status.error}.`
        : "Read-only.";
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
    if (result.reload) {
      // What was sent is on the robot; what the editor holds is not the robot's map now.
      say(`Sent ${result.applied.length} change(s), but ${result.reload}.`, "error");
      try {
        await reload(false);
      } catch {
        // the message above already says to reload
      }
    }
    await refreshHistory();
  } catch (err) {
    say(message(err), "error");
    updateApplyButton();
  }
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
    // Backups of area settings are kept beside the map's, but are not maps to revert to.
    backups = (await api.backups()).filter((b) => b.kind === "map");
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
    ${p.family === "areas" && !isNew ? areaSettingsLink(p.id!, p.algorithm_type) : ""}
    ${smoothable(feature) && !smoothAllOpen ? smoothFieldset(feature) : ""}
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
  `;
  const id = feature.id;
  ($("sel-name") as HTMLInputElement).addEventListener("change", (e) =>
    editor.updateProperties(id, { name: (e.target as HTMLInputElement).value.trim() }),
  );
  $("sel-enabled")?.addEventListener("change", (e) =>
    editor.updateProperties(id, { enabled: (e.target as HTMLInputElement).checked }),
  );
  if (smoothable(feature) && !smoothAllOpen) wireSmoothFieldset(feature);
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
  body.querySelectorAll<HTMLAnchorElement>("a[data-dashboard]").forEach((a) => wireDashboardLink(a));
}

/** An area's mowing pattern and settings are robot settings, set on the dashboard. */
function areaSettingsLink(areaId: number, pattern: number | null | undefined): string {
  const name = PATTERNS.find(([v]) => v === pattern)?.[1] ?? (pattern == null ? "unknown" : `type ${pattern}`);
  const link = dashboardUrl("area-settings", { area: areaId })
    ? `<a href="#" data-dashboard="area-settings" data-area="${areaId}">Mowing settings for this area</a>`
    : "Mowing settings: on the Yarbo dashboard's Area settings view";
  return `<p class="facts">Mowing pattern: ${esc(name)}</p><p class="hint">${link}</p>`;
}

// -- smoothing: proposed outlines shown over the map until taken as edits or dropped

function smoothing(): Smoothing {
  return status?.smoothing ?? DEFAULT_SMOOTHING;
}

function smoothable(feature: MapFeature): boolean {
  return SMOOTHABLE.includes(feature.properties.family) && feature.geometry.type === "Polygon";
}

function drawSmooth() {
  const offset = currentOffset();
  const features: GeoJSON.Feature[] = [];
  for (const r of smoothPending.filter(changes)) {
    const ring = shiftGeometry(r.geometry, offset).coordinates[0];
    features.push({ type: "Feature", properties: {}, geometry: { type: "LineString", coordinates: ring } });
    for (const c of ring.slice(0, -1)) features.push({ type: "Feature", properties: {}, geometry: { type: "Point", coordinates: c } });
  }
  setData(map, "smooth", features);
  // Over the editor's own layers, which it adds after these: the two outlines are within
  // inches of each other.
  if (features.length && map.getLayer("smooth-line")) {
    map.moveLayer("smooth-line");
    map.moveLayer("smooth-points");
  }
}

function clearSmooth() {
  smoothAsked++;
  if (!smoothPending.length) return;
  smoothPending = [];
  smoothTicked.clear();
  drawSmooth();
}

/** Drop previews of outlines edited since: they were worked out from the old shape. */
function dropStaleSmooth() {
  const fresh = smoothPending.filter((r) => JSON.stringify(editor.feature(r.id)?.geometry) === smoothFrom.get(r.id));
  if (fresh.length === smoothPending.length) return;
  smoothPending = fresh;
  for (const id of [...smoothTicked]) if (!fresh.some((r) => r.id === id)) smoothTicked.delete(id);
  drawSmooth();
  if (smoothAllOpen) renderSmoothAll();
}

/** Ask for smoothed outlines; false if it failed or the answer is no longer wanted. */
async function proposeSmooth(ids: string[]): Promise<boolean> {
  const asked = ++smoothAsked;
  // The outlines as sent, not as they are when the answer comes: an outline dragged in
  // the meantime must not look as if this preview was worked out from it.
  const sent = editor.collection();
  const from = new Map(sent.features.map((f) => [f.id, JSON.stringify(f.geometry)]));
  try {
    const { results } = await api.smooth(sent, ids, tolerance(smoothing()), choices.arcs, choices.keepShared);
    if (asked !== smoothAsked) return false;
    smoothPending = results;
    smoothFrom = from;
    drawSmooth();
    dropStaleSmooth();
    return true;
  } catch (err) {
    say(`Could not smooth: ${message(err)}`, "error");
    return false;
  }
}

/** Take smoothed outlines as edits (one undo step). How many changed. */
function useSmoothed(results: Smoothed[]): number {
  const geometries = new Map<string, MapFeature["geometry"]>(results.filter(changes).map((r) => [r.id, r.geometry]));
  clearSmooth();
  if (geometries.size) editor.setGeometries(geometries);
  return geometries.size;
}

function smoothFieldset(feature: MapFeature): string {
  const family = feature.properties.family;
  const s = smoothing();
  const pending = smoothPending.find((r) => r.id === feature.id);
  return `<fieldset><legend>Smooth outline</legend>
    <p class="hint" style="margin-top:0">Fewer points and rounder curves. ${
      family === "areas" ? "The area only gets smaller." : "The zone only gets bigger."
    }</p>
    ${controls("sm", s, family === "areas")}
    <div class="row"><button id="sm-preview">${pending ? "Preview again" : "Preview"}</button></div>
    ${
      pending
        ? `<p class="facts" style="margin-top:8px">${esc(summary(pending, family, s))}</p>
          <div class="row"><button id="sm-use" class="primary" ${changes(pending) ? "" : "disabled"}>Use smoothed outline</button>
          <button id="sm-discard">Discard</button></div>`
        : ""
    }</fieldset>`;
}

function wireSmoothFieldset(feature: MapFeature) {
  const id = feature.id;
  const name = feature.properties.name || "the outline";
  $("sm-preview")?.addEventListener("click", async (e) => {
    if (!readControls("sm", smoothing())) return say(`Give how far a line may move, in ${smoothing().unit}.`, "error");
    const button = e.currentTarget as HTMLButtonElement;
    button.disabled = true;
    button.textContent = "Working…";
    await proposeSmooth([id]);
    renderSelection();
  });
  $("sm-use")?.addEventListener("click", () => {
    const r = smoothPending.find((x) => x.id === id);
    if (!r) return;
    const text = summary(r, feature.properties.family, smoothing());
    useSmoothed([r]);
    say(`Smoothed ${name}: ${text}. It is a change to review and apply like any other.`, "ok");
  });
  $("sm-discard")?.addEventListener("click", () => {
    clearSmooth();
    renderSelection();
  });
}

function renderSmoothAll() {
  const box = $("smooth-all");
  box.hidden = !smoothAllOpen;
  ($("smooth-all-open") as HTMLButtonElement).classList.toggle("active", smoothAllOpen);
  if (!smoothAllOpen) return;
  const s = smoothing();
  const shown = smoothPending.filter(changes);
  const family = (id: string) => editor.feature(id)?.properties.family ?? "areas";
  const rows = shown
    .map((r) => {
      const f = editor.feature(r.id);
      return `<li><label class="check" style="align-items:flex-start;margin:0">
        <input type="checkbox" data-smooth="${esc(r.id)}" ${smoothTicked.has(r.id) ? "checked" : ""}/>
        <span><a href="#" data-smooth-show="${esc(r.id)}"><i class="sw ${family(r.id)}"></i>${esc(f?.properties.name || r.id)}</a>
        <br><span class="hint">${esc(summary(r, family(r.id), s))}</span></span></label></li>`;
    })
    .join("");
  const total = (fam: Family) => {
    const of = smoothPending.filter((r) => family(r.id) === fam);
    const before = of.reduce((n, r) => n + r.stats.before, 0);
    const after = of.reduce((n, r) => n + r.stats.after, 0);
    return of.length ? `${fam === "areas" ? "Areas" : "No-go zones"}: ${before.toLocaleString()} → ${after.toLocaleString()} points` : "";
  };
  box.innerHTML = `<fieldset><legend>Smooth every area and no-go zone</legend>
    <p class="hint" style="margin-top:0">Areas only get smaller and no-go zones only bigger. Nothing goes to the robot until you apply the changes.</p>
    ${controls("sa", s, true)}
    <div class="row"><button id="sa-preview">${smoothPending.length ? "Preview again" : "Preview"}</button><button id="sa-close">Close</button></div>
    ${
      smoothPending.length
        ? `<p class="facts" style="margin-top:8px">${[total("areas"), total("nogozones")].filter(Boolean).join(" · ")}${
            shown.length < smoothPending.length ? ` · ${smoothPending.length - shown.length} already as simple as this allows` : ""
          }</p>
          <ul class="plans">${rows}</ul>
          <div class="row"><button id="sa-use" class="primary" ${smoothTicked.size ? "" : "disabled"}>Use ticked (${smoothTicked.size})</button></div>`
        : ""
    }</fieldset>`;
  $("sa-preview").addEventListener("click", async (e) => {
    if (!readControls("sa", s)) return say(`Give how far a line may move, in ${s.unit}.`, "error");
    const button = e.currentTarget as HTMLButtonElement;
    button.disabled = true;
    button.textContent = "Working…";
    const ids = editor.features().filter(smoothable).map((f) => f.id);
    if (await proposeSmooth(ids)) {
      smoothTicked.clear();
      for (const r of smoothPending.filter(changes)) smoothTicked.add(r.id);
    }
    renderSmoothAll();
  });
  $("sa-close").addEventListener("click", () => {
    smoothAllOpen = false;
    clearSmooth();
    renderSmoothAll();
    if (selectedId) renderSelection(); // its own Smooth outline is back
  });
  box.querySelectorAll<HTMLInputElement>("input[data-smooth]").forEach((input) =>
    input.addEventListener("change", () => {
      if (input.checked) smoothTicked.add(input.dataset.smooth!);
      else smoothTicked.delete(input.dataset.smooth!);
      const use = $("sa-use") as HTMLButtonElement;
      use.disabled = !smoothTicked.size;
      use.textContent = `Use ticked (${smoothTicked.size})`;
    }),
  );
  box.querySelectorAll<HTMLAnchorElement>("a[data-smooth-show]").forEach((a) =>
    a.addEventListener("click", (e) => {
      e.preventDefault();
      const f = editor.feature(a.dataset.smoothShow!);
      const extent = f ? bounds([{ geometry: shiftGeometry(f.geometry, currentOffset()) }]) : null;
      if (extent) map.fitBounds(extent, { padding: 60, maxZoom: 21 });
    }),
  );
  $("sa-use")?.addEventListener("click", () => {
    const n = useSmoothed(smoothPending.filter((r) => smoothTicked.has(r.id)));
    smoothAllOpen = false;
    renderSmoothAll();
    if (selectedId) renderSelection();
    say(`Smoothed ${n} outline${n === 1 ? "" : "s"}. They are changes to review and apply like any other; Undo takes them all back.`, "ok");
  });
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

// -- the Yarbo dashboard: where mowing settings, runs, route previews and wear are

/** Find the Yarbo dashboard once, and which of its views are this robot's. */
async function linkDashboard() {
  try {
    const [path, info] = await Promise.all([api.dashboard(), api.info()]);
    dashboard = { path, robots: api.robots(), device: info.device_id ?? null };
  } catch {
    // listing dashboards failed; say where to add one instead
  }
  const hint = $("dashboard-hint");
  hint.innerHTML = dashboard.path
    ? `Mowing settings, plans, runs, route previews and the wear map are on the
       <a href="#" data-dashboard="yard">Yarbo dashboard</a>; every area's settings side by side in its
       <a href="#" data-dashboard="area-settings">Area settings</a> view.`
    : `Mowing settings, plans, runs, route previews and the wear map are on the Yarbo dashboard. Add it under
       Settings, Dashboards, Add dashboard, Yarbo.`;
  hint.querySelectorAll<HTMLAnchorElement>("a[data-dashboard]").forEach((a) => wireDashboardLink(a));
  if (selectedId) renderSelection();
}

/** The dashboard view (the strategy's "yard" or "area-settings"), with a query to select
 * something on its cards; null when there is no Yarbo dashboard. */
function dashboardUrl(view: "yard" | "area-settings", query: Record<string, string | number> = {}): string | null {
  if (!dashboard.path) return null;
  // With several robots the strategy names each robot's views by its device.
  const name =
    dashboard.robots > 1 && dashboard.device ? (view === "yard" ? dashboard.device : `${dashboard.device}-${view}`) : view;
  const search = new URLSearchParams(Object.entries(query).map(([k, v]) => [k, String(v)])).toString();
  return `${dashboard.path}/${name}${search ? `?${search}` : ""}`;
}

/** Links marked data-dashboard (and data-area) move Home Assistant itself: the panel is
 * an iframe. */
function wireDashboardLink(a: HTMLAnchorElement) {
  const view = a.dataset.dashboard as "yard" | "area-settings";
  const url = dashboardUrl(view, a.dataset.area ? { area: a.dataset.area } : {});
  if (!url) return;
  a.href = url;
  a.addEventListener("click", (e) => {
    e.preventDefault();
    const top = window.parent;
    top.history.pushState(null, "", url);
    top.dispatchEvent(new CustomEvent("location-changed", { detail: { replace: false } }));
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
    pill.textContent = "yarbie not answering";
    pill.className = "pill bad";
  } else {
    const parts = [status.source.toUpperCase()];
    if (status.activity) parts.push(status.activity);
    if (status.battery != null) parts.push(`${status.battery}%`);
    pill.textContent = parts.join(" · ");
    pill.className = `pill ${!status.connected ? "bad" : status.writable ? "ok" : "ro"}`;
    pill.title = status.writable ? "Connected: edits can be sent" : "Read-only";
    $("demo-banner").hidden = status.source !== "sim";
    drawRobot();
  }
  updateApplyButton();
  window.setTimeout(pollStatus, 4000);
}

function drawRobot() {
  setData(map, "robot", robot(status?.position, currentOffset()));
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
  setData(map, "labels", labels(editor.features().filter((f) => !hidden.has(f.properties.family)), currentOffset()));
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
  $("reload").addEventListener("click", () => void reload(false));
  $("fit").addEventListener("click", fitToMap);
  $("smooth-all-open").addEventListener("click", () => {
    smoothAllOpen = !smoothAllOpen;
    clearSmooth();
    renderSmoothAll();
    if (selectedId) renderSelection();
  });
  $("apply").addEventListener("click", apply);
  $("export").addEventListener("click", exportGeoJSON);
  $("import").addEventListener("change", importGeoJSON);
  document.querySelectorAll<HTMLButtonElement>("button[data-nudge]").forEach((b) =>
    b.addEventListener("click", () => {
      const v = b.dataset.nudge!;
      if (v === "reset") imagery.nudge([0, 0]);
      else {
        const [e, n] = v.split(",").map(Number);
        imagery.nudge([imagery.metres[0] + e * NUDGE_M, imagery.metres[1] + n * NUDGE_M]);
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
