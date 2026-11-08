// Text drawn next to a player marker. Kept short because labels of a full server overlap
// quickly: the squad is reduced to its initial (able → A), the full name is in the tooltip.

const COMMANDER_ROLE = "armycommander";

/** Short squad tag ("A" for able, "CMD" for the commander), or null without a squad. */
export function squadTag(player) {
  if (player.role === COMMANDER_ROLE) return "CMD";
  const unit = player.unit_name;
  if (!unit || unit === "unassigned") return null;
  return unit[0].toUpperCase();
}

/** "grmxque", or "A · grmxque" when the squad is shown. */
export function playerLabel(player, showSquad) {
  const tag = showSquad ? squadTag(player) : null;
  return tag ? `${tag} · ${player.name}` : player.name;
}
