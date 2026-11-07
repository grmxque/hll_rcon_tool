# Welcome Stats

Fork feature (port of [HLL_CRCON_All_time_stats](https://github.com/grmxque/HLL_CRCON_All_time_stats),
archived in `docs/legacy-player-messages/all_time_stats.py`). Configure it from
**Settings → Others → Welcome Stats** (`/settings/others/welcome-stats`). The configuration is scoped to the
game and server and read at each connection. Disabled by default.

When a player connects, a private message is sent after `delay_seconds` with:

- VIP status (not VIP, unlimited, or expiration date in UTC);
- played games, cumulated play time and kill/death ratio;
- the `top_count` players they killed the most (victims) and who killed them the most (nemesis).

| Setting | Default | Meaning |
| --- | --- | --- |
| `enabled` | false | |
| `language` | en | `en` or `fr` labels |
| `delay_seconds` | 20 | Wait so the player has loaded in; a message sent earlier is lost |
| `min_games` | 1 | Players with fewer recorded games do not receive their stats |
| `new_player_message` | empty | Sent instead to players below `min_games`; empty disables. Placeholder `{player_name}` |
| `top_count` | 3 | Victims and nemesis listed (0 hides both sections) |

## Data sources

- Games, kills and deaths: sum of the player's `player_stats` rows (one per recorded game, all servers).
- Play time: `PlayerID.get_total_playtime_seconds()` (player sessions).
- Victims and nemesis: `KILL` rows of `log_lines`, aggregated on the indexed `player1_steamid` /
  `player2_steamid` columns, with each opponent's most recently seen name. They require the log recorder
  service; without recorded logs the sections are empty and hidden.
- VIP: `Rcon.get_vip_ids()`. A VIP without expiration record is shown as unlimited.

## Implementation

`rcon/welcome_stats.py` registers an `on_connected` log loop hook (imported from `rcon/__init__.py`). The hook
only starts a daemon timer; queries and the RCON message run in that thread, so the log loop is never delayed.
Errors are logged and never propagated. Messages are sent with `save_message=False`, so they do not fill the
player's message history.
