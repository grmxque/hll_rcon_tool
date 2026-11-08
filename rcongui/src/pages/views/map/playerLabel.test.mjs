import assert from "node:assert/strict";
import { test } from "node:test";
import { playerLabel, squadTag } from "./playerLabel.js";

const player = (fields) => ({ name: "grmxque", unit_name: "able", role: "rifleman", ...fields });

test("the label is the name only unless the squad is shown", () => {
  assert.equal(playerLabel(player(), false), "grmxque");
  assert.equal(playerLabel(player(), true), "A · grmxque");
});

test("the squad is shortened to its initial, the commander to CMD", () => {
  assert.equal(squadTag(player({ unit_name: "baker" })), "B");
  assert.equal(squadTag(player({ unit_name: "command", role: "armycommander" })), "CMD");
});

test("players without a squad have no tag", () => {
  assert.equal(squadTag(player({ unit_name: "unassigned" })), null);
  assert.equal(squadTag(player({ unit_name: null })), null);
  assert.equal(playerLabel(player({ unit_name: null }), true), "grmxque");
});
