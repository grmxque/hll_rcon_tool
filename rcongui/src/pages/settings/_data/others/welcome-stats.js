const WelcomeStats = `
Welcome Stats privately sends each player their all-time stats when they connect,
after delay_seconds (default 20, so the player has loaded in and can read it).

The message shows the VIP status, played games, cumulated play time, kill/death ratio,
then the players they killed the most (victims) and who killed them the most (nemesis).
Stats cover every game recorded by CRCON; top_count sets how many victims and nemesis
are listed (0 hides both sections). language selects English or French labels.

Players with fewer than min_games recorded games receive new_player_message instead,
if it is not empty. Placeholder: {player_name}.
`;

export default WelcomeStats;
