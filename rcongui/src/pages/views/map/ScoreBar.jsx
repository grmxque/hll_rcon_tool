import { Box, Paper, Stack, Typography } from "@mui/material";
import { TEAM_COLORS } from "./LiveMapCanvas";

// Factions without a dedicated flag in /icons/teams.
const FLAG_ALIASES = { cw: "gb", b8a: "gb", dak: "ger" };
const FACTION_LABELS = { cw: "GB", rus: "SOV" };

const factionLabel = (faction, fallback) =>
  faction ? (FACTION_LABELS[faction] ?? faction.toUpperCase()) : fallback;

function formatTime(seconds) {
  const safe = Math.max(0, seconds);
  const h = Math.floor(safe / 3600);
  const m = Math.floor((safe % 3600) / 60);
  const s = safe % 60;
  const pad = (value) => String(value).padStart(2, "0");
  return h ? `${h}:${pad(m)}:${pad(s)}` : `${pad(m)}:${pad(s)}`;
}

const TeamScore = ({ faction, fallback, score, players, team, reverse }) => (
  // Allies on the left read "flag name score", Axis on the right "score name flag".
  <Stack
    direction={reverse ? "row-reverse" : "row"}
    alignItems="center"
    spacing={1.5}
    sx={{ flex: 1, justifyContent: "flex-end" }}
  >
    {faction && (
      <Box
        component="img"
        src={`/icons/teams/${FLAG_ALIASES[faction] ?? faction}.webp`}
        alt=""
        onError={(event) => {
          event.currentTarget.style.display = "none";
        }}
        sx={{ width: 32, height: 32, objectFit: "contain" }}
      />
    )}
    <Box sx={{ textAlign: reverse ? "left" : "right" }}>
      <Typography variant="subtitle1" sx={{ fontWeight: 600, lineHeight: 1.2 }}>
        {factionLabel(faction, fallback)}
      </Typography>
      <Typography variant="caption" color="text.secondary">
        {players} players
      </Typography>
    </Box>
    <Typography variant="h4" sx={{ fontWeight: 700, color: TEAM_COLORS[team], minWidth: 32, textAlign: "center" }}>
      {score}
    </Typography>
  </Stack>
);

/** Current score, remaining time and team sizes, shown above the map. */
export default function ScoreBar({ score, layer }) {
  if (!score) return null;
  return (
    <Paper variant="outlined" sx={{ px: 2, py: 1 }}>
      <Stack direction="row" alignItems="center" spacing={2}>
        <TeamScore
          team="allies"
          faction={score.allied_faction}
          fallback="Allies"
          score={score.allied}
          players={score.allied_players}
        />
        <Box sx={{ textAlign: "center", minWidth: 110 }}>
          <Typography variant="h6" sx={{ fontVariantNumeric: "tabular-nums", lineHeight: 1.2 }}>
            {formatTime(score.time_remaining_seconds)}
          </Typography>
          {layer && (
            <Typography variant="caption" color="text.secondary" sx={{ textTransform: "capitalize" }}>
              {layer.map_id} · {layer.game_mode}
            </Typography>
          )}
        </Box>
        <TeamScore
          team="axis"
          faction={score.axis_faction}
          fallback="Axis"
          score={score.axis}
          players={score.axis_players}
          reverse
        />
      </Stack>
    </Paper>
  );
}
