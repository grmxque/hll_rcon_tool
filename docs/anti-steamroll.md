# Anti-Steamroll

Anti-Steamroll is a separate Automod for Warfare. Enable and configure it from
**Automods → Anti-Steamroll** (`/settings/automods/anti-steamroll`). It also appears
on the administration dashboard. Configuration is scoped to the selected game
and server, stored with the existing user settings, and reloaded every Automod
cycle. The Automod service must be running. Defaults leave the feature disabled.

## Protected area

When one team owns at least the configured cap threshold (default: four of five),
its players must stay out of the losing team's final capture sector. This is the
**whole final sector containing the three possible objective capture zones**,
not just the active strongpoint circle. Reconnaissance inside that sector also
violates the rule. No server setting locks captures: enforcement uses the existing
warning, punish and kick commands.

The geometry comes from the project's locked `hllrcon` catalog, including each
layer's grid scale, offset, orientation and mirrored faction starts. Unknown
layers, non-Warfare catalog layers and layouts without five sectors are skipped.
The detector does not guess geometry from map images or use offensive-score deltas.
CRCON's recorded administrative layout does not reliably identify the objectives
randomly selected by the game, so it is not used as proof of the active strongpoint.

Positions are taken from the existing `GetServerInformation` players response,
via `worldPosition` → `world_position` → `get_team_view`. The team view and game
state have two-second caches and the Automod loop sleeps five seconds between
cycles. Actual observation intervals also include command processing time.
The RCON snapshot has no position age or accuracy guarantee. Missing, non-finite
and all-zero coordinates are ignored; bleeding-out players may retain coordinates.
Neither altitude nor movement direction is used to determine sector membership.

The default inset of 1,000 world units excludes the perimeter on every edge.
The standard grid uses roughly 20,000 world units per cell, with map-specific
scales supplied by the catalog. Boundary equality is excluded. Ten seconds of
observed presence are required by default. A gap exceeding 15 seconds restarts
confirmation and enforcement state. An incursion between observations may be missed.
This feature therefore reduces steamrolls, rather than guaranteeing no final capture.

## Configuration

| Setting | Default | Meaning |
| --- | --- | --- |
| `enabled` | false | Enable the independent Automod |
| `dry_run` | false | Audit proposed actions without messaging or sanctioning |
| `caps_threshold` | 4 | Leading team's cap threshold, from 3 to 4; still protects only the final sector |
| `allow_final_attack_remaining_minutes` | 30 | Release protection at or below this remaining match time |
| `protection_duration_minutes` | 0 | Maximum continuous protection from the first eligible observation; zero disables this limit |
| `min_players` | 0 | Minimum total server population |
| `boundary_margin_units` | 1000 | Inset on the final sector's edges |
| `entry_grace_seconds` | 10 | Required observed presence before an action |
| `violation_reset_seconds` | 120 | Forget escalation after this long outside the protected interior |
| `number_of_warnings` | 2 | Warning count; zero skips, -1 repeats indefinitely |
| `warning_interval_seconds` | 15 | Warning interval and delay before moving beyond warnings |
| `number_of_punishments` | 2 | Punishment count; zero skips, -1 repeats indefinitely |
| `punish_interval_seconds` | 15 | Punishment interval and delay before moving beyond punishments |
| `kick_after_max_punish` | true | Enable the final kick stage |
| `kick_grace_period_seconds` | 30 | Minimum delay after the last warning/punish before kicking, also the kick retry interval |
| `warning_message`, `punish_message`, `kick_message` | supplied templates | Player messages |
| `discord_webhook_url` | null | Optional existing Automod Discord action audit |

Message variables: `{player_name}`, `{team}`, `{remaining_minutes}`,
`{attack_allowed_minutes}`, `{received_warnings}`, `{max_warnings}`,
`{received_punishes}`, `{max_punishes}`. `remaining_minutes` is the current
remaining match time (whole minutes); `attack_allowed_minutes` is the configured
release threshold. Unsupported placeholders and format specifications are
rejected server-side.

Unknown keys are rejected when saving through the API but ignored when loading,
so a stored configuration that still contains a removed or renamed field keeps
its other values. A stored value that no longer validates (for instance a message
using a removed placeholder) still makes the whole configuration fall back to the
defaults, which disables the Automod: migrate stored values when changing them.

Leaving or dying stops actions immediately and resets presence confirmation.
Recent escalation is retained so a player killed by a punish can progress to the
next stage if they return. Team switches and disconnects clear player history.
Score, layer, match, timer increase, configuration changes and interrupted sampling
reset enforcement state. Population falling below the threshold also clears state.
The maximum protection duration follows the match timer and stays released until
the context changes. After a service interruption it starts again at the next
eligible observation; it does not reconstruct historical dominance.

## Integration and deployment

`rcon/automods/antisteamroll.py` evaluates a whole server snapshot once per cycle,
including commanders and unassigned squads. It returns the existing
`PunitionsToApply` model. The common executor handles commands, Discord audit and
failed-punish callbacks. State is JSON in Redis, keyed by game/server and stable
player ID; match-start/end hooks update a lifecycle marker and clear state.
The end marker blocks enforcement on post-match data. It records the ended layer
and remaining time, and is discarded as soon as another layer or a higher remaining
time is observed, so a missed MATCH START log does not disable the next match.
If that snapshot cannot be taken, the end marker expires after 15 minutes.
Server-wide rules are merged after the squad Automods; when another Automod
already punishes the same player in that cycle, the Anti-Steamroll punish is
dropped and rolled back from its escalation.

The UI uses the existing schema-based configuration form and its
get/describe/validate/set endpoints. Backend validation uses Pydantic and the
standard scoped persistence path. No seeding configuration is required.

The two permissions are fork permissions (`rconweb/api/fork_permissions.py`):
they are created by the normal Django `migrate` run in the maintenance
container, without a dedicated migration. Grant `api.can_view_auto_mod_antisteamroll_config` and
`api.can_change_auto_mod_antisteamroll_config` to the appropriate administration
groups; existing restricted accounts do not automatically receive these rights.
The normal user-config initialization discovers the new model automatically.

Proposed actions are recorded in CRCON audit logs with command
`antisteamroll_detection`. Results are `dry_run` or `action_applied`, written
after the RCON command succeeded; failed commands are retried by the executor and
recorded once they succeed. An audit-log write failure is logged and does not stop
the Automod loop.
Successful live player actions also use the existing player action history.

Tests use `FakeRedis`, simulated clocks and mocked command/database boundaries.
No live game server is required. With the development dependencies installed,
the focused suite can run without a Redis service using the project's memory-cache
mode:

```sh
DEBUG=1 SERVER_NUMBER=1 HLL_GAME=hll uv run pytest tests/test_antisteamroll_automod.py tests/test_automod_config_scope.py
```

The UI tests exercise the shared page's real loader and save action with simulated
HTTP responses. From `rcongui`, after installing its locked dependencies:

```sh
node --experimental-vm-modules 'src/pages/settings/[configs]/anti-steamroll.test.mjs'
```
