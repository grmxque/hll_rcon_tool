const AntiSteamroll = `
Anti-Steamroll protects the losing team's final Warfare capture sector.

The entire final sector is protected, including all three possible capture zones.
Entering it for reconnaissance also violates the rule. The active randomly selected
strongpoint is not exposed reliably by the server. Unknown layers are skipped.

Settings are reloaded every Automod cycle (approximately five seconds).
Protection starts at caps_threshold (default 4), above min_players, and ends when
remaining match time reaches allow_final_attack_remaining_minutes (default 30).
protection_duration_minutes optionally limits continuous protection; 0 disables this limit.

boundary_margin_units insets the sector edges (default 1000 world units).
entry_grace_seconds requires confirmed presence before enforcement (default 10 seconds).
Missing, non-finite and all-zero positions are ignored. Leaving the protected
interior or dying ends presence confirmation and stops actions immediately.
Warnings and punishments are retained for violation_reset_seconds (default 120)
outside the protected interior, so repeated incursions can lead to a kick.
Changing teams or disconnecting clears that player's escalation.
Score, map, match and configuration changes clear the enforcement state.

Warnings and punishments: 0 skips the stage, -1 repeats indefinitely.
kick_after_max_punish enables the final kick stage after kick_grace_period_seconds.
dry_run records proposed actions in CRCON audit logs without sending any command.
The Discord webhook optionally receives the existing Automod action audit.

Message placeholders: {player_name}, {team}, {remaining_minutes} (current remaining
match time), {attack_allowed_minutes} (allow_final_attack_remaining_minutes),
{received_warnings}, {max_warnings}, {received_punishes}, {max_punishes}.
`;

export default AntiSteamroll;
