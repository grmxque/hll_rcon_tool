import assert from "node:assert/strict";
import { test } from "node:test";
import { updateDeployment, updateLastPositions, withDeadPositions, withDeployment } from "./deadPlayers.js";

const player = (id, { alive = true, x = 0, y = 0, team = "allies" } = {}) => ({
  player_id: id,
  name: `P${id}`,
  team,
  alive,
  x,
  y,
});

test("a player who dies keeps the position where they were last seen alive", () => {
  let positions = updateLastPositions(new Map(), [player("1", { x: 100, y: 200 })]);
  positions = updateLastPositions(positions, [player("1", { alive: false })]);

  assert.deepEqual(withDeadPositions([player("1", { alive: false })], positions), [
    { ...player("1", { alive: false }), x: 100, y: 200, dead: true },
  ]);
});

test("respawning replaces the remembered position", () => {
  let positions = updateLastPositions(new Map(), [player("1", { x: 100, y: 200 })]);
  positions = updateLastPositions(positions, [player("1", { alive: false })]);
  positions = updateLastPositions(positions, [player("1", { x: 5000, y: 10 })]);

  const [shown] = withDeadPositions([player("1", { x: 5000, y: 10 })], positions);
  assert.equal(shown.dead, undefined);
  assert.deepEqual(positions.get("1"), { x: 5000, y: 10, team: "allies" });
});

test("players never seen alive, gone, or who switched team are not kept", () => {
  let positions = updateLastPositions(new Map(), [player("1", { x: 1 }), player("2", { x: 2 })]);
  positions = updateLastPositions(positions, [
    player("1", { alive: false, team: "axis" }), // switched team while dead
    player("3", { alive: false }), // died before the page saw them
  ]);

  assert.equal(positions.size, 0);
  const shown = withDeadPositions([player("3", { alive: false })], positions);
  assert.equal(shown[0].dead, undefined);
});

test("a player in the menus is not drawn until his position changes", () => {
  const menus = { ...player("9", { x: 97748, y: 35110 }), deaths: 0 };
  let states = updateDeployment(new Map(), [{ ...player("1", { x: 5 }), deaths: 0 }], { initial: true });
  assert.equal(states.get("1").deployed, true); // there when the page opened
  states = updateDeployment(states, [menus]);
  assert.deepEqual(withDeployment([menus], states)[0].alive, false);
  states = updateDeployment(states, [menus]); // still the same position
  assert.equal(states.get("9").deployed, false);
  const spawned = { ...menus, x: 97435, y: -40247 };
  states = updateDeployment(states, [spawned]);
  assert.equal(withDeployment([spawned], states)[0].alive, true);
});

test("a body kept where it died is dead, then the player is alive once he moves", () => {
  let states = updateDeployment(new Map(), [{ ...player("1", { x: 10 }), deaths: 0 }], { initial: true });
  states = updateDeployment(states, [{ ...player("1", { x: 10 }), deaths: 1 }]); // died, body stays
  assert.equal(states.get("1").deployed, false);
  states = updateDeployment(states, [{ ...player("1", { alive: false }), deaths: 1 }]);
  states = updateDeployment(states, [{ ...player("1", { x: 22 }), deaths: 1 }]); // respawned nearby
  assert.equal(states.get("1").deployed, true);
});
