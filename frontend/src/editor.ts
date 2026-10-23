// Drawing and editing on the map with Terra Draw. The editor holds the working copy of the
// map; nothing reaches the robot until the user previews and applies the changes.

import type { Map as MapLibreMap } from "maplibre-gl";
import {
  TerraDraw,
  TerraDrawLineStringMode,
  TerraDrawPolygonMode,
  TerraDrawRenderMode,
  TerraDrawSelectMode,
  ValidateNotSelfIntersecting,
  type GeoJSONStoreFeatures,
} from "terra-draw";
import { TerraDrawMapLibreGLAdapter } from "terra-draw-maplibre-gl-adapter";
import type { Family, MapCollection, MapFeature } from "./api";
import { debugLog } from "./debug";
import { shiftGeometry, type Offset } from "./geo";
import { COLORS } from "./yardMap";

export const EDITABLE: Family[] = ["areas", "nogozones", "pathways", "sidewalks"];
const POLYGONS: Family[] = ["areas", "nogozones"];


export type Tool = "select" | "nogozones" | "pathways" | "sidewalks" | "areas";

type Listener = () => void;

function family(feature: GeoJSONStoreFeatures): Family {
  return (feature.properties.family as Family | undefined) ?? "nogozones";
}

const color = (f: GeoJSONStoreFeatures) => COLORS[family(f)];

export class Editor {
  readonly draw: TerraDraw;
  private history: MapFeature[][] = [];
  private future: MapFeature[][] = []; // states undone, for redo
  private original = new Map<string, MapFeature>(); // as the robot has them
  private pendingFamily: Family | null = null;
  private listeners: { change: Listener[]; select: ((id: string | null) => void)[] } = {
    change: [],
    select: [],
  };
  private hidden = new Set<Family>();
  private all: MapFeature[] = []; // robot coordinates, without the display offset
  private offset: Offset = [0, 0];
  // Shapes from the robot are loaded as they are; only new drawing is validated. Driven
  // area outlines often cross themselves slightly, and must never be dropped for it.
  private loading = false;
  // Anything that still fails to draw is kept unchanged and never read back as deleted.
  private undrawn = new Set<string>();

  constructor(private readonly map: MapLibreMap) {
    const editableFlags = {
      feature: {
        draggable: false,
        // Terra Draw silently refuses any corner move that leaves the outline crossing
        // itself, so outlines the robot drove that already cross could not be edited at
        // all. The change list warns when an edit adds a crossing instead.
        selfIntersectable: true,
        coordinates: {
          draggable: true,
          deletable: true,
          midpoints: { draggable: true },
          snappable: { toCoordinate: true },
        },
      },
    };
    this.draw = new TerraDraw({
      adapter: new TerraDrawMapLibreGLAdapter({ map }),
      idStrategy: {
        isValidId: (id) => typeof id === "string" && id.length > 0,
        getId: () => `new:${crypto.randomUUID()}`,
      },
      modes: [
        new TerraDrawPolygonMode({
          snapping: { toCoordinate: true },
          // Only shapes being drawn are checked. Edits to an existing shape are not: driven
          // outlines often already cross themselves, and blocking every drag on them would
          // make them uneditable. The change list still warns about new crossings.
          validation: (f) =>
            this.loading || this.draw.getMode() !== "polygon" ? { valid: true } : ValidateNotSelfIntersecting(f),
          styles: {
            fillColor: color,
            fillOpacity: (f) => (family(f) === "areas" ? 0.18 : 0.35),
            outlineColor: color,
            outlineWidth: 2,
          },
        }),
        new TerraDrawLineStringMode({
          snapping: { toCoordinate: true },
          styles: { lineStringColor: color, lineStringWidth: 4 },
        }),
        new TerraDrawSelectMode({
          // Clicks are resolved by pick() instead: Terra Draw would hand overlapping
          // polygons to whichever its index lists last, often the area round a no-go zone.
          allowManualSelection: false,
          flags: { polygon: editableFlags, linestring: editableFlags },
          styles: {
            selectedPolygonColor: color,
            selectedPolygonFillOpacity: 0.45,
            selectedPolygonOutlineColor: "#ffffff",
            selectedPolygonOutlineWidth: 3,
            selectedLineStringColor: "#ffffff",
            selectedLineStringWidth: 5,
            selectionPointColor: "#ffffff",
            selectionPointOutlineColor: "#000000",
            midPointColor: "#ffffff",
            midPointOutlineColor: "#000000",
          },
        }),
        new TerraDrawRenderMode({ modeName: "static", styles: {} }),
      ],
    });
    this.draw.start();
    this.draw.on("finish", (id, context) => this.onFinish(String(id), context.mode, context.action));
    this.draw.on("select", (id) => this.listeners.select.forEach((cb) => cb(String(id))));
    this.draw.on("deselect", () => this.listeners.select.forEach((cb) => cb(null)));
    map.on("click", (e) => {
      const mode = this.draw.getMode();
      debugLog("map.click", {
        mode,
        point: [Math.round(e.point.x), Math.round(e.point.y)],
        zoom: map.getZoom().toFixed(2),
        shapes: this.draw.getSnapshot().filter((f) => f.properties.family).length,
        candidate: mode === "select" ? this.pick(e.point.x, e.point.y) : null,
      });
      if (mode !== "select") return;
      const id = this.pick(e.point.x, e.point.y);
      if (id && !this.draw.getSnapshotFeature(id)?.properties.selected) this.draw.selectFeature(id);
    });
  }

  /** The object under a click: a line within reach first, else the smallest shape
   * containing the point, so a no-go zone wins over the area around it. */
  private pick(x: number, y: number): string | null {
    let line: { id: string; d: number } | null = null;
    let shape: { id: string; size: number } | null = null;
    for (const f of this.draw.getSnapshot()) {
      if (!f.properties.family) continue; // selection handles, points being drawn
      const id = String(f.id);
      if (f.geometry.type === "LineString") {
        const px = f.geometry.coordinates.map(([lon, lat]) => this.map.project([lon, lat]));
        for (let i = 0; i < px.length - 1; i++) {
          const d = segmentDistance(x, y, px[i].x, px[i].y, px[i + 1].x, px[i + 1].y);
          if (d <= PICK_PX && (!line || d < line.d)) line = { id, d };
        }
      } else if (f.geometry.type === "Polygon") {
        const ring = f.geometry.coordinates[0].map(([lon, lat]) => this.map.project([lon, lat]));
        if (!insideRing(x, y, ring)) continue;
        const size = ringArea(ring);
        if (!shape || size < shape.size) shape = { id, size };
      }
    }
    return line?.id ?? shape?.id ?? null;
  }

  onChange(cb: Listener) {
    this.listeners.change.push(cb);
  }

  onSelect(cb: (id: string | null) => void) {
    this.listeners.select.push(cb);
  }

  // -- loading and reading the working copy

  load(collection: MapCollection) {
    this.all = collection.features.filter((f) => EDITABLE.includes(f.properties.family));
    this.original = new Map(this.all.map((f) => [f.id, structuredClone(f)]));
    this.render();
    this.history = [];
    this.future = [];
    this.checkpoint();
  }

  private render() {
    this.draw.clear();
    // Areas first, so they draw underneath no-go zones and lines.
    const visible = this.all
      .filter((f) => !this.hidden.has(f.properties.family))
      .sort((a, b) => Number(b.properties.family === "areas") - Number(a.properties.family === "areas"));
    this.loading = true;
    const results = this.draw.addFeatures(
      visible.map((f) => ({
        type: "Feature",
        id: f.id,
        geometry: shiftGeometry(f.geometry, this.offset),
        properties: {
          ...f.properties,
          mode: f.geometry.type === "Polygon" ? "polygon" : "linestring",
        },
      })) as GeoJSONStoreFeatures[],
    );
    this.loading = false;
    this.undrawn = new Set();
    for (const result of results) {
      if (!result.valid) {
        console.warn("not drawn:", result.id, result.reason);
        this.undrawn.add(String(result.id));
      }
    }
  }

  /** Objects that could not be shown for editing; they are kept exactly as loaded. */
  locked(): MapFeature[] {
    return this.all.filter((f) => this.undrawn.has(f.id));
  }

  /** The current working copy: edits plus anything hidden from view. */
  features(): MapFeature[] {
    this.sync();
    return this.all.map((f) => structuredClone(f));
  }

  collection(): MapCollection {
    return { type: "FeatureCollection", features: this.features() };
  }

  feature(id: string): MapFeature | undefined {
    this.sync();
    return this.all.find((f) => f.id === id);
  }

  /** Pull geometry and properties out of Terra Draw for the features it shows. */
  private sync() {
    const shown = new Map<string, GeoJSONStoreFeatures>();
    for (const f of this.draw.getSnapshot()) shown.set(String(f.id), f);
    const next: MapFeature[] = [];
    for (const f of this.all) {
      if (this.hidden.has(f.properties.family) || this.undrawn.has(f.id)) {
        next.push(f);
        continue;
      }
      const live = shown.get(f.id);
      if (!live) continue; // deleted on the map
      shown.delete(f.id);
      next.push({ ...f, geometry: this.unshift(live.geometry) });
    }
    for (const [id, live] of shown) {
      if (!live.properties.family) continue; // still being drawn
      next.push({ ...toMapFeature(id, live), geometry: this.unshift(live.geometry) });
    }
    this.all = next;
  }

  private unshift(geometry: GeoJSONStoreFeatures["geometry"]): MapFeature["geometry"] {
    return shiftGeometry(geometry as MapFeature["geometry"], this.offset, -1);
  }

  /** Move what is drawn (not the data) to line up with the imagery. */
  setOffset(offset: Offset) {
    this.sync();
    this.offset = offset;
    this.render();
  }

  // -- tools

  setTool(tool: Tool) {
    if (tool === "select") {
      this.pendingFamily = null;
      this.draw.setMode("select");
      return;
    }
    this.pendingFamily = tool;
    this.draw.setMode(POLYGONS.includes(tool) ? "polygon" : "linestring");
  }

  private onFinish(id: string, mode: string, action: string) {
    if ((mode === "polygon" || mode === "linestring") && action === "draw" && this.pendingFamily) {
      const fam = this.pendingFamily;
      const count = this.all.filter((f) => f.properties.family === fam).length + 1;
      this.draw.updateFeatureProperties(id, {
        family: fam,
        id: null,
        name: `${defaultName(fam)} ${count}`,
        enabled: true,
      });
    }
    this.checkpoint();
  }

  // -- edits from the side panel

  updateProperties(id: string, changes: Partial<MapFeature["properties"]>) {
    this.sync();
    const target = this.all.find((f) => f.id === id);
    if (!target) return;
    target.properties = { ...target.properties, ...changes };
    if (this.draw.hasFeature(id)) {
      this.draw.updateFeatureProperties(id, changes as Record<string, string | number | boolean>);
    }
    this.checkpoint();
  }

  remove(id: string) {
    this.sync();
    if (this.draw.hasFeature(id)) {
      this.draw.deselectFeature(id);
      this.draw.removeFeatures([id]);
    }
    this.all = this.all.filter((f) => f.id !== id);
    this.checkpoint();
  }

  /** Replace an object's outline (robot coordinates), e.g. with a circle. */
  setGeometry(id: string, geometry: MapFeature["geometry"]) {
    this.setGeometries(new Map([[id, geometry]]));
  }

  /** Replace several outlines at once, as one step to undo. */
  setGeometries(geometries: Map<string, MapFeature["geometry"]>) {
    this.sync();
    for (const [id, geometry] of geometries) {
      const target = this.all.find((f) => f.id === id);
      if (!target) continue;
      target.geometry = geometry;
      if (this.draw.hasFeature(id)) {
        this.draw.updateFeatureGeometry(id, shiftGeometry(geometry, this.offset) as GeoJSONStoreFeatures["geometry"]);
      }
    }
    this.checkpoint();
  }

  select(id: string) {
    this.draw.setMode("select");
    if (this.draw.hasFeature(id)) this.draw.selectFeature(id);
  }

  /** Replace the working copy, e.g. with an imported file. */
  replaceAll(features: MapFeature[]) {
    this.all = features.filter((f) => EDITABLE.includes(f.properties.family));
    this.render();
    this.checkpoint();
  }

  /** Put one object back the way the robot has it: a new one goes, an edited one
   * reverts, a deleted one returns. */
  revert(key: string) {
    this.sync();
    const original = this.original.get(key);
    const at = this.all.findIndex((f) => f.id === key);
    if (original && at >= 0) this.all[at] = structuredClone(original);
    else if (original) this.all.push(structuredClone(original));
    else if (at >= 0) this.all.splice(at, 1);
    this.render();
    this.checkpoint();
  }

  /** Throw away every edit not yet sent. */
  revertAll() {
    this.all = [...this.original.values()].map((f) => structuredClone(f));
    this.render();
    this.checkpoint();
  }

  setHidden(fam: Family, hidden: boolean) {
    this.sync();
    if (hidden) this.hidden.add(fam);
    else this.hidden.delete(fam);
    this.render();
  }

  // -- undo

  private checkpoint() {
    this.sync();
    const snapshot = this.all.map((f) => structuredClone(f));
    const last = this.history.at(-1);
    if (!last || JSON.stringify(last) !== JSON.stringify(snapshot)) {
      this.history.push(snapshot);
      this.future = [];
      if (this.history.length > 100) this.history.shift();
    }
    this.emit();
  }

  canUndo() {
    return this.history.length > 1;
  }

  canRedo() {
    return this.future.length > 0;
  }

  undo() {
    if (this.history.length < 2) return;
    this.future.push(this.history.pop()!);
    this.all = structuredClone(this.history.at(-1)!);
    this.render();
    this.emit();
  }

  redo() {
    const next = this.future.pop();
    if (!next) return;
    this.history.push(next);
    this.all = structuredClone(next);
    this.render();
    this.emit();
  }

  private emit() {
    this.listeners.change.forEach((cb) => cb());
  }
}

const PICK_PX = 8;

type Px = { x: number; y: number };

function segmentDistance(x: number, y: number, ax: number, ay: number, bx: number, by: number): number {
  const dx = bx - ax;
  const dy = by - ay;
  const len = dx * dx + dy * dy;
  const t = len === 0 ? 0 : Math.max(0, Math.min(1, ((x - ax) * dx + (y - ay) * dy) / len));
  return Math.hypot(x - (ax + t * dx), y - (ay + t * dy));
}

function insideRing(x: number, y: number, ring: Px[]): boolean {
  let inside = false;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const a = ring[i];
    const b = ring[j];
    if (a.y > y !== b.y > y && x < ((b.x - a.x) * (y - a.y)) / (b.y - a.y) + a.x) inside = !inside;
  }
  return inside;
}

function ringArea(ring: Px[]): number {
  let twice = 0;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    twice += ring[j].x * ring[i].y - ring[i].x * ring[j].y;
  }
  return Math.abs(twice) / 2;
}

function toMapFeature(id: string, live: GeoJSONStoreFeatures): MapFeature {
  const p = live.properties;
  return {
    type: "Feature",
    id,
    geometry: live.geometry as MapFeature["geometry"],
    properties: {
      family: p.family as Family,
      id: (p.id as number | null | undefined) ?? null,
      name: String(p.name ?? ""),
      enabled: p.enabled !== false,
      algorithm_type: (p.algorithm_type as number | null | undefined) ?? null,
    },
  };
}

export function defaultName(fam: Family): string {
  return (
    {
      areas: "Area",
      nogozones: "No-go zone",
      pathways: "Pathway",
      sidewalks: "Sidewalk",
    } as Partial<Record<Family, string>>
  )[fam] ?? fam;
}
