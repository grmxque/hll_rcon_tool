# End of Game Rewards

Fork feature (port of [HLL_CRCON_Top_stats](https://github.com/grmxque/HLL_CRCON_Top_stats), archived in
`docs/legacy-player-messages/top_stats.py`). Configure it from **Settings → Others → End of Game Rewards**
(`/settings/others/end-of-game-rewards`). The configuration is scoped to the game and server. Disabled by
default.

At each match end:

1. every connected player receives a message with the best score of each `top_categories` entry, their own
   kills, kill streak, kills per minute and kill/death ratio, and the VIP days they won;
2. the rewarded players get VIP days.

## Rewards

Rewards apply only when `vip_rewards_enabled` is on and at least `min_players_for_rewards` players are
connected. They add up per player:

| Rule | Default |
| --- | --- |
| Best player of each `top_categories` entry (combat, offense, defense, support; first player on ties) | 4 days (`top_category_reward_days`) |
| Each individual threshold reached: `kills_threshold`, `kills_streak_threshold`, `kills_per_minute_threshold`, `kill_death_ratio_threshold` (only from `kill_death_ratio_min_kills` kills). A threshold of 0 disables its rule | 70, 10, 0.75, 1.5 (30 kills); 2 days each (`individual_reward_days`) |
| Player already VIP (`fidelity_reward_days`) | 0 days |

- Every player present in the final stats can be rewarded, connected or not. Only connected players are
  messaged.
- A new VIP gets the days from now. An existing VIP is extended from its expiration, or from now if it has
  already expired.
- Unlimited VIPs (and VIPs without expiration record) are never modified.
- The VIP description is `"<player name> - <vip_description>"`.
- Expired rewards are removed by the upstream **Expired VIP** service, which must stay enabled.

Differences from the original plugin:

- the player minimum now applies to all rewards, not only to the best scores;
- individual rewards are also granted to players who left before the end;
- an expired VIP is extended from now instead of from its past expiration date.

## Safety

- Each `MATCH ENDED` log line is processed at most once (Redis key per server and log timestamp, kept 6 hours),
  so a log loop restart can never grant VIP twice. A failure during processing is not retried, for the same
  reason.
- `dry_run` computes everything and writes the audit log and Discord summary, but sends no message and grants
  no VIP.
- Every reward writes an `end_of_game_vip_reward` audit log row (`vip_granted` or `dry_run`). `discord_webhook_url`
  receives one summary per game.
- A failed VIP grant is logged and does not prevent the other rewards.

## Data source and limits

Stats come from the live game stats cache (`get_cached_live_game_stats()`), refreshed every few seconds by the
`live_stats_refresh` service, which must be running. The last seconds of the game may be missing. Messages are
sent through pipelined `MessagePlayer` commands, so the log loop is not blocked by one round trip per player.

## Implementation

`rcon/end_of_game_rewards.py` registers an `on_match_end` log loop hook (imported from `rcon/__init__.py`).
`compute_rewards()` applies the rules without side effects. `process_end_of_game()` sends the messages, grants VIP
with `Rcon.add_vip()` and reports.
