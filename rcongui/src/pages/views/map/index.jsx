import { useCallback, useMemo, useRef, useState } from "react";
import { Alert, Box, LinearProgress, Paper, Stack, Typography } from "@mui/material";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { cmd } from "@/utils/fetchUtils";
import LiveMapCanvas from "./LiveMapCanvas";
import MapSidebar, { isUnassigned } from "./MapSidebar";
import { updateDeployment, updateLastPositions, withDeadPositions, withDeployment } from "./deadPlayers";
import { visibleGarrisons } from "./garrisons";
import { visibleSpawnPoints } from "./spawnPoints";
import PlayerLogs from "./PlayerLogs";
import ScoreBar from "./ScoreBar";
import { useFullscreen } from "./useFullscreen";

// Same as the backend team view cache TTL: polling faster would only return cached data.
const REFRESH_INTERVAL_MS = 2000;
const LOGS_PANEL_HEIGHT = 240;

// Phones: the map is a square as wide as the page (the tactical map is square), with the sidebar below.
// Desktop: it fills the space left by the sidebar. The height must be explicit, the canvas fills it.
const MAP_AREA_SX = {
  display: "flex",
  flexDirection: "column",
  width: "100%",
  flexGrow: { md: 1 },
  minHeight: { md: 0 },
  aspectRatio: { xs: "1 / 1", md: "auto" },
  maxHeight: { xs: "75vh", md: "none" },
};

// Full screen: the map and the score cover the whole viewport, above the app bar and the sidebar.
const FULLSCREEN_MAP_AREA_SX = {
  display: "flex",
  flexDirection: "column",
  position: "fixed",
  inset: 0,
  zIndex: "modal",
  bgcolor: "background.default",
};

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
  const { active: fullscreen, toggle: toggleFullscreen } = useFullscreen();
  const [selectedId, setSelectedId] = useState(null);
  const [highlightSquad, setHighlightSquad] = useState(null);
  const [visibleTeams, setVisibleTeams] = useState({ allies: true, axis: true });
  const [showNames, setShowNames] = useState(true);
  const [showSquadNames, setShowSquadNames] = useState(false);
  const [showUnassigned, setShowUnassigned] = useState(false);
  const [showDead, setShowDead] = useState(true);
  const [showSectorColors, setShowSectorColors] = useState(true);
  const [showGarrisons, setShowGarrisons] = useState(true);
  const garrisons = data?.garrisons ?? [];
  const mapGarrisons = useMemo(
    () => visibleGarrisons(garrisons, { show: showGarrisons, visibleTeams }),
    [garrisons, showGarrisons, visibleTeams],
  );
  const [showSpawnPoints, setShowSpawnPoints] = useState(true);
  const spawnPoints = data?.spawn_points ?? [];
  const mapSpawnPoints = useMemo(
    () => visibleSpawnPoints(spawnPoints, { show: showSpawnPoints, visibleTeams }),
    [spawnPoints, showSpawnPoints, visibleTeams],
  );

  // Last alive position of each player, so the dead stay on the map until they redeploy.
  // Forgotten when a new match starts.
  // Players in the menus or whose body is still shown after a death are not deployed yet.
  const lastPositions = useRef({ match: null, positions: new Map(), deployment: new Map() });
  const players = useMemo(() => {
    const match = `${data?.match_start_ms}:${data?.layer?.map_id}`;
    const sameMatch = lastPositions.current.match === match;
    const deployment = updateDeployment(
      sameMatch ? lastPositions.current.deployment : new Map(),
      data?.players ?? [],
      { initial: !sameMatch },
    );
    const raw = withDeployment(data?.players ?? [], deployment);
    const previous = sameMatch ? lastPositions.current.positions : new Map();
    const positions = updateLastPositions(previous, raw);
    lastPositions.current = { match, positions, deployment };
    return withDeadPositions(raw, positions).sort((a, b) => a.name.localeCompare(b.name));
  }, [data]);
  const mapPlayers = useMemo(
    () =>
      players.filter(
        (player) =>
          (player.alive || (showDead && player.dead)) &&
          visibleTeams[player.team] !== false &&
          (showUnassigned || !isUnassigned(player)),
      ),
    [players, visibleTeams, showUnassigned, showDead],
  );
  const squads = useMemo(() => squadsOf(players), [players]);
  const selectedPlayer = players.find((player) => player.player_id === selectedId) ?? null;

  const onSearch = useCallback(
    (player) => {
      setSelectedId(player?.player_id ?? null);
      const onMap = player?.alive || (showDead && player?.dead);
      if (!onMap || (!showUnassigned && isUnassigned(player))) return;
      if (visibleTeams[player.team] === false) {
        setVisibleTeams((teams) => ({ ...teams, [player.team]: true }));
      }
      canvasRef.current?.focusPlayer(player.player_id);
    },
    [visibleTeams, showUnassigned, showDead],
  );

  const scoreBar = <ScoreBar score={data?.score} layer={data?.layer} />;

  return (
    <Stack spacing={1} sx={{ mt: 1 }}>
      <Box sx={{ height: 4 }}>{isLoading && <LinearProgress />}</Box>
      {error && (
        <Alert severity={error.status === 403 ? "warning" : "error"}>
          {data ? "Showing the last known positions. " : ""}
          {errorMessage(error)}
        </Alert>
      )}
      {!fullscreen && scoreBar}
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
          minHeight: { md: 560 },
          overflow: "hidden",
        }}
      >
        <Stack sx={{ flexGrow: 1, minWidth: 0, height: { xs: "auto", md: "100%" } }}>
          {/* The canvas keeps its place in the tree in full screen, so it is not remounted. */}
          <Box sx={fullscreen ? FULLSCREEN_MAP_AREA_SX : MAP_AREA_SX}>
            {fullscreen && scoreBar}
            <Box sx={{ flexGrow: 1, minHeight: 0 }}>
              <LiveMapCanvas
                ref={canvasRef}
                layer={data?.layer ?? null}
                players={mapPlayers}
                garrisons={mapGarrisons}
                spawnPoints={mapSpawnPoints}
                selectedId={selectedId}
                highlightSquad={highlightSquad}
                showNames={showNames}
                showSquadNames={showSquadNames}
                showSectorColors={showSectorColors}
                onSelect={setSelectedId}
                fullscreen={fullscreen}
                onToggleFullscreen={toggleFullscreen}
              />
            </Box>
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
              showSquadNames={showSquadNames}
              onShowSquadNames={setShowSquadNames}
              showUnassigned={showUnassigned}
              onShowUnassigned={setShowUnassigned}
              showDead={showDead}
              onShowDead={setShowDead}
              showSectorColors={showSectorColors}
              onShowSectorColors={setShowSectorColors}
              garrisons={garrisons}
              showGarrisons={showGarrisons}
              onShowGarrisons={setShowGarrisons}
              spawnPoints={spawnPoints}
              showSpawnPoints={showSpawnPoints}
              onShowSpawnPoints={setShowSpawnPoints}
            />
          )}
        </Box>
      </Paper>
    </Stack>
  );
};

export default LiveMapPage;
