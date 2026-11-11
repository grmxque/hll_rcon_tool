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

/**
 * Next deployment state of each player (player_id → {x, y, deaths, team, deployed}).
 *
 * A player in the menus (just connected, choosing a team or a role) is reported at a fixed
 * position near the edge of the map, not at (0, 0, 0), and a dead body stays where it died for
 * a few seconds with the death already counted. So after connecting while the page is open,
 * dying or changing team, a player is only deployed once his position changes. Players already
 * there when the page opened (``initial``) are taken as reported.
 */
export function updateDeployment(previous, players, { initial = false } = {}) {
  const next = new Map();
  for (const player of players) {
    const known = previous.get(player.player_id);
    let deployed;
    if (!player.alive) deployed = false;
    else if (!known) deployed = initial;
    else if (player.deaths > known.deaths || player.team !== known.team) deployed = false;
    else if (known.deployed) deployed = true;
    else deployed = player.x !== known.x || player.y !== known.y;
    next.set(player.player_id, {
      x: player.x,
      y: player.y,
      deaths: player.deaths,
      team: player.team,
      deployed,
    });
  }
  return next;
}

/** Players not deployed yet are treated as dead (``alive: false``). */
export function withDeployment(players, states) {
  return players.map((player) =>
    player.alive && states.get(player.player_id)?.deployed === false ? { ...player, alive: false } : player,
  );
}
