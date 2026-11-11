# Actions Tracking

Fork feature: detects player actions that the game server does not report, from the counters it does report.
Today: **garrisons** and **resource nodes**, used by the [End of Game Rewards](end-of-game-rewards.md)
construction categories and the thanks banner. The module is built to receive other actions (supply boxes,
vehicle destructions…): see [Adding an action](#adding-an-action).

The game server exposes no construction event (no log line, no command): the only trace of a garrison or a
node is the **support score** it gives its builder. Each detector explains the change of a player's counters
between two polls with the score sources that can produce it, within what the game makes physically possible.

## Data

- **No extra RCON call.** The log loop already polls `get_detailed_players()` (`GetAllPlayerInfo`) every 3 to
  4 s (`LogLoop.update_maps_history`). `observe_poll()` receives that poll right after `record_player_stats`.
- Per player: scores (combat, offense, defense, support), kills, deaths, vehicle kills / destroyed, role,
  loadout, level, team, squad (`unit_id`) and world position. A position of `(0, 0, 0)` means dead or on the
  deployment screen. All of them are recorded, so recordings can be replayed for actions added later.
- State per server and per match (map start timestamp) in Redis, `actions_tracking:<server>:<start>`
  (JSON, kept 6 h). Every call reloads it, so a log loop restart resumes the match; a new match starts
  empty; a poll that is not newer than the last one is ignored.

## Structure (`rcon/actions_tracking/`)

| File | Role |
| --- | --- |
| `model.py` | `PlayerSample` / `PollSnapshot` (polled data), `Decision` (diagnostics), `PlayerActions` (count per action and when it was reached), `rank_players()` |
| `rules.py` | `ScoreTable` (what the game gives) and `GameRules` (what it makes impossible) |
| `context.py` | `PollContext` given to the detectors: current samples, previous samples, support gains, spawns and spawn points |
| `spawns.py` | `SpawnLedger` (recent teammate spawns and the spawn points learned for each player; a spawn credits a single player) and `SpawnPoints` (every spawn grouped by position and classified: HQ, garrison, outpost, airhead) |
| `sectors.py` | sectors and their owners (from the score), captures, front line |
| `detectors/` | one `ActionDetector` per action (`garrison.py`, `node.py`), registered in `detectors/__init__.py` |
| `engine.py` | `ActionsEngine`: counter checks (reconnection, team change, stats leak), spawns, runs the detectors, summary. Pure, JSON state |
| `tracker.py` | Redis persistence, log loop entry point, end of match, snapshot recording |
| `banner.py` | thanks broadcast |
| `__main__.py` | replay of recorded polls |

## Score table and game rules

`ScoreTable`, from the
[community score table](https://docs.google.com/spreadsheets/d/1eN4f7J_4jXreYCdaU082kOdaYTFAsPp7tZvMY7vgKrM).
**To be validated in game** (see below).

| Source (to the builder) | Support |
| --- | --- |
| Garrison built | 50 |
| Node built / blueprint bonus | 50 / 40 (90 when the same engineer places and builds it) |
| Node passive production | 10 per minute per node |
| A squad member / another teammate spawns on the player's spawn point | 5 / 3 |
| Outpost | 10 per 60 s |
| Marking | 5 per 20 s |
| Blowtorch repair | 5 per 5 s |
| Repair station | 50 / 40 (same as a node) |
| Commander abilities | 50 to 100 (several give exactly 50) |
| Supplies consumed by a construction (to whoever placed them: truck crate, support box, drop) | 1 per supply, 50 per garrison or node |

The supplies credit was observed in game: an officer building a garrison on his own truck crate gets +100.

`GameRules`, what cannot happen whatever the score says:

| Rule | Value |
| --- | --- |
| Garrisons per builder between two polls | 1 |
| Nodes per player | 3 (one of each type) |
| Nodes per team | 9 |
| Nodes of the same type per team | 3: **not checked**, the node type is not observable |

## Garrisons

Only players whose role was in `garrison_builder_roles` at the previous poll are analysed (changing class
requires dying, so the role of the construction is the role before). Default: `officer` (squad leaders) and
`armycommander`. The roles are validated against the HLL role catalog.

**A garrison is only counted once teammates spawn on it.** Several sources give the same +50 as a garrison:
commander abilities (Encouraged, Airhead, Final Stand, Recon Plane, Reinforce, Supply Drop, Ammo Drop), the
supplies of a player consumed by someone else's construction, a spawn wave. The support gain only makes a
**candidate**; a garrison nobody ever spawns on is never counted.

**Deployed players.** Seen in game: a dead player is reported at (0, 0, 0), but a player in the menus (just
connected, choosing a team or a role) is reported at a fixed position near the edge of the map, and a body stays
at the place of the death for about 4 s with the death already counted. A redeploy from the menu behaves like a
death. So after connecting, dying or changing team, a player is treated as dead (at (0, 0, 0)) until his
position changes (`ActionsEngine._deployed`). A player first seen when the tracking starts mid-match and who
only walks from there was already in the game: no spawn.

**Spawns.** A teammate spawned when they come back alive after being dead or on the deployment screen, unless
they are within 25 m of where they went down without a new death (revive), or when their deaths increase with
a jump of more than 100 m between two polls. Spawns of the previous poll stay usable once (the credit and the
new position are not always reported by the same poll).

**Candidate.** The gain is explained in two ways, never by an exact match on 50:

- **H0, no construction**: `gain = 5 × squad spawns + 3 × other spawns + outpost + marking`, bounded by the spawns
  observed in the team;
- **H1, one garrison**: the same with 50 added, or 100 when built on the player's own supplies.

| H0 | H1 | Decision |
| --- | --- | --- |
| impossible | exact | candidate |
| exact | impossible | none |
| impossible | impossible | an unknown source is involved: candidate only if H1 leaves ≤ 20 points unexplained and explains at least 40 points that H0 cannot |
| exact | exact | none if the spawns appeared on a known spawn point of the player (his outpost, his garrisons), candidate otherwise |

The player must have been alive during the interval. Builders are processed in this order: unambiguous ones
first, then those whose spawns are on their own spawn point, so that they claim their spawn credits before the
ambiguous ones are analysed (a spawn credits only one player).

**Confirmation.** A candidate is confirmed when, over the last poll (or the last two: the credit of a spawn
can come one poll after it):

- teammates spawned within 50 m of the build position, clearly closer to it (15 m) than to a spawn point the
  builder already had (his outpost, his garrisons);
- the builder's support gain is **exactly** made of: credits of some of those spawns (+5 squad member, +3
  other teammate), credits of spawns on his other known spawn points, possibly an outpost tick or a marking,
  and the value of a garrison he built in the same poll (+50, or +100 on his own supplies);
- in that explanation, the spawns on his own known points are all counted first (they do credit him), and the
  wave is still needed: otherwise those teammates spawned at a point that credits nobody. Spawns also near
  another player's spawn point or candidate, or clearly closer to a teammate's confirmed garrison, are not
  counted as his;
- the wave is not only made of the builder's own squad: an outpost he just placed there gives the same +5 per
  squad member. A squad-only wave is enough when his squad also spawned, since the candidate, on an outpost of
  his elsewhere (a squad has one outpost at a time). Otherwise the candidate waits for a spawn of another
  squad, of the commander or of a player without a squad on it, in a poll where the builder got a spawn
  credit;
- the spot is not an HQ: at least 3 teammates spawned there without crediting anybody before the
  candidate existed, or since then while the builder, connected, gained nothing in that poll nor in the
  next one. A wave whose credits did not match exactly does not count: the builder did gain something.
  Nor is it on a spawn point classified `hq` or `airhead` (see [Spawn points](#spawn-points)): they credit
  nobody.

Only the owner of a spawn point gets credits, so HQ spawns next to a commander, or next to an officer whose
supplies were just consumed by someone else, confirm nothing. When a builder has several candidates near the
wave, the closest one is confirmed and his other candidates within 100 m are rejected (they would share its
spawns). Spawns near another player's candidate are left to that player when explaining his own gains.
A candidate not confirmed within 5 minutes becomes **dormant** until the end of the match (a garrison built in
advance and used later). It can still be confirmed by the rule above, or by teammates of **at least two
squads** spawning on it in one poll (players without a squad count as one more group): an outpost only takes
its own squad. The builder, if still connected in the team, must then have got at least one spawn credit in that
poll, the exact amount is not required. The spot must not be an HQ, and those spawns must be clearly closer to
the candidate (15 m) than to a teammate's spawn point or candidate: a teammate may have built a garrison next to
it since. A dormant candidate is also confirmed by a single spawn of a player who has no outpost to spawn on:
the commander, or a player without a squad.

The commander only spawns at the HQ or on a garrison. His own spawn within 50 m of one of his candidates, after
it was created, confirms it (it credits nobody, so the exact rule above cannot see it), unless he had already
spawned at that spot before the candidate (an HQ), teammates spawned there without crediting anybody (an HQ),
or the spawn is not clearly closer to the candidate (15 m) than to a teammate's spawn point or candidate. Nor
for a candidate made in the first 3 min of the tracked match: the commander stands at the HQ then, and the HQ
point is not known yet. Dormant candidates are rejected at the end of the match.

Players without a squad have no unit (CRCON gives `unit_id` 0 to both Able and them): their spawn credits a
garrison builder +3, never +5.

**Data gaps** (more than 20 s between two polls: RCON outage, service restart): a player back in the game with
a new death after the gap respawned during it, somewhere unknown, so his next move is not taken as a spawn. The
positions during the gap
are unknown, so a garrison built then cannot be confirmed and is not counted.

## Spawn points

`SpawnPoints` (`spawns.py`) groups every spawn the polls saw into points: a spawn joins the point of its team
within 50 m, or starts a new one; the position is the mean of its spawns. A spawn joins its point 20 s after it
happened, once the detectors had the time to give its credit to a spawn point owner. A point nobody used for
20 min is forgotten (destroyed, moved, or no longer used). Each point keeps its spawns per squad, those of the
commander and of players without a squad, the credits per owner and the uncredited spawns, and is classified:

The HQs are at the start of each team, never in the middle of the map. With the sectors known (Warfare,
Offensive: see [Sector captures](#sector-captures)), a point **away from the team's home sector** (the end of
the row it starts from) is never an HQ:

| Kind | Evidence, away from the home sector |
| --- | --- |
| `outpost` | only one squad spawned there, from the first spawn (outpost spawns are not always seen crediting the officer) |
| `airhead` | several squads, credits nobody, used for 3 min at most (possible) |
| `garrison` | several squads, the commander or players without a squad (they never use an outpost) |

In the home sector, or when the sectors are unknown (Skirmish, older recordings):

| Kind | Evidence |
| --- | --- |
| `garrison` | it credits its owner and is used by several squads, the commander or players without a squad |
| `outpost` | it credits its owner and only his squad spawns there (at least 2 spawns) |
| `hq` | in the home sector, several squads from the first 3 min of the match; or at least 3 teammates spawned there without crediting anybody, and it was used within 3 min of the start of the tracked match or for more than 10 min |
| `airhead` | at least 3 uncredited spawns of several squads, placed during the match, not an HQ (possible) |
| `unknown` | anything else: one spawn, few uncredited spawns, uncredited spawns of one squad, the commander alone (he also spawns on garrisons) |

A garrison confirmed by the garrison detector always makes its point a `garrison`. Two garrisons of a team are
never within 200 m (game rule): a `garrison` point that close to a confirmed garrison or to a more used one
becomes `unknown`. Only officers and the commander get their spawn credits analysed (the garrison builder
roles). The airhead and HQ timings are assumptions to check against recorded matches (`replay --spawns`).

## Sector captures

When a team captures a sector, the game destroys every garrison and outpost of the other team in it. It is
the only destruction the polls can date. At each poll, `tracker.sectors_of()` reads the sectors of the layer
and their owners as the live map derives them from the score (`rcon.live_map.build_layer`: Warfare and
Offensive only; cached RCON reads, no extra traffic). They are part of the recorded polls, so a replay
reproduces the captures.

A capture is a sector whose last known owner was one team and is now the other one (an unknown or neutral
owner in between is ignored; nothing is inferred from the first poll with sectors). Then, for the team that
lost the sector:

- its garrison candidates in the sector are rejected (they can no longer be spawned on, so never confirmed);
- its confirmed garrisons there stay counted for the rewards, but are no longer drawn on the live map nor
  given the spawns around them, and the spawn points its players had there are forgotten;
- its spawn points there (`SpawnPoints`) are deleted, a point classified as an HQ included (an HQ is never
  in a capturable sector, so it was a mistake), including the spawns not settled yet;
  spawns there after the capture start new points.

Each capture is logged by the log loop and printed by `replay --spawns`.

## Nodes

Players whose role was in `node_builder_roles` (default `engineer`) are analysed, plus anyone who already has a
passive production stream (the nodes keep producing after a class change).

- The gain is decomposed into blueprint (40), construction (50), passive ticks of the known streams (10 each),
  new passive streams (10 each) and repairs (5). At equal unexplained part, passive ticks and repairs are
  preferred over a construction.
- A player who already has 3 nodes (producing or awaiting confirmation), or whose team has 9, cannot build
  another one: no candidate. New streams never take a player or a team beyond those limits.
- A construction value is **pending** until passive production confirms it: a new +10 stream within 150 s
  proves a node. Without it the candidate is rejected (repair station, node destroyed before its first tick).
  It is counted when the match ends or the player leaves before the end of those 150 s.
- A regular +10 every 60 s (3 ticks) on an engineer proves a node even if its construction was missed (gap,
  tracking started after it). Passive production alone never counts as a *new* construction: the node count is
  the larger of the confirmed constructions and the most simultaneous streams.

## Consumers

- [End of Game Rewards](end-of-game-rewards.md): TOP 3 garrison builders, node builders, thanks banner.
- [Live map](live-map.md): garrison candidates, confirmed garrisons still in use and the classified spawn
  points (`tracker.live_tracking()`, `GarrisonDetector.live()`, `SpawnPoints.live()`).

## Diagnostics

- Each decision is logged by the log loop (`Action <kind> <verdict>: <name> <player_id> (+gain in Ns) <reason>
  <details>`) and pushed to the Redis list `actions_tracking:<server>:<start>:decisions` (last 2000). Verdicts:
  `none`, `pending` (garrison candidate, node awaiting its production), then `confirmed` / `rejected`;
  `reset` / `ignored` for the counters. The details show both hypotheses: unexplained points, squad and team
  spawns used, spawns available, near the player's points and claimable, alive state, role.
- `record_actions_snapshots` writes every poll of the match to
  `$LOGGING_PATH/actions_snapshots/<server>_<start>.jsonl` (20 last files kept). Replay a file:

  ```bash
  python -m rcon.actions_tracking replay /logs/actions_snapshots/1_1762800000.jsonl [--player ID] [--all]
  python -m rcon.actions_tracking replay /logs/actions_snapshots/1_1762800000.jsonl --trace grmxque
  python -m rcon.actions_tracking replay /logs/actions_snapshots/1_1762800000.jsonl --spawns
  ```

  `--spawns` prints every spawn point of the match with its kind, squads, credits and the reason.
  `--trace` prints every poll of one player (ID or part of the name): position, deaths, role, support, jumps
  of more than 100 m and the spawns the tracking detected.

  Outside the container: `DEBUG=1 SERVER_NUMBER=1 HLL_GAME=hll HLL_MAINTENANCE_CONTAINER=1 uv run python -m ...`.

## Validation before enabling the rewards

1. Enable `enabled`, the two categories, `dry_run` and `record_actions_snapshots`.
2. Play a few matches noting the real constructions (garrisons, nodes, outposts, repair stations, commander
   abilities, spawn waves, destroyed garrisons) with their time and builder.
3. Replay the files and compare. To check first: +50 for a garrison, +5 / +3 per spawn, how often the passive
   production of a node ticks and whether it goes to the builder, the blueprint bonus, the position reported
   for dead and revived players, whether `deaths` counts a downed player.
4. Adjust `ScoreTable` / `GameRules` if needed, then disable `dry_run`.

## Adding an action

1. Write a subclass of `ActionDetector` (`detectors/base.py`) in `detectors/<action>.py`:
   - `action` (name used in `PlayerActions.counts`, the decisions and the settings) and `default_roles`;
   - `load(state)` / `to_state()`: its own JSON state (persisted between polls);
   - `observe(ctx)`: decisions for the interval, from `ctx.gains` (support gains), `ctx.current` /
     `ctx.players` (current and previous samples) and `ctx.spawns`;
   - `finalize(end_ts, names)` for what is pending at the end of the match, and `results()`: count and when it
     was reached, per player.
2. Add its score values to `ScoreTable` and its physical limits to `GameRules`.
3. Register it in `DETECTORS` (`detectors/__init__.py`). If it compares a counter other than the support
   score, keep that counter in `ActionsEngine._remember()` (the recordings already contain all of them).
4. Write its scenarios with `tests/actions_scenarios.py` and extend the simulation
   (`tests/actions_simulation.py`) to measure its error rates; replay real recordings.
5. A consumer (reward category, banner line) uses `PlayerActions.count(action)` and `rank_players()`.

## Known limits

- A garrison nobody spawns on is not counted (simulation: about 15 % of the garrisons, mostly destroyed before
  being used). Among the garrisons used, about 6.5 % are missed (simulation, no false garrison in 40 matches):
  mostly garrisons only the builder's squad used (an outpost gives the same credits), then a credit that
  matches two possible spawns is given to the one on the builder's known spawn point, or the builder left.
- A garrison rebuilt in place of the builder's own previous one cannot be told from it (its spawns are those
  of the old spot).
- A garrison built during a data gap is not counted (its position is unknown).
- A garrison less than 100 m from another one of the same builder is counted once.
- A commander ability used during a spawn wave on a fresh point right next to him that credits him exactly
  could be confirmed.
- A commander who spawns on an Airhead placed right where he scored +50 for another ability would confirm a
  false garrison (he places the Airhead from the map, usually far from where he stands).
- A node and a repair station are identical when built; a node destroyed before its first tick stays
  unconfirmed. A repair station built in the last 150 s of a match counts as a node.
- Constructions before the first poll of the tracking (CRCON started mid-match) are invisible, except nodes
  found through their passive production.
- A player who spawns and stays perfectly still is only seen as spawned when he first moves.
- HLL (WW2) only.

## Tests

- `tests/test_actions_detection.py`: scenarios poll by poll (`tests/actions_scenarios.py`), each checking false
  positives and false negatives, plus a seeded simulation of whole matches (`tests/actions_simulation.py`:
  players walking across the map, synchronised spawn waves on one of the 3 HQs, outposts (moved by their
  officer), airheads and garrisons, the commander dying and respawning at an HQ or on a garrison, garrisons
  destroyed at random, supplies consumed and credited to their owner (the builder, another squad leader, a
  support), nodes within the player / team limits, repair stations, commander abilities, polls every
  2.5–4.5 s, optional outage) that measures the error rates against the ground truth, separating the garrisons
  someone spawned on, and the precision of the spawn point classification. The simulation applies the same score table and rules: it tests the decision logic, not
  the game values.
- `tests/test_actions_spawn_points.py`: spawn point grouping and classification.
- `tests/test_actions_tracking.py`: Redis persistence, restart, idempotence, banner, recording and replay, log
  loop hook.
