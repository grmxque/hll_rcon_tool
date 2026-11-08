import {
  Alert,
  Autocomplete,
  Box,
  Checkbox,
  Divider,
  FormControlLabel,
  IconButton,
  MenuItem,
  Paper,
  Stack,
  TextField,
  Tooltip,
  Typography,
} from "@mui/material";
import MyLocationIcon from "@mui/icons-material/MyLocation";
import { ActionMenuButton } from "@/features/player-action/ActionMenu";
import { generatePlayerActions } from "@/features/player-action/actions";
import { TEAM_COLORS } from "./LiveMapCanvas";

const TEAM_LABELS = { allies: "Allies", axis: "Axis", unassigned: "Unassigned" };

/** In the lobby (no team) or in a team without a squad. Commanders are in a squad of their own. */
export const isUnassigned = (player) =>
  !["allies", "axis"].includes(player.team) || !player.unit_name || player.unit_name === "unassigned";

const squadKey = (squad) => (squad ? `${squad.team}:${squad.unit_name}` : "");

const SelectedPlayer = ({ player, onFocus, showUnassigned }) => (
  <Paper variant="outlined" sx={{ p: 1.5 }}>
      <Stack direction="row" alignItems="center" spacing={1}>
        <Box
          sx={{
            width: 12,
            height: 12,
            borderRadius: "50%",
            bgcolor: TEAM_COLORS[player.team] ?? TEAM_COLORS.unassigned,
            flexShrink: 0,
          }}
        />
        <Typography variant="subtitle1" noWrap sx={{ flexGrow: 1 }}>
          {player.name}
        </Typography>
        {player.alive && (showUnassigned || !isUnassigned(player)) && (
          <Tooltip title="Centre the map on this player">
            <IconButton size="small" onClick={() => onFocus(player)}>
              <MyLocationIcon fontSize="small" />
            </IconButton>
          </Tooltip>
        )}
        <ActionMenuButton
          actions={generatePlayerActions({ multiAction: false, onlineAction: true })}
          recipients={player}
        />
      </Stack>
      <Typography variant="body2" color="text.secondary">
        {TEAM_LABELS[player.team] ?? "?"} · {player.unit_name ?? "?"} · {player.role ?? "?"}
      </Typography>
      <Typography variant="body2" color="text.secondary">
        Level {player.level} · {player.kills} K / {player.deaths} D
      </Typography>
      {!player.alive && (
        <Typography variant="body2" color="warning.main">
          Dead or on the deployment screen: not shown on the map.
        </Typography>
      )}
      {player.alive && isUnassigned(player) && !showUnassigned && (
        <Typography variant="body2" color="warning.main">
          Not in a squad: hidden from the map.
        </Typography>
      )}
    </Paper>
);

/** Search, filters and details of the selected player. */
export default function MapSidebar({
  players,
  selectedPlayer,
  onSearch,
  onFocus,
  squads,
  highlightSquad,
  onHighlightSquad,
  visibleTeams,
  onToggleTeam,
  showNames,
  onShowNames,
  showUnassigned,
  onShowUnassigned,
  layer,
  showSectorColors,
  onShowSectorColors,
  showInactiveStrongpoints,
  onShowInactiveStrongpoints,
}) {
  const hasFrontLine = layer?.sectors?.length === 5;
  const aliveCount = players.filter((player) => player.alive).length;
  const unassignedCount = players.filter((player) => player.alive && isUnassigned(player)).length;
  const onMapCount = showUnassigned ? aliveCount : aliveCount - unassignedCount;

  return (
    <Stack spacing={2} sx={{ p: 2, height: "100%", overflowY: "auto" }}>
      <Autocomplete
        size="small"
        options={players}
        value={selectedPlayer ?? null}
        getOptionLabel={(player) => player.name}
        isOptionEqualToValue={(option, value) => option.player_id === value.player_id}
        groupBy={(player) => TEAM_LABELS[player.team] ?? "Unassigned"}
        onChange={(_, player) => onSearch(player)}
        renderOption={({ key, ...props }, player) => (
          <li key={player.player_id} {...props}>
            <Box sx={{ opacity: player.alive ? 1 : 0.5 }}>
              {player.name}
              <Typography component="span" variant="caption" color="text.secondary">
                {" "}
                {player.unit_name} · {player.role}
              </Typography>
            </Box>
          </li>
        )}
        renderInput={(params) => <TextField {...params} label="Find a player" />}
      />

      {selectedPlayer && <SelectedPlayer player={selectedPlayer} onFocus={onFocus} showUnassigned={showUnassigned} />}

      <Divider />

      <TextField
        select
        size="small"
        label="Highlight a squad"
        value={squadKey(highlightSquad)}
        onChange={(event) =>
          onHighlightSquad(squads.find((squad) => squadKey(squad) === event.target.value) ?? null)
        }
      >
        <MenuItem value="">None</MenuItem>
        {squads.map((squad) => (
          <MenuItem key={squadKey(squad)} value={squadKey(squad)}>
            {TEAM_LABELS[squad.team] ?? "?"} · {squad.unit_name} ({squad.count})
          </MenuItem>
        ))}
      </TextField>

      <Box>
        {["allies", "axis"].map((team) => (
          <FormControlLabel
            key={team}
            control={
              <Checkbox
                size="small"
                checked={visibleTeams[team]}
                onChange={() => onToggleTeam(team)}
                sx={{ color: TEAM_COLORS[team], "&.Mui-checked": { color: TEAM_COLORS[team] } }}
              />
            }
            label={TEAM_LABELS[team]}
          />
        ))}
        <FormControlLabel
          control={<Checkbox size="small" checked={showNames} onChange={(e) => onShowNames(e.target.checked)} />}
          label="Show names when zoomed in"
        />
        <FormControlLabel
          control={
            <Checkbox size="small" checked={showUnassigned} onChange={(e) => onShowUnassigned(e.target.checked)} />
          }
          label="Show players without a squad"
        />
        <FormControlLabel
          control={
            <Checkbox size="small" checked={showSectorColors} onChange={(e) => onShowSectorColors(e.target.checked)} />
          }
          label="Colour sectors by owner"
        />
        {layer?.objectives_known && (
          <FormControlLabel
            control={
              <Checkbox
                size="small"
                checked={showInactiveStrongpoints}
                onChange={(e) => onShowInactiveStrongpoints(e.target.checked)}
              />
            }
            label="Show inactive strongpoints"
          />
        )}
      </Box>

      <Typography variant="body2" color="text.secondary">
        {onMapCount} on the map · {players.length - aliveCount} dead or deploying
        {!showUnassigned && unassignedCount > 0 ? ` · ${unassignedCount} without a squad (hidden)` : ""}
      </Typography>
      {hasFrontLine && !layer.objectives_known && (
        <Alert severity="warning" variant="outlined">
          Active strongpoints unknown: the game does not report them. They are only known when the layout was set
          from CRCON (Maps → Objectives) before the match, so all strongpoints are shown.
        </Alert>
      )}
      <Alert severity="info" variant="outlined">
        Positions refresh every 2 seconds. Scroll or pinch to zoom, drag to move.
      </Alert>
    </Stack>
  );
}
