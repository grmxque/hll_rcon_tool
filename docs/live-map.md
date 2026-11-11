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
- Scroll wheel / pinch to zoom, drag to move, buttons to zoom and to show the whole map. Names are drawn from
  2.5× zoom on (option in the sidebar). The option "Show squad letter before names" (off by default) prefixes
  them with the squad initial, e.g. `A · grmxque` for Able (`CMD` for the commander).
- Search a player to centre and zoom on them. Filters: show/hide a team, highlight one squad (its players are
  drawn in orange, above the others).
- Players without a squad ("unassigned", or in the lobby) are hidden from the map by default (option
  "Show players without a squad"); they stay in the search.
- Dead players stay greyed where they died until they spawn again (option "Show dead players where they
  died"). A revive nearby glides, a respawn elsewhere jumps.
- The current layer's grid (letters / numbers) is drawn over the image.
- Warfare / Offensive: each sector is coloured by the team holding it, derived from the score (option
  "Colour sectors by owner" to turn it off).
- Garrisons detected by the [actions tracking](actions-tracking.md) are drawn with the game's garrison icon on
  a team-coloured disc, under the players (option "Show garrisons", which also gives the counts; the team filters apply):
  - **possible** (see-through, dashed outline): the builder's score shows a garrison, not confirmed yet (a
    commander ability or supplies consumed give the same points). Those of the commander are not drawn: his
    +50 are nearly always abilities;
  - **confirmed** (opaque): teammates spawned on it. It disappears after 20 minutes without a spawn on it
    (destruction is not observable).

  The tooltip gives the builder, its age and the last spawn on it. Requires the actions tracking to run
  (End of Game Rewards enabled with a construction category, the banner or the snapshot recording); otherwise
  no garrison is shown.
- Spawn points seen from the spawns of the match ([actions tracking](actions-tracking.md#spawn-points)) are
  drawn the same way under the garrisons (option "Show spawn points", with the counts; the team filters
  apply): the game's outpost and airhead icons (a possible airhead is see-through with a dashed outline), `H`
  for an HQ (no icon), the garrison icon for a garrison only seen from the spawns (not counted for its builder: built before the tracking started, or not confirmed by
  the rules of the rewards). A point appears 20 s after the spawns that reveal it; outposts and airheads
  disappear 5 minutes after their last spawn, HQs stay. Points with too few spawns to be classified are not
  drawn. The tooltip gives the player credited for the spawns, the number of spawns and the last one.
- Front line (Warfare / Offensive, sector owners from the score): garrisons and outposts of a team disappear
  as soon as the other team captures their sector (the game destroys them). On the front line, a sector the
  team does not hold or one of its sectors next to it, a garrison or spawn point nobody spawned on for
  5 minutes is no longer drawn (probably destroyed); behind the front the 20 minutes apply. A candidate counts
  from its detection. HQs always stay.

## Permission

The page calls `/api/get_live_map`, which requires `api.can_view_live_map` ("Can view the live map (player
positions)"). It is a fork permission (`rconweb/api/fork_permissions.py`) created by the next `migrate`; assign it
to the relevant groups in the Django admin. Player positions give a big in-game advantage if they leak (ghosting),
so only give it to trusted admins.

## Data sources

- Positions: `worldPosition` of the RCON player info, already parsed by CRCON into `world_position` and returned
  by `Rcon.get_team_view()`. That call is cached in Redis for ~2 s and polled by the automods, so the map adds
  no RCON traffic; polling faster than 2 s would only return cached data.
- Map geometry: `hllrcon` static layer data (`Layer.grid`, `Layer.sectors`). The tactical image
  (`/tac-maps/<map_id>.webp`, shared with the objective picker) is assumed to be square and to cover 10 cells of
  the map's large (Warfare) grid on each axis. Skirmish layers keep that image and draw their own smaller grid.
- Score and remaining time: `Rcon.get_gamestate()` (cached ~2 s). If it fails the map is still returned,
  without the banner.
- Garrisons and spawn points: the actions tracking state of the match in Redis
  (`rcon.actions_tracking.tracker.live_tracking`), updated by the log loop every 3 to 4 s. If it cannot be
  read the map is returned without them.
- Sector owners: the score is the number of sectors each team holds, counted from its spawn side
  (`Map.allies_direction`). Only for layers with 5 sectors of 3 capture zones (Warfare, Offensive).
- World axes: `x` towards the east, `y` towards the south (game centimetres), mapped directly onto image pixels.
- A position of exactly (0, 0, 0) is treated as dead / on the deployment screen (`alive: false`). A player in
  the menus (just connected, choosing a team or a role) is reported at a fixed position near the edge of the map,
  and a body stays at the place of the death for a few seconds: after connecting while the page is open, dying or
  changing team, a player is drawn alive only once his position changes (`deadPlayers.js::updateDeployment`).
  Players already there when the page opens are drawn as reported. The server
  does not keep the death location: the page remembers each player's last alive position in the browser
  (`deadPlayers.js`) and draws dead players there. It is forgotten when they spawn again, switch team, leave,
  or a new match starts. A player who died before the page was opened has no known position and is not drawn.

If hllrcon does not know the current layer, the page shows a message and no map.

## Implementation

- Backend: `rcon/live_map.py` (pure builders + `live_map_for(rcon)`, tests in `tests/test_live_map.py`) and
  `RconAPI.get_live_map`.
- Map icons: `assets/hll/images/icons/map/` (garrison, outpost, airhead; copied to `rcongui/public/` like the
  other HLL images), taken from
  [maps-let-loose](https://github.com/mattwright324/maps-let-loose) by mattwright324; they are game assets
  © Team17. Drawn white (SVG filter) on the team colour.
- Frontend: `rcongui/src/pages/views/map/` — `LiveMapCanvas.jsx` (SVG with pan/zoom, no map library), `PlayerLogs.jsx`, `ScoreBar.jsx`, `garrisons.js` and `spawnPoints.js` (filtering and tooltips),
  `MapSidebar.jsx`, `coordinates.js` (world → image → screen conversions), `deadPlayers.js` (last known
  positions) and `playerLabel.js` (marker labels), tested by `node --test src/pages/views/map/*.test.mjs`.

## Previewing without a game server

With the local stack (`docs/local-dev.md`) and `npm run dev`, open `/views/map?demo`: the page uses fake
players moving around Carentan, a score that changes every 20 s, a few garrisons (confirmed and possible), spawn points and fake kill logs (`rcongui/src/pages/views/map/demo.js`). This mode only
exists in the Vite dev server; production builds do not contain it.

## Known limits

- The axis orientation and image calibration come from hllrcon's data and the tactical images; a map whose
  image is framed differently would show offset positions. Check by comparing your own position in game.
- In Warfare the score assumes every sector is held by one team; a sector being captured keeps its previous
  colour until the score changes.
- Strongpoints are not shown: the game server has no command to read the active objectives of the match in
  progress, so drawing the 15 possible ones would be misleading.
- Garrisons are those detected from the scores (see above), with the actions tracking delay and limits: a
  garrison nobody spawns on stays "possible" for 20 minutes (it can still be confirmed later). Besides a
  capture of its sector, a destroyed garrison stays drawn until nobody spawned on it for 20 minutes (5 on the
  front line). Sector owners come from the score, so a sector being captured counts only once the score
  changes. The game server exposes no spawn point (neither RCON commands nor logs): HQs, outposts and
  airheads are inferred from where players spawn and who gets the credits, so an outpost nobody used yet is
  not shown, and the spotters' outposts are not told apart (their credits are not analysed).
- Vehicles are not shown separately: crew members appear at the vehicle's position.
