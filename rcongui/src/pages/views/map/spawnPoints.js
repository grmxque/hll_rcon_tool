// Spawn points seen from the spawns of the match (rcon/actions_tracking/spawns.py), as sent by
// /api/get_live_map: HQs, outposts, possible airheads, and garrisons only seen from the spawns (the
// garrisons counted for their builder are in `garrisons`).
import { formatAge } from "./garrisons.js";

export const SPAWN_POINT_KINDS = {
  hq: { letter: "H", title: "HQ", possible: false },
  outpost: { letter: "O", title: "Outpost", possible: false },
  airhead: { letter: "A", title: "Possible airhead", possible: true },
  garrison: { letter: "G", title: "Garrison (seen from the spawns)", possible: false },
};

/** Spawn points to draw: the option is on and their team is shown. */
export function visibleSpawnPoints(points, { show, visibleTeams }) {
  if (!show) return [];
  return (points ?? []).filter((point) => SPAWN_POINT_KINDS[point.kind] && visibleTeams[point.team] !== false);
}

/** Tooltip lines. ``nowMs``: current time in milliseconds. */
export function spawnPointDescription(point, nowMs) {
  const nowSeconds = nowMs / 1000;
  const kind = SPAWN_POINT_KINDS[point.kind];
  const lines = [];
  if (point.owner_name) lines.push(`Credits ${point.owner_name}`);
  lines.push(
    `${point.spawns} spawn${point.spawns === 1 ? "" : "s"} seen · last one ${formatAge(nowSeconds - point.last_seen)} ago`,
  );
  if (kind?.possible) lines.push("Credits nobody, used by several squads, placed during the match");
  return { title: kind?.title ?? point.kind, lines };
}

/** Number of points per kind, for the sidebar. */
export function spawnPointCounts(points) {
  const counts = { hq: 0, outpost: 0, airhead: 0, garrison: 0 };
  for (const point of points ?? []) {
    if (point.kind in counts) counts[point.kind] += 1;
  }
  return counts;
}
