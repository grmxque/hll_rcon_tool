import assert from "node:assert/strict";
import { test } from "node:test";
import { formatAge, garrisonCounts, garrisonDescription, visibleGarrisons } from "./garrisons.js";

const garrison = (status, team = "allies", extra = {}) => ({
  x: 0,
  y: 0,
  team,
  status,
  builder_id: "1",
  builder_name: "grmxque",
  built_at: 1_000,
  last_seen: status === "confirmed" ? 1_200 : null,
  ...extra,
});

test("garrisons follow the option and the team filter, candidates drawn first", () => {
  const all = [garrison("confirmed"), garrison("candidate"), garrison("confirmed", "axis")];
  assert.deepEqual(visibleGarrisons(all, { show: false, visibleTeams: {} }), []);
  assert.deepEqual(
    visibleGarrisons(all, { show: true, visibleTeams: { allies: true, axis: false } }).map((g) => g.status),
    ["candidate", "confirmed"],
  );
  assert.deepEqual(visibleGarrisons(undefined, { show: true, visibleTeams: {} }), []);
});

test("ages", () => {
  assert.equal(formatAge(-3), "0 s");
  assert.equal(formatAge(45), "45 s");
  assert.equal(formatAge(180), "3 min");
  assert.equal(formatAge(3_900), "1 h 05");
});

test("tooltips tell candidates from confirmed garrisons", () => {
  assert.deepEqual(garrisonDescription(garrison("confirmed"), 1_300_000), {
    title: "Garrison",
    lines: ["Built by grmxque · 5 min ago", "Last spawn on it 1 min ago"],
  });
  assert.deepEqual(garrisonDescription(garrison("candidate"), 1_045_000), {
    title: "Possible garrison",
    lines: ["Built by grmxque · 45 s ago", "Detected from the score, waiting for a spawn on it to confirm it"],
  });
});

test("counts", () => {
  assert.deepEqual(garrisonCounts([garrison("confirmed"), garrison("candidate"), garrison("candidate")]), {
    confirmed: 1,
    candidates: 2,
  });
  assert.deepEqual(garrisonCounts(undefined), { confirmed: 0, candidates: 0 });
});
