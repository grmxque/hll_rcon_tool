import assert from "node:assert/strict";
import { test } from "node:test";
import {
  MAP_SIZE,
  MAX_ZOOM,
  centerOn,
  fitView,
  gridGeometry,
  relativeZoom,
  worldLengthToMap,
  worldToMap,
  zoomAt,
} from "./coordinates.js";

const EXTENT = { x_min: -100000, y_min: -100000, x_max: 100000, y_max: 100000 };
const VIEWPORT = { width: 1200, height: 600 };

test("world positions map onto the square image", () => {
  assert.deepEqual(worldToMap(EXTENT, -100000, -100000), { x: 0, y: 0 });
  assert.deepEqual(worldToMap(EXTENT, 0, 0), { x: MAP_SIZE / 2, y: MAP_SIZE / 2 });
  // East is right, south is down.
  assert.deepEqual(worldToMap(EXTENT, 100000, 50000), { x: MAP_SIZE, y: 750 });
  assert.equal(worldLengthToMap(EXTENT, 5000), 25);
});

test("grid lines follow a grid smaller than the image", () => {
  const grid = { x_min: -50000, y_min: -60000, x_max: 50000, y_max: 60000, columns: 10, rows: 6 };
  const geometry = gridGeometry(EXTENT, grid);

  assert.deepEqual(geometry.bounds, { x1: 250, y1: 200, x2: 750, y2: 800 });
  assert.equal(geometry.columns.length, 10);
  assert.deepEqual(geometry.columns[1], { label: "B", x: 300, center: 325 });
  assert.deepEqual(geometry.rows.at(-1), { label: "6", y: 700, center: 750 });
});

test("fit view shows the whole map centred", () => {
  assert.deepEqual(fitView(VIEWPORT), { scale: 0.6, x: 300, y: 0 });
});

test("zooming keeps the point under the cursor fixed and is clamped", () => {
  const view = fitView(VIEWPORT);
  const zoomed = zoomAt(view, 2, 600, 300, VIEWPORT);
  const mapPointBefore = (600 - view.x) / view.scale;
  const mapPointAfter = (600 - zoomed.x) / zoomed.scale;

  assert.equal(zoomed.scale, 1.2);
  assert.ok(Math.abs(mapPointBefore - mapPointAfter) < 1e-9);
  assert.equal(relativeZoom(zoomAt(view, 1000, 0, 0, VIEWPORT), VIEWPORT), MAX_ZOOM);
});

test("centering puts the point in the middle of the viewport", () => {
  const view = centerOn(fitView(VIEWPORT), { x: 100, y: 900 }, VIEWPORT);

  assert.equal(relativeZoom(view, VIEWPORT), 4);
  assert.equal(100 * view.scale + view.x, VIEWPORT.width / 2);
  assert.equal(900 * view.scale + view.y, VIEWPORT.height / 2);
});
