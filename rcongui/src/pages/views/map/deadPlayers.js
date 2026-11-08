// Keeps dead players on the map at the place they died.
//
// The game reports a (0, 0, 0) position for dead players and players on the deployment
// screen (`alive: false` from the backend), so their death location is only known to a
// page that saw them alive. This module remembers the last alive position of each player
// in the browser, until they spawn again, change team or leave the server.

/**
 * Next table of last known positions (player_id → {x, y, team}).
 * Alive players update their entry; dead players keep theirs while they stay in the same
 * team; players who left or switched team are forgotten.
 */
export function updateLastPositions(previous, players) {
  const next = new Map();
  for (const player of players) {
    if (player.alive) {
      next.set(player.player_id, { x: player.x, y: player.y, team: player.team });
      continue;
    }
    const known = previous.get(player.player_id);
    if (known && known.team === player.team) next.set(player.player_id, known);
  }
  return next;
}

/**
 * Players with `dead: true` and their last known position when they are dead but were
 * seen alive; other players are returned unchanged.
 */
export function withDeadPositions(players, lastPositions) {
  return players.map((player) => {
    if (player.alive) return player;
    const known = lastPositions.get(player.player_id);
    return known ? { ...player, x: known.x, y: known.y, dead: true } : player;
  });
}
