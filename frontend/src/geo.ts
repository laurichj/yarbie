// Small geometry helpers. The imagery offset is display-only: geometry is shifted on the
// way onto the map and shifted back on the way off, so the robot never sees it.

export type Offset = [number, number]; // degrees of longitude, latitude

const METERS_PER_DEGREE = (6378137 * Math.PI) / 180;

export function metresToOffset(east: number, north: number, lat: number): Offset {
  return [east / (METERS_PER_DEGREE * Math.cos((lat * Math.PI) / 180)), north / METERS_PER_DEGREE];
}

// Terra Draw refuses coordinates with more than 9 decimals, and adding an offset leaves
// float noise (47.86745 + 0.0000045 = 47.867454500000005). 1e-9 degrees is 0.1 mm.
const round9 = (v: number) => Math.round(v * 1e9) / 1e9;

function shiftPosition(p: GeoJSON.Position, [dx, dy]: Offset, sign: 1 | -1): GeoJSON.Position {
  return [round9(p[0] + sign * dx), round9(p[1] + sign * dy)];
}

export function shiftGeometry<G extends GeoJSON.Geometry>(geometry: G, offset: Offset, sign: 1 | -1 = 1): G {
  if (offset[0] === 0 && offset[1] === 0) return geometry;
  switch (geometry.type) {
    case "Point":
      return { ...geometry, coordinates: shiftPosition(geometry.coordinates, offset, sign) };
    case "LineString":
      return { ...geometry, coordinates: geometry.coordinates.map((p) => shiftPosition(p, offset, sign)) };
    case "Polygon":
      return {
        ...geometry,
        coordinates: geometry.coordinates.map((ring) => ring.map((p) => shiftPosition(p, offset, sign))),
      };
    default:
      return geometry;
  }
}

export function bounds(features: { geometry: GeoJSON.Geometry }[]): [number, number, number, number] | null {
  let minX = Infinity;
  let minY = Infinity;
  let maxX = -Infinity;
  let maxY = -Infinity;
  const visit = (p: GeoJSON.Position) => {
    minX = Math.min(minX, p[0]);
    minY = Math.min(minY, p[1]);
    maxX = Math.max(maxX, p[0]);
    maxY = Math.max(maxY, p[1]);
  };
  for (const { geometry } of features) {
    if (geometry.type === "Point") visit(geometry.coordinates);
    else if (geometry.type === "LineString") geometry.coordinates.forEach(visit);
    else if (geometry.type === "Polygon") geometry.coordinates.forEach((r) => r.forEach(visit));
  }
  return Number.isFinite(minX) ? [minX, minY, maxX, maxY] : null;
}

/** A ring's centre (by area) and its radii in metres: the one that covers every corner,
 * and the average distance of the corners from the centre. */
export function circleFit(ring: GeoJSON.Position[]): { center: GeoJSON.Position; cover: number; average: number } {
  const pts = ring.length > 1 && ring[0][0] === ring.at(-1)![0] && ring[0][1] === ring.at(-1)![1] ? ring.slice(0, -1) : ring;
  const lat0 = pts.reduce((s, p) => s + p[1], 0) / pts.length;
  const lon0 = pts.reduce((s, p) => s + p[0], 0) / pts.length;
  const kx = METERS_PER_DEGREE * Math.cos((lat0 * Math.PI) / 180);
  const xy = pts.map((p) => [(p[0] - lon0) * kx, (p[1] - lat0) * METERS_PER_DEGREE]);
  // Area-weighted centroid, so a cluster of corners on one side does not pull it over.
  let a = 0;
  let cx = 0;
  let cy = 0;
  for (let i = 0; i < xy.length; i++) {
    const [x1, y1] = xy[i];
    const [x2, y2] = xy[(i + 1) % xy.length];
    const cross = x1 * y2 - x2 * y1;
    a += cross;
    cx += (x1 + x2) * cross;
    cy += (y1 + y2) * cross;
  }
  if (Math.abs(a) > 1e-9) {
    cx /= 3 * a;
    cy /= 3 * a;
  } else {
    cx = 0;
    cy = 0;
  }
  const d = xy.map(([x, y]) => Math.hypot(x - cx, y - cy));
  return {
    center: [round9(lon0 + cx / kx), round9(lat0 + cy / METERS_PER_DEGREE)],
    cover: Math.max(...d),
    average: d.reduce((s, v) => s + v, 0) / d.length,
  };
}

/** A circle of ``radius`` metres as a closed polygon ring of ``points`` corners. */
export function circle(center: GeoJSON.Position, radius: number, points = 24): GeoJSON.Polygon {
  const kx = METERS_PER_DEGREE * Math.cos((center[1] * Math.PI) / 180);
  const ring: GeoJSON.Position[] = [];
  for (let i = 0; i < points; i++) {
    const t = (2 * Math.PI * i) / points;
    ring.push([round9(center[0] + (radius * Math.cos(t)) / kx), round9(center[1] + (radius * Math.sin(t)) / METERS_PER_DEGREE)]);
  }
  ring.push(ring[0]);
  return { type: "Polygon", coordinates: [ring] };
}
