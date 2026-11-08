import { useCallback, useMemo, useRef, useState } from "react";
import { Alert, Box, LinearProgress, Paper, Stack, Typography } from "@mui/material";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { cmd } from "@/utils/fetchUtils";
import LiveMapCanvas from "./LiveMapCanvas";
import MapSidebar, { isUnassigned } from "./MapSidebar";
import PlayerLogs from "./PlayerLogs";
import ScoreBar from "./ScoreBar";

// Same as the backend team view cache TTL: polling faster would only return cached data.
const REFRESH_INTERVAL_MS = 2000;
const LOGS_PANEL_HEIGHT = 240;

// `npm run dev` + `/views/map?demo` shows fake data, to preview the page without a game server.
const hasDemoParam = () => new URLSearchParams(window.location.search).has("demo");

async function fetchLiveMap() {
  // `import.meta.env.DEV` is a literal false in production builds, so this branch and the
  // demo module are dropped from the bundle.
  if (import.meta.env.DEV && hasDemoParam()) {
    const { demoLiveMap } = await import("./demo");
    return demoLiveMap();
  }
  return cmd.GET_LIVE_MAP({ throwRouteError: false });
}

export const liveMapQueryOptions = {
  queryKey: [{ queryIdentifier: "get_live_map" }],
  queryFn: fetchLiveMap,
  refetchInterval: REFRESH_INTERVAL_MS,
  placeholderData: keepPreviousData,
  retry: (failureCount, error) => error?.status !== 403 && failureCount < 2,
};

function squadsOf(players) {
  const squads = new Map();
  for (const player of players) {
    if (!player.unit_name || player.team == null) continue;
    const key = `${player.team}:${player.unit_name}`;
    const squad = squads.get(key) ?? { team: player.team, unit_name: player.unit_name, count: 0 };
    squad.count += 1;
    squads.set(key, squad);
  }
  return [...squads.values()].sort(
    (a, b) => a.team.localeCompare(b.team) || a.unit_name.localeCompare(b.unit_name),
  );
}

function errorMessage(error) {
  if (error.status === 403) return "You need the \"Can view the live map\" permission to see this page.";
  const hint = import.meta.env.DEV
    ? " Check the RCON credentials in dev.env, or open /views/map?demo to preview with fake data."
    : "";
  return `Could not load the live map: ${error.message ?? error}.${hint}`;
}

const LiveMapPage = () => {
  const { data, error, isLoading } = useQuery(liveMapQueryOptions);
  const canvasRef = useRef(null);
  const [selectedId, setSelectedId] = useState(null);
  const [highlightSquad, setHighlightSquad] = useState(null);
  const [visibleTeams, setVisibleTeams] = useState({ allies: true, axis: true });
  const [showNames, setShowNames] = useState(true);
  const [showUnassigned, setShowUnassigned] = useState(false);
  const [showSectorColors, setShowSectorColors] = useState(true);
  const [showInactiveStrongpoints, setShowInactiveStrongpoints] = useState(false);

  const players = useMemo(
    () => [...(data?.players ?? [])].sort((a, b) => a.name.localeCompare(b.name)),
    [data],
  );
  const mapPlayers = useMemo(
    () =>
      players.filter(
        (player) =>
          player.alive &&
          visibleTeams[player.team] !== false &&
          (showUnassigned || !isUnassigned(player)),
      ),
    [players, visibleTeams, showUnassigned],
  );
  const squads = useMemo(() => squadsOf(players), [players]);
  const selectedPlayer = players.find((player) => player.player_id === selectedId) ?? null;

  const onSearch = useCallback(
    (player) => {
      setSelectedId(player?.player_id ?? null);
      if (!player?.alive || (!showUnassigned && isUnassigned(player))) return;
      if (visibleTeams[player.team] === false) {
        setVisibleTeams((teams) => ({ ...teams, [player.team]: true }));
      }
      canvasRef.current?.focusPlayer(player.player_id);
    },
    [visibleTeams, showUnassigned],
  );

  return (
    <Stack spacing={1} sx={{ mt: 1 }}>
      <Box sx={{ height: 4 }}>{isLoading && <LinearProgress />}</Box>
      {error && (
        <Alert severity={error.status === 403 ? "warning" : "error"}>
          {data ? "Showing the last known positions. " : ""}
          {errorMessage(error)}
        </Alert>
      )}
      <ScoreBar score={data?.score} layer={data?.layer} />
      {data && !data.layer && (
        <Alert severity="info">
          The current map is unknown to CRCON: players cannot be placed on a tactical map.
        </Alert>
      )}
      <Paper
        variant="outlined"
        sx={{
          display: "flex",
          flexDirection: { xs: "column", md: "row" },
          height: { xs: "auto", md: "calc(100vh - 230px)" },
          minHeight: 560,
          overflow: "hidden",
        }}
      >
        <Stack sx={{ flexGrow: 1, minWidth: 0, height: { xs: "auto", md: "100%" } }}>
          <Box sx={{ flexGrow: 1, minHeight: { xs: "60vh", md: 0 } }}>
            <LiveMapCanvas
              ref={canvasRef}
              layer={data?.layer ?? null}
              players={mapPlayers}
              selectedId={selectedId}
              highlightSquad={highlightSquad}
              showNames={showNames}
              showSectorColors={showSectorColors}
              showInactiveStrongpoints={showInactiveStrongpoints}
              onSelect={setSelectedId}
            />
          </Box>
          {selectedPlayer && (
            <Box sx={{ height: LOGS_PANEL_HEIGHT, flexShrink: 0, borderTop: 1, borderColor: "divider" }}>
              <PlayerLogs player={selectedPlayer} matchStartMs={data?.match_start_ms ?? null} />
            </Box>
          )}
        </Stack>
        <Box
          sx={{
            width: { xs: "100%", md: 320 },
            flexShrink: 0,
            borderLeft: { md: 1 },
            borderTop: { xs: 1, md: 0 },
            borderColor: "divider",
          }}
        >
          {isLoading ? (
            <Typography sx={{ p: 2 }} color="text.secondary">
              Loading…
            </Typography>
          ) : (
            <MapSidebar
              players={players}
              selectedPlayer={selectedPlayer}
              onSearch={onSearch}
              onFocus={(player) => canvasRef.current?.focusPlayer(player.player_id)}
              squads={squads}
              highlightSquad={highlightSquad}
              onHighlightSquad={setHighlightSquad}
              visibleTeams={visibleTeams}
              onToggleTeam={(team) => setVisibleTeams((teams) => ({ ...teams, [team]: !teams[team] }))}
              showNames={showNames}
              onShowNames={setShowNames}
              showUnassigned={showUnassigned}
              onShowUnassigned={setShowUnassigned}
              layer={data?.layer ?? null}
              showSectorColors={showSectorColors}
              onShowSectorColors={setShowSectorColors}
              showInactiveStrongpoints={showInactiveStrongpoints}
              onShowInactiveStrongpoints={setShowInactiveStrongpoints}
            />
          )}
        </Box>
      </Paper>
    </Stack>
  );
};

export default LiveMapPage;
