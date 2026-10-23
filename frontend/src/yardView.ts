// The yard card's map: the yard as the Yard editor draws it (yardMap.ts), with nothing to
// edit. Its own chunk, imported by yardCard.ts only once a yard card is on screen, so the
// cards bundle every page loads stays free of MapLibre.

import maplibreCss from "maplibre-gl/dist/maplibre-gl.css?inline";
import type { Heatmap, Imagery, MapResponse, Progress, RouteCollection } from "./api";
import { bounds, shiftGeometry } from "./geo";
import {
  addHeatLayer,
  addLabelLayer,
  addPastRunLayers,
  addRobotLayer,
  addRouteLayers,
  addRunLayers,
  addShapeLayers,
  coloured,
  createMap,
  drawHeat,
  drawRun,
  labels,
  robot,
  setData,
  showHeat,
  showRun,
  YardImagery,
} from "./yardMap";

export const MAP_CSS = maplibreCss;

type Position = { lon: number; lat: number } | null;

export class YardView {
  readonly map;
  readonly imagery;
  #loaded = false;
  #yard: MapResponse | null = null;
  #robot: Position = null;
  #run: Progress | null = null;
  #route: { route: RouteCollection; areas: number[] } | null = null;
  #heat: Heatmap | null = null;
  #past: Record<string, string> | null = null;
  #fitted = false;
  #imagery: Imagery[] = []; // keyed sources that came before the map had loaded

  constructor(container: HTMLElement) {
    this.map = createMap(container);
    this.imagery = new YardImagery(this.map);
    this.map.on("load", () => {
      // Bottom to top: wear, a past run, a route, the yard, the run now, the robot, names.
      addHeatLayer(this.map);
      addPastRunLayers(this.map);
      addRouteLayers(this.map);
      addShapeLayers(this.map, "yard", false);
      this.map.moveLayer("route-areas"); // a route's areas outlined over the yard's own
      addRunLayers(this.map);
      addRobotLayer(this.map);
      addLabelLayer(this.map);
      this.#loaded = true;
      for (const source of this.#imagery) this.imagery.add(source);
      this.#draw();
    });
  }

  /** Photo sources that need a key (Google): only admins are given them. */
  addImagery(sources: Imagery[]) {
    if (!this.#loaded) {
      this.#imagery.push(...sources); // added on load
      return;
    }
    for (const source of sources) this.imagery.add(source);
    if (this.#yard) this.#draw();
  }

  setYard(yard: MapResponse) {
    this.#yard = yard;
    this.#draw();
  }

  setRobot(position: Position) {
    this.#robot = position;
    if (this.#loaded) setData(this.map, "robot", robot(position, this.imagery.offset()));
  }

  setRun(run: Progress | null) {
    this.#run = run;
    this.#draw();
  }

  setRoute(route: RouteCollection | null, areas: number[] = []) {
    this.#route = route ? { route, areas } : null;
    this.#draw();
    if (route) this.#fitTo(route.features);
  }

  setHeat(heat: Heatmap | null) {
    this.#heat = heat;
    this.#draw();
  }

  /** A past run's areas by what was finished (done, edge, left), or none. */
  setPastRun(states: Record<string, string> | null) {
    this.#past = states;
    this.#draw();
    if (states) this.#fitTo(this.#areas((id) => id in states));
  }

  fit() {
    const box = this.#yard && bounds(this.#yard.features.features);
    if (box) this.#fitBox(box, 30);
  }

  resize() {
    this.map.resize();
  }

  remove() {
    this.map.remove();
  }

  #areas(keep: (id: string) => boolean) {
    return (this.#yard?.features.features ?? []).filter(
      (f) => f.properties.family === "areas" && f.properties.id !== null && keep(String(f.properties.id)),
    );
  }

  #fitTo(features: { geometry: GeoJSON.Geometry }[]) {
    const offset = this.imagery.offset();
    const box = bounds(features.map((f) => ({ geometry: shiftGeometry(f.geometry, offset) })));
    if (box) this.#fitBox(box, 40);
  }

  #fitBox(box: [number, number, number, number], padding: number) {
    this.map.fitBounds(box, { padding, maxZoom: 21, duration: this.#fitted ? 300 : 0 });
    this.#fitted = true;
  }

  /** Everything again, shifted by the current alignment. */
  #draw() {
    if (!this.#loaded || !this.#yard) return;
    const yard = this.#yard;
    this.imagery.lat = yard.reference?.[0] ?? 0;
    const want = this.imagery.preferred(yard.reference);
    if (want !== this.imagery.id || !this.#fitted) this.imagery.show(want);
    const offset = this.imagery.offset();
    setData(this.map, "yard", coloured(yard.features.features, offset));
    setData(this.map, "labels", labels(yard.features.features, offset));
    setData(this.map, "robot", robot(this.#robot, offset));
    drawRun(this.map, this.#run, offset, this.imagery.lat);
    showRun(this.map, this.#run !== null);
    drawHeat(this.map, this.#heat, offset);
    showHeat(this.map, this.#heat !== null);
    const route = this.#route;
    setData(
      this.map,
      "route",
      route ? route.route.features.map((f) => ({ ...f, geometry: shiftGeometry(f.geometry, offset) })) : [],
    );
    setData(
      this.map,
      "route-areas",
      route
        ? this.#areas((id) => route.areas.includes(Number(id))).map((f) => ({
            type: "Feature" as const,
            geometry: shiftGeometry(f.geometry, offset),
            properties: {},
          }))
        : [],
    );
    const past = this.#past;
    setData(
      this.map,
      "past-run",
      past
        ? this.#areas((id) => id in past).map((f) => ({
            type: "Feature" as const,
            geometry: shiftGeometry(f.geometry, offset),
            properties: { state: past[String(f.properties.id)] },
          }))
        : [],
    );
    if (!this.#fitted) this.fit();
  }
}
