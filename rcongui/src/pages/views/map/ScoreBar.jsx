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
    spacing={{ xs: 1, sm: 1.5 }}
    sx={{ flex: 1, minWidth: 0, justifyContent: "flex-end" }}
  >
    {faction && (
      <Box
        component="img"
        src={`/icons/teams/${FLAG_ALIASES[faction] ?? faction}.webp`}
        alt=""
        onError={(event) => {
          event.currentTarget.style.display = "none";
        }}
        sx={{ width: { xs: 24, sm: 32 }, height: { xs: 24, sm: 32 }, objectFit: "contain", flexShrink: 0 }}
      />
    )}
    <Box sx={{ textAlign: reverse ? "left" : "right", minWidth: 0 }}>
      <Typography variant="subtitle1" noWrap sx={{ fontWeight: 600, lineHeight: 1.2 }}>
        {factionLabel(faction, fallback)}
      </Typography>
      <Typography variant="caption" color="text.secondary" noWrap component="div">
        {players} players
      </Typography>
    </Box>
    <Typography
      variant="h4"
      sx={(theme) => ({
        fontWeight: 700,
        color: TEAM_COLORS[team],
        minWidth: { xs: 24, sm: 32 },
        textAlign: "center",
        [theme.breakpoints.down("sm")]: { fontSize: "1.5rem" },
      })}
    >
      {score}
    </Typography>
  </Stack>
);

/** Current score, remaining time and team sizes, shown above the map. */
export default function ScoreBar({ score, layer }) {
  if (!score) return null;
  return (
    <Paper variant="outlined" sx={{ px: { xs: 1, sm: 2 }, py: 1, flexShrink: 0 }}>
      <Stack direction="row" alignItems="center" spacing={{ xs: 1, sm: 2 }}>
        <TeamScore
          team="allies"
          faction={score.allied_faction}
          fallback="Allies"
          score={score.allied}
          players={score.allied_players}
        />
        <Box sx={{ textAlign: "center", minWidth: { xs: 80, sm: 110 } }}>
          <Typography
            variant="h6"
            sx={(theme) => ({
              fontVariantNumeric: "tabular-nums",
              lineHeight: 1.2,
              [theme.breakpoints.down("sm")]: { fontSize: "1rem" },
            })}
          >
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
