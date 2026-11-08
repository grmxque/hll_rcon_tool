// Pure geometry helpers for the live map. No React here so they can be unit tested
// with node:test (see coordinates.test.mjs).
//
// Three coordinate spaces are involved:
// - world: game centimetres from the backend (x → east, y → south);
// - map: the tactical image, MAP_SIZE units square, origin top-left;
// - screen: CSS pixels of the SVG viewport. A view {scale, x, y} maps map → screen
//   as screen = map * scale + (x, y).

export const MAP_SIZE = 1000;
export const MIN_ZOOM = 0.8; // relative to the "fit" scale
export const MAX_ZOOM = 16;

/** Convert a world position to map units using the image extent from the backend. */
export function worldToMap(extent, x, y) {
  return {
    x: ((x - extent.x_min) / (extent.x_max - extent.x_min)) * MAP_SIZE,
    y: ((y - extent.y_min) / (extent.y_max - extent.y_min)) * MAP_SIZE,
  };
}

/** Convert a world distance (e.g. the maximum glide distance) to map units. */
export function worldLengthToMap(extent, length) {
  return (length / (extent.x_max - extent.x_min)) * MAP_SIZE;
}

/**
 * Grid lines and labels in map units. Columns are lettered (A, B, …) and rows
 * numbered from 1, like the in-game grid.
 */
export function gridGeometry(extent, grid) {
  const topLeft = worldToMap(extent, grid.x_min, grid.y_min);
  const bottomRight = worldToMap(extent, grid.x_max, grid.y_max);
  const cellWidth = (bottomRight.x - topLeft.x) / grid.columns;
  const cellHeight = (bottomRight.y - topLeft.y) / grid.rows;
  return {
    bounds: { x1: topLeft.x, y1: topLeft.y, x2: bottomRight.x, y2: bottomRight.y },
    columns: Array.from({ length: grid.columns }, (_, i) => ({
      label: String.fromCharCode(65 + i),
      x: topLeft.x + i * cellWidth,
      center: topLeft.x + (i + 0.5) * cellWidth,
    })),
    rows: Array.from({ length: grid.rows }, (_, i) => ({
      label: String(i + 1),
      y: topLeft.y + i * cellHeight,
      center: topLeft.y + (i + 0.5) * cellHeight,
    })),
  };
}

/** View that shows the whole map centred in a viewport of the given size. */
export function fitView(viewport) {
  const scale = Math.min(viewport.width, viewport.height) / MAP_SIZE || 1;
  return {
    scale,
    x: (viewport.width - MAP_SIZE * scale) / 2,
    y: (viewport.height - MAP_SIZE * scale) / 2,
  };
}

function clampScale(scale, viewport) {
  const fit = fitView(viewport).scale;
  return Math.min(Math.max(scale, fit * MIN_ZOOM), fit * MAX_ZOOM);
}

/** Zoom by `factor` while keeping the screen point (px, py) fixed. */
export function zoomAt(view, factor, px, py, viewport) {
  const scale = clampScale(view.scale * factor, viewport);
  const ratio = scale / view.scale;
  return {
    scale,
    x: px - (px - view.x) * ratio,
    y: py - (py - view.y) * ratio,
  };
}

/** Centre the map point on screen, at least at `minZoom` × the fit scale. */
export function centerOn(view, point, viewport, minZoom = 4) {
  const scale = clampScale(
    Math.max(view.scale, fitView(viewport).scale * minZoom),
    viewport,
  );
  return {
    scale,
    x: viewport.width / 2 - point.x * scale,
    y: viewport.height / 2 - point.y * scale,
  };
}

/**
 * Whether moving from `previous` to `next` (map units) is a jump rather than a walk or
 * drive, e.g. a respawn: such moves must not be animated across the map.
 */
export function isTeleport(previous, next, maxStep) {
  if (!previous) return true;
  return Math.hypot(next.x - previous.x, next.y - previous.y) > maxStep;
}

/** Zoom level relative to the fit scale (1 = whole map visible). */
export function relativeZoom(view, viewport) {
  return view.scale / fitView(viewport).scale;
}
