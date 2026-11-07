const AntiSteamroll = `
Anti-Steamroll protects the losing team's last Warfare sector while the score is 4-1,
to avoid a game ending before the time limit.

The entire final sector is protected (all three possible capture zones), until the
remaining match time reaches allow_final_attack_remaining_minutes (default 45).
Nothing happens below min_players, on unknown layers or outside Warfare.

Players of the winning team found inside that sector for about 10 seconds receive
warnings, then punishments, then a kick (0 skips a stage, -1 repeats it forever).
Players in immune_roles are never checked. Available roles: armycommander, officer,
rifleman, assault, automaticrifleman, medic, spotter, support, heavymachinegunner,
antitank, engineer, tankcommander, crewman, sniper (HLL Vietnam has its own list).

Announcements are sent once each time the score becomes 4-1: winning_team_message to
the team holding 4 points, losing_team_message to the team holding 1 point, and
protection_lifted_message to everyone when the remaining time is reached. Leave a
message empty to disable it.

dry_run only logs and audits, without messaging or sanctioning anyone. The Discord
webhook receives the announcements and every warning, punish and kick.

Announcement placeholders: {remaining_minutes}, {attack_allowed_minutes}.
Warning/punish/kick placeholders: {player_name}, {team}, {remaining_minutes},
{attack_allowed_minutes}, {received_warnings}, {max_warnings}, {received_punishes},
{max_punishes}.
`;

export default AntiSteamroll;
