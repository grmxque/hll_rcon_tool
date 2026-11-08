import { Alert, Box, Chip, Stack, Typography } from "@mui/material";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { cmd } from "@/utils/fetchUtils";

const REFRESH_INTERVAL_MS = 5000;
// Number of most recent log lines scanned on the server, and lines kept for display.
const SCANNED_LINES = 10000;
const SHOWN_LINES = 100;

const hasDemoParam = () => new URLSearchParams(window.location.search).has("demo");

async function fetchPlayerLogs(playerName) {
  // `import.meta.env.DEV` is a literal false in production builds, so this branch and the
  // demo module are dropped from the bundle.
  if (import.meta.env.DEV && hasDemoParam()) {
    const { demoPlayerLogs } = await import("./demo");
    return demoPlayerLogs(playerName);
  }
  const response = await cmd.GET_LIVE_LOGS({
    payload: {
      end: SCANNED_LINES,
      filter_player: [playerName],
      filter_action: [],
      inclusive_filter: true,
      exact_player_match: true,
    },
    throwRouteError: false,
  });
  return response.result;
}

/** Label and colour of a log line from the selected player's point of view. */
function describe(line, playerId) {
  const isActor = line.player_id_1 === playerId;
  switch (line.action) {
    case "KILL":
      return isActor ? { label: "KILL", color: "success" } : { label: "DEATH", color: "error" };
    case "TEAM KILL":
      return isActor ? { label: "TEAM KILL", color: "warning" } : { label: "TEAM KILLED", color: "warning" };
    default:
      if (line.action.startsWith("CHAT")) return { label: line.action, color: "info" };
      if (line.action.startsWith("ADMIN") || line.action.startsWith("MESSAGE")) {
        return { label: line.action, color: "secondary" };
      }
      return { label: line.action, color: "default" };
  }
}

function summary(line, playerId) {
  if (line.action === "KILL" || line.action === "TEAM KILL") {
    const other = line.player_id_1 === playerId ? line.player_name_2 : line.player_name_1;
    const direction = line.player_id_1 === playerId ? "→" : "←";
    return `${direction} ${other}${line.weapon ? ` (${line.weapon})` : ""}`;
  }
  return line.sub_content ?? line.message;
}

const time = (timestampMs) =>
  new Date(timestampMs).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });

/**
 * Game log lines involving one player, newest first. With `matchStartMs` (start of the
 * match in progress), older lines (previous matches, earlier sessions) are dropped.
 */
export default function PlayerLogs({ player, matchStartMs }) {
  const { data, error, isLoading } = useQuery({
    queryKey: [{ queryIdentifier: "get_recent_logs", player: player.name }],
    queryFn: () => fetchPlayerLogs(player.name),
    refetchInterval: REFRESH_INTERVAL_MS,
    placeholderData: keepPreviousData,
    retry: (failureCount, err) => err?.status !== 403 && failureCount < 2,
  });

  // The server filters on the name; also check the id so a namesake's lines are dropped.
  const lines = (data?.logs ?? [])
    .filter((line) => !line.player_id_1 || line.player_id_1 === player.player_id || line.player_id_2 === player.player_id)
    .filter((line) => !matchStartMs || line.timestamp_ms >= matchStartMs)
    .slice(0, SHOWN_LINES);

  return (
    <Stack sx={{ height: "100%", minHeight: 0 }}>
      <Typography variant="subtitle2" sx={{ px: 1.5, py: 1, borderBottom: 1, borderColor: "divider" }}>
        Game logs · {player.name}
        <Typography component="span" variant="caption" color="text.secondary">
          {matchStartMs ? " · current match" : " · recent"}
        </Typography>
      </Typography>
      <Box sx={{ flexGrow: 1, overflowY: "auto", px: 1.5, py: 0.5, fontFamily: "monospace", fontSize: 13 }}>
        {error && (
          <Alert severity="warning" sx={{ my: 1 }}>
            {error.status === 403
              ? "You need the permission to view recent logs."
              : `Could not load the logs: ${error.message ?? error}`}
          </Alert>
        )}
        {!error && !isLoading && lines.length === 0 && (
          <Typography variant="body2" color="text.secondary" sx={{ py: 1 }}>
            {matchStartMs ? "No log line for this player in the current match." : "No recent log line for this player."}
          </Typography>
        )}
        {lines.map((line) => {
          const { label, color } = describe(line, player.player_id);
          return (
            <Stack
              key={`${line.timestamp_ms}-${line.action}-${line.player_id_1}-${line.player_id_2}`}
              direction="row"
              spacing={1}
              alignItems="center"
              sx={{ py: 0.25 }}
            >
              <Box component="span" sx={{ color: "text.secondary", flexShrink: 0 }}>
                {time(line.timestamp_ms)}
              </Box>
              <Chip size="small" label={label} color={color} sx={{ flexShrink: 0, minWidth: 90 }} />
              <Box component="span" sx={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                {summary(line, player.player_id)}
              </Box>
            </Stack>
          );
        })}
      </Box>
    </Stack>
  );
}
