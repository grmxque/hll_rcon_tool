# End of Game Rewards

Fork feature (port of [HLL_CRCON_Top_stats](https://github.com/grmxque/HLL_CRCON_Top_stats), archived in
`docs/legacy-player-messages/top_stats.py`). Configure it from **Settings → Others → End of Game Rewards**
(`/settings/others/end-of-game-rewards`). The configuration is scoped to the game and server. Disabled by
default.

At each match end:

1. every connected player receives a message with the best score of each `top_categories` entry, the TOP 3
   garrison builders and the number of node builders (when those categories are enabled), their own kills,
   kill streak, kills per minute, kill/death ratio and constructions, and the VIP days they won;
2. the rewarded players get VIP days.

## Rewards

Rewards apply only when `vip_rewards_enabled` is on and at least `min_players_for_rewards` players are
connected. They add up per player:

| Rule | Default |
| --- | --- |
| Best player of each `top_categories` entry (combat, offense, defense, support; first player on ties) | 4 days (`top_category_reward_days`) |
| Each individual threshold reached: `kills_threshold`, `kills_streak_threshold`, `kills_per_minute_threshold`, `kill_death_ratio_threshold` (only from `kill_death_ratio_min_kills` kills). A threshold of 0 disables its rule | 70, 10, 0.75, 1.5 (30 kills); 2 days each (`individual_reward_days`) |
| Player already VIP (`fidelity_reward_days`) | 0 days |
| TOP 3 garrison builders (`garrison_rewards_enabled`, at least 1 garrison) | 1 day each (`garrison_reward_days`) |
| Every player who built at least 1 resource node (`node_rewards_enabled`), whatever the count | 1 day (`node_reward_days`) |

Constructions are detected from the support score: see [Actions Tracking](actions-tracking.md)
for the method, its limits and how to validate it before enabling these rewards.

- TOP 3 garrison ties: the player who reached the count first in the match wins, then the lowest player ID.
  At most 3 winners; fewer when fewer players built a garrison.
- Each category gives its days at most once per player and per match.
- `garrison_builder_roles` (default `officer`, `armycommander`) and `node_builder_roles` (default `engineer`)
  are the roles analysed. A garrison only counts once teammates spawn on it (within 5 minutes): commander
  abilities and supplies consumed give the same +50 as a garrison.

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

## Message

Console players cannot display more than 16 lines, so the message has no blank line and stays within 16
lines in the worst case (4 best scores, TOP 3 garrisons, every threshold reached), for example:

```
█ FIN DE PARTIE █ +4 j/TOP
Attaque : Alice (900)
Défense : Bob (800)
Soutien : Carol (600)
Combat : Bob (400)
▒ GARNISONS +1 j ▒
1. grmxque (5)
2. tartanpion (4)
3. martinmatin (3)
Nodes +1 j : 7 constructeurs
▒ grmxque ▒
KILLS 45 (+2 j) · SÉRIE 10
K/MIN 0.81 · K/D 1.6 (+2 j)
GARNISONS 5 (+1 j) · NODES 2 (+1 j)
TU AS GAGNÉ 10 JOURS DE VIP!
```

Disabled or empty sections are left out, names are cut at 20 characters (24 for the player's own name), and
VIP days only show when they are granted.

## Thanks banner

With `thanks_banner_enabled`, after a new detected construction (at most once every
`thanks_banner_interval_minutes`, default 5), the in-game broadcast shows
`Merci pour la construction de garnisons : grmxque (2), tartanpion (3)` then, `thanks_banner_seconds` later
(default 30), `Merci pour la construction des nœuds : grmxque (3), martinmatin (2)`, then the previous
broadcast comes back. Totals of the match so far, sorted like the TOP 3, 8 names at most then `+N autres`.
The next messages and the restore go through the rq worker queue (`workers` service). Nothing is broadcast in
`dry_run` (logged only). The **Auto broadcasts** service, when enabled, can replace the banner at its next
rotation.

## Safety

- Each `MATCH ENDED` log line is processed at most once (Redis key per server and log timestamp, kept 6 hours),
  so a log loop restart can never grant VIP twice. A failure during processing is not retried, for the same
  reason.
- `dry_run` computes everything and writes the audit log and Discord summary, but sends no message and grants
  no VIP.
- Every reward writes an `end_of_game_vip_reward` audit log row (`vip_granted` or `dry_run`) with the reasons
  (`top_offense`, `threshold_kills`, `fidelity`, `top_garrison`, `node_builder`…). `discord_webhook_url`
  receives one summary per game.
- A failure of the actions tracking never prevents the other rewards.
- A failed VIP grant is logged and does not prevent the other rewards.

## Data source and limits

Stats come from the live game stats cache (`get_cached_live_game_stats()`), refreshed every few seconds by the
`live_stats_refresh` service, which must be running. The last seconds of the game may be missing. Messages are
sent through pipelined `MessagePlayer` commands, so the log loop is not blocked by one round trip per player.

## Implementation

`rcon/end_of_game_rewards.py` registers an `on_match_end` log loop hook (imported from `rcon/__init__.py`).
It settles the constructions of the ending match (`final_actions()`, match found by its start in
`MapsHistory`), then `compute_rewards()` applies the rules without side effects and `process_end_of_game()` sends
the messages, grants VIP with `Rcon.add_vip()` and reports. Construction detection: `rcon/actions_tracking/`.
