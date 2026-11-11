import assert from "node:assert/strict";
import { test } from "node:test";
import { spawnPointCounts, spawnPointDescription, visibleSpawnPoints } from "./spawnPoints.js";

const point = (kind, team = "allies", extra = {}) => ({
  x: 0,
  y: 0,
  team,
  kind,
  owner_id: null,
  owner_name: null,
  first_seen: 1_000,
  last_seen: 1_200,
  spawns: 4,
  ...extra,
});

test("spawn points follow the option and the team filter", () => {
  const all = [point("hq"), point("outpost", "axis"), point("unknown")];
  assert.deepEqual(visibleSpawnPoints(all, { show: false, visibleTeams: {} }), []);
  assert.deepEqual(
    visibleSpawnPoints(all, { show: true, visibleTeams: { allies: true, axis: false } }).map((p) => p.kind),
    ["hq"],
  );
  assert.deepEqual(visibleSpawnPoints(undefined, { show: true, visibleTeams: {} }), []);
});

test("tooltips", () => {
  assert.deepEqual(spawnPointDescription(point("outpost", "allies", { owner_name: "grmxque" }), 1_300_000), {
    title: "Outpost",
    lines: ["Credits grmxque", "4 spawns seen · last one 1 min ago"],
  });
  assert.deepEqual(spawnPointDescription(point("airhead", "allies", { spawns: 1 }), 1_230_000), {
    title: "Possible airhead",
    lines: ["1 spawn seen · last one 30 s ago", "Credits nobody, used by several squads, placed during the match"],
  });
});

test("counts", () => {
  assert.deepEqual(spawnPointCounts([point("hq"), point("hq"), point("airhead")]), {
    hq: 2,
    outpost: 0,
    airhead: 1,
    garrison: 0,
  });
  assert.deepEqual(spawnPointCounts(undefined), { hq: 0, outpost: 0, airhead: 0, garrison: 0 });
});
