# Live Map

Fork feature. **Views → Map** (`/views/map`) shows the current map's tactical image with the position of every
player, refreshed every 2 seconds.

- A banner above the map shows the score, remaining time, factions and team sizes.
- Players are dots coloured by team (Allies blue, Axis red) with their role icon. Hovering shows name, role,
  squad, level and K/D.
- Clicking a player (or picking them in the search) selects them:
  - the sidebar shows the player card with the usual player action menu (message, punish, kick, switch,
    ban…, each action keeping its own permission) and a button to centre the map on them;
  - a panel under the map lists the player's game log lines of the current match (since
    `MapsHistory()[0]["start"]`, returned as `match_start_ms`), newest first, refreshed every 5 s: kills,
    deaths, team kills (given or received), chat, admin actions… It uses `get_recent_logs` (permission
    `can_view_recent_logs`) on the log history kept in Redis by the log loop.
- Markers glide between refreshes; a move of more than 150 m in one refresh (respawn at a garrison or OP)
  is shown as a jump.
- Scroll wheel / pinch to zoom, drag to move, buttons to zoom and to show the whole map. Names and squads are
  drawn from 2.5× zoom on (option in the sidebar).
- Search a player to centre and zoom on them. Filters: show/hide a team, highlight one squad (others dimmed).
- Players without a squad ("unassigned", or in the lobby) are hidden from the map by default (option
  "Show players without a squad"); they stay in the search.
- Dead players stay greyed where they died until they spawn again (option "Show dead players where they
  died"). A revive nearby glides, a respawn elsewhere jumps.
- The current layer's grid (letters / numbers) and strongpoints are drawn over the image.
- Warfare / Offensive: each sector is coloured by the team holding it, derived from the score (option
  "Colour sectors by owner" to turn it off). The active strongpoint of each sector is drawn solid; the two
  inactive ones are hidden ("Show inactive strongpoints" shows them faded).

## Permission

The page calls `/api/get_live_map`, which requires `api.can_view_live_map` ("Can view the live map (player
positions)"). It is a fork permission (`rconweb/api/fork_permissions.py`) created by the next `migrate`; assign it
to the relevant groups in the Django admin. Player positions give a big in-game advantage if they leak (ghosting),
so only give it to trusted admins.

## Data sources

- Positions: `worldPosition` of the RCON player info, already parsed by CRCON into `world_position` and returned
  by `Rcon.get_team_view()`. That call is cached in Redis for ~2 s and polled by the automods, so the map adds
  no RCON traffic; polling faster than 2 s would only return cached data.
- Map geometry: `hllrcon` static layer data (`Layer.grid`, `Layer.sectors` → strongpoints). The tactical image
  (`/tac-maps/<map_id>.webp`, shared with the objective picker) is assumed to be square and to cover 10 cells of
  the map's large (Warfare) grid on each axis. Skirmish layers keep that image and draw their own smaller grid.
- Score and remaining time: `Rcon.get_gamestate()` (cached ~2 s). If it fails the map is still returned,
  without the banner.
- Sector owners: the score is the number of sectors each team holds, counted from its spawn side
  (`Map.allies_direction`). Only for layers with 5 sectors of 3 strongpoints (Warfare, Offensive).
- Active strongpoints: the game server has **no command to read the current objectives**. CRCON records the
  layout it set (Maps → Objectives, `set_game_layout`) in `MapsHistory()[0]["game_layout"]["set"]` when the
  next match starts. When that record is empty (layout chosen randomly by the game) or belongs to another layer,
  all 15 strongpoints are drawn dashed and the sidebar says the objectives are unknown. Setting the layout from
  CRCON before each match (even with "random" rows, CRCON then draws them itself) makes them known.
- World axes: `x` towards the east, `y` towards the south (game centimetres), mapped directly onto image pixels.
- A position of exactly (0, 0, 0) is treated as dead / on the deployment screen (`alive: false`). The server
  does not keep the death location: the page remembers each player's last alive position in the browser
  (`deadPlayers.js`) and draws dead players there. It is forgotten when they spawn again, switch team, leave,
  or a new match starts. A player who died before the page was opened has no known position and is not drawn.

If hllrcon does not know the current layer, the page shows a message and no map.

## Implementation

- Backend: `rcon/live_map.py` (pure builders + `live_map_for(rcon)`, tests in `tests/test_live_map.py`) and
  `RconAPI.get_live_map`.
- Frontend: `rcongui/src/pages/views/map/` — `LiveMapCanvas.jsx` (SVG with pan/zoom, no map library), `PlayerLogs.jsx`, `ScoreBar.jsx`,
  `MapSidebar.jsx`, `coordinates.js` (world → image → screen conversions) and `deadPlayers.js` (last known
  positions), tested by `node --test src/pages/views/map/coordinates.test.mjs src/pages/views/map/deadPlayers.test.mjs`.

## Previewing without a game server

With the local stack (`docs/local-dev.md`) and `npm run dev`, open `/views/map?demo`: the page uses fake
players moving around Carentan, a score that changes every 20 s, active objectives and fake kill logs (`rcongui/src/pages/views/map/demo.js`). This mode only
exists in the Vite dev server; production builds do not contain it.

## Known limits

- The axis orientation and image calibration come from hllrcon's data and the tactical images; a map whose
  image is framed differently would show offset positions. Check by comparing your own position in game.
- In Warfare the score assumes every sector is held by one team; a sector being captured keeps its previous
  colour until the score changes.
- Garrisons, outposts and other spawn points cannot be shown: the game server exposes them neither through
  RCON commands nor in the logs.
- Vehicles are not shown separately: crew members appear at the vehicle's position.
