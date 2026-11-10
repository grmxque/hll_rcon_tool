const EndOfGameRewards = `
At the end of each game, every connected player receives the best scores, their own
stats and the VIP days they won (send_stats_message), and the best players are rewarded
with VIP (vip_rewards_enabled).

VIP days, only with at least min_players_for_rewards connected players:
- top_category_reward_days for the best player of each top_categories entry
  (combat, offense, defense, support);
- individual_reward_days for each threshold reached: kills, kill streak, kills per
  minute, kill/death ratio (with at least kill_death_ratio_min_kills kills).
  A threshold of 0 disables its rule;
- fidelity_reward_days for players who are already VIP;
- garrison_reward_days for the 3 players who built the most garrisons
  (garrison_rewards_enabled; ties: the first to reach the count wins);
- node_reward_days for every player who built at least one resource node
  (node_rewards_enabled).
Constructions are detected from the support score of the roles listed in
garrison_builder_roles and node_builder_roles: see docs/actions-tracking.md, and
check them with dry_run and record_actions_snapshots before granting VIP.
thanks_banner_enabled broadcasts the builders of the match after a new construction
(at most every thanks_banner_interval_minutes), then restores the previous broadcast.
Rewards add up. A new VIP gets the days from now, an existing VIP is extended from its
expiration date (or from now if it has expired). Unlimited VIPs are never changed.
The VIP is stored as "<player name> - vip_description" and removed at expiration by
the Expired VIP service, which must be enabled.

Each match end is processed once. Stats come from the live game stats, which are
refreshed every few seconds. dry_run only logs, audits and posts to Discord, without
messaging players or granting VIP. The Discord webhook receives a summary of the
rewards of each game. language selects English or French labels.
`;

export default EndOfGameRewards;
