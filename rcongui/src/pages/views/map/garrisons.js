// Garrisons detected by the actions tracking (rcon/actions_tracking), as sent by /api/get_live_map.
// "candidate": detected from the builder's score, not confirmed yet (abilities and supplies consumed
// give the same points); "confirmed": teammates spawned on it.

/** Game icon of a garrison (assets/hll/images/icons/map, see docs/live-map.md). */
export const GARRISON_ICON = "/icons/map/garrison.png";

/** Garrisons to draw: the option is on and their team is shown. Candidates first, so confirmed
 * garrisons are drawn above them. */
export function visibleGarrisons(garrisons, { show, visibleTeams }) {
  if (!show) return [];
  return (garrisons ?? [])
    .filter((garrison) => visibleTeams[garrison.team] !== false)
    .sort((a, b) => (a.status === b.status ? 0 : a.status === "candidate" ? -1 : 1));
}

/** "45 s", "3 min", "1 h 05" */
export function formatAge(seconds) {
  const value = Math.max(0, Math.round(seconds));
  if (value < 60) return `${value} s`;
  const minutes = Math.floor(value / 60);
  if (minutes < 60) return `${minutes} min`;
  return `${Math.floor(minutes / 60)} h ${String(minutes % 60).padStart(2, "0")}`;
}

/** Tooltip lines. ``nowMs``: current time in milliseconds. */
export function garrisonDescription(garrison, nowMs) {
  const nowSeconds = nowMs / 1000;
  const lines = [`Built by ${garrison.builder_name} · ${formatAge(nowSeconds - garrison.built_at)} ago`];
  if (garrison.status === "confirmed") {
    if (garrison.last_seen) lines.push(`Last spawn on it ${formatAge(nowSeconds - garrison.last_seen)} ago`);
    return { title: "Garrison", lines };
  }
  lines.push("Detected from the score, waiting for a spawn on it to confirm it");
  return { title: "Possible garrison", lines };
}

export function garrisonCounts(garrisons) {
  const confirmed = (garrisons ?? []).filter((garrison) => garrison.status === "confirmed").length;
  return { confirmed, candidates: (garrisons ?? []).length - confirmed };
}
