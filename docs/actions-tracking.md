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
| `context.py` | `PollContext` given to the detectors: current samples, previous samples, support gains, `SpawnLedger` (teammate spawns and learned spawn points; a spawn credits a single player) |
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
**candidate**; a garrison nobody spawns on within 5 minutes is never counted.

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

**Confirmation.** A candidate is confirmed when, in one poll:

- teammates spawn within 50 m of the build position, and clearly closer to it (15 m) than to a spawn point
  the builder already had (his outpost, his garrisons);
- the builder's support gain is **exactly** the credits of some of those spawns (+5 squad member, +3 other
  teammate; the credit of a spawn can come with the next poll), plus credits of spawns on his other known
  spawn points, plus possibly an outpost tick or a marking;
- and his other observed credits alone do not explain that gain: then those teammates spawned at a point that
  credits nobody;
- the spot is not one where teammates already spawned without crediting anybody before the candidate (an HQ).

Only the owner of a spawn point gets credits, so HQ spawns next to a commander, or next to an officer whose
supplies were just consumed by someone else, confirm nothing. When a builder has several candidates near the
wave, the closest one is confirmed. Candidates not confirmed within 5 minutes, or at the end of the match, are
rejected.

**Data gaps** (more than 20 s between two polls: RCON outage, service restart): the positions during the gap
are unknown, so a garrison built then cannot be confirmed and is not counted.

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
  ```

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

- A garrison nobody spawns on within 5 minutes is not counted (simulation: about 14 % of the garrisons). Among
  the garrisons used, about 8 % are missed: their spawn waves come with other gains of the builder that the
  table cannot explain exactly, or the builder left.
- A garrison rebuilt in place of the builder's own previous one cannot be told from it (its spawns are those
  of the old spot).
- A garrison built during a data gap is not counted.
- A commander ability used during a spawn wave on a fresh point right next to him that credits him exactly
  could be confirmed (simulation: about one in 40 matches).
- A node and a repair station are identical when built; a node destroyed before its first tick stays
  unconfirmed. A repair station built in the last 150 s of a match counts as a node.
- Constructions before the first poll of the tracking (CRCON started mid-match) are invisible, except nodes
  found through their passive production.
- HLL (WW2) only.

## Tests

- `tests/test_actions_detection.py`: scenarios poll by poll (`tests/actions_scenarios.py`), each checking false
  positives and false negatives, plus a seeded simulation of whole matches (`tests/actions_simulation.py`:
  players walking across the map, synchronised spawn waves on the HQ, outposts and garrisons, garrisons
  destroyed at random, supplies consumed and credited to their owner (the builder, another squad leader, a
  support), nodes within the player / team limits, repair stations, commander abilities, polls every
  2.5–4.5 s, optional outage) that measures the error rates against the ground truth, separating the garrisons
  someone spawned on. The simulation applies the same score table and rules: it tests the decision logic, not
  the game values.
- `tests/test_actions_tracking.py`: Redis persistence, restart, idempotence, banner, recording and replay, log
  loop hook.
