"""Position-based final-sector protection, independent of seeding rules."""

import json
import logging
import math
import time
import uuid

import hllrcon

from rcon.automods import server_info_for_automod
from rcon.automods.models import (
    ActionMethod,
    PunishDetails,
    PunishPlayer,
    PunitionsToApply,
)
from rcon.cache_utils import get_redis_client
from rcon.discord import send_to_discord_audit
from rcon.logs.loop import on_match_end, on_match_start
from rcon.maps import GameMode
from rcon.models import AuditLog, enter_session
from rcon.player_messages import message_players
from rcon.types import GameEnum
from rcon.user_config.auto_mod_antisteamroll import AutoModAntiSteamrollUserConfig

logger = logging.getLogger(__name__)
AUTHOR = "AutoMod_AntiSteamroll"
STATE_TTL = 120
MAX_SAMPLE_GAP = 15
# Inset on every edge of the protected sector to avoid border false positives
# (a standard grid cell is 20000 world units, so 1000 is about 10 metres).
BOUNDARY_MARGIN_UNITS = 1000
# Continuous observed presence required before any action (cycles run every ~5 s).
ENTRY_GRACE_SECONDS = 10
# Warnings/punishments are forgotten after this long outside the protected sector.
VIOLATION_RESET_SECONDS = 120
# Announcement bookkeeping only needs to outlive one match.
ANNOUNCEMENT_TTL = 3 * 60 * 60
# Upper bound for an "ended" marker whose end-of-match snapshot could not be
# taken; the post-match screen lasts a couple of minutes at most.
ENDED_MARKER_TTL = 15 * 60


def state_key(server_info):
    return f"antisteamroll:{server_info.game.value}:{server_info.number}"


def match_key(server_info):
    return f"{state_key(server_info)}:match"


def announcement_key(server_info):
    return f"{state_key(server_info)}:announcement"


def team_player_ids(team_view, team):
    team_data = team_view.get(team) or {}
    players = [team_data["commander"]] if team_data.get("commander") else []
    for squad in (team_data.get("squads") or {}).values():
        players.extend(squad.get("players", []))
    return [player["player_id"] for player in players if player.get("player_id")]


def new_match_marker():
    return {"id": uuid.uuid4().hex, "active": True}


@on_match_start
def match_started(rcon, _log):
    red = get_redis_client()
    server_info = server_info_for_automod(rcon)
    red.set(match_key(server_info), json.dumps(new_match_marker()))
    red.delete(state_key(server_info))


@on_match_end
def match_ended(rcon, _log):
    """Block enforcement on post-match data until the next match is observed.

    The layer and remaining time at the end are recorded so the marker can be
    recognised as stale if the following MATCH START log is missed (log loop
    restart during the map change...), instead of disabling protection for the
    whole next match.
    """
    red = get_redis_client()
    server_info = server_info_for_automod(rcon)
    marker = {"id": uuid.uuid4().hex, "active": False}
    try:
        game_state = rcon.get_gamestate()
        marker["layer"] = game_state["current_map"]["id"]
        marker["remaining"] = game_state["time_remaining"].total_seconds()
        ttl = None
    except Exception:
        logger.warning(
            "Anti-Steamroll: could not snapshot the ended match; the post-match "
            "block will expire after %s seconds",
            ENDED_MARKER_TTL,
            exc_info=True,
        )
        ttl = ENDED_MARKER_TTL
    red.set(match_key(server_info), json.dumps(marker), ex=ttl)
    red.delete(state_key(server_info))


def ended_match_is_current(marker, layer_id, remaining):
    """Whether the server still shows the match the last MATCH ENDED belongs to.

    A different layer, or more remaining time than at the end, means a new match
    is running even though no MATCH START was received.
    """
    if marker.get("active") is not False:
        return False
    ended_layer = marker.get("layer")
    ended_remaining = marker.get("remaining")
    if ended_layer is None or ended_remaining is None:
        return True  # No snapshot: bounded by ENDED_MARKER_TTL.
    return (
        ended_layer.casefold() == layer_id.casefold() and remaining <= ended_remaining
    )


def final_sector(layer_id, dominant_team, game):
    """Resolve catalog geometry and mirrored faction starts; never guess unknown maps."""
    catalog = hllrcon.HLLVLayer if game == GameEnum.HLL_VIETNAM else hllrcon.HLLLayer
    layer = next(
        (item for item in catalog.all() if item.id.casefold() == layer_id.casefold()),
        None,
    )
    if (
        layer is None
        or len(layer.sectors) != 5
        or layer.game_mode.id.lower() != GameMode.WARFARE
    ):
        return None
    allies_home_index = 4 if layer.map.is_mirrored else 0
    index = allies_home_index if dominant_team == "axis" else 4 - allies_home_index
    return layer.sectors[index]


def inside_protected_sector(position, sector, margin):
    """Use strict inset boundaries; missing, dead and non-finite positions are excluded."""
    if not isinstance(position, dict):
        return False
    coordinates = [position.get(axis) for axis in ("x", "y", "z")]
    if any(
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        for value in coordinates
    ):
        return False
    if not any(coordinates):
        return False
    x, y, _ = coordinates
    (x1, y1), (x2, y2) = sector.area
    return x1 + margin < x < x2 - margin and y1 + margin < y < y2 - margin


class AntiSteamrollAutomod:
    def __init__(self, config: AutoModAntiSteamrollUserConfig, red, server_info):
        self.config = config
        self.red = red
        self.server_info = server_info
        self.key = state_key(server_info)

    def enabled(self):
        if not self.config.enabled:
            self.red.delete(self.key)
        return self.config.enabled

    def server_punitions_to_apply(self, team_view, game_state, rcon=None):
        """Evaluate one server snapshot, including empty teams and score transitions.

        Protection applies in Warfare while the score is 4-1, the remaining time is
        above ``allow_final_attack_remaining_minutes`` and the population reaches
        ``min_players``. ``rcon`` is used to send the protection announcements; without
        it (or in dry-run) nothing is sent to players.
        """
        result = PunitionsToApply()
        remaining = game_state["time_remaining"].total_seconds()
        scores = (game_state["allied_score"], game_state["axis_score"])
        layer_id = game_state["current_map"]["id"]
        match = self.current_match(layer_id, remaining)
        dominant = "allies" if scores[0] > scores[1] else "axis"
        if (
            not self.config.enabled
            or game_state["game_mode"] != GameMode.WARFARE
            or sorted(scores) != [1, 4]
            or sum(
                team_view.get(team, {}).get("count", 0) for team in ("allies", "axis")
            )
            < self.config.min_players
            or match.get("active") is False
        ):
            # Protection is not running: the next 4-1 (even with the same score
            # as a previous one) is a new protection that must be announced.
            self.red.delete(self.key, announcement_key(self.server_info))
            return result

        sector = final_sector(layer_id, dominant, self.server_info.game)
        if sector is None:
            logger.warning(
                "Anti-Steamroll: no verified final-sector geometry for %s; skipping",
                layer_id,
            )
            self.red.delete(self.key)
            return result

        announcement_context = [match.get("id"), layer_id, list(scores)]
        if remaining <= self.config.allow_final_attack_remaining_minutes * 60:
            self.announce_lifted(rcon, announcement_context, layer_id, remaining)
            self.red.delete(self.key)
            return result
        self.announce_started(
            rcon, announcement_context, team_view, layer_id, dominant, remaining
        )

        now = time.time()
        context = [
            layer_id,
            list(scores),
            match.get("id"),
            self.config.model_dump(mode="json"),
        ]
        state = json.loads(self.red.get(self.key) or "{}")
        if (
            state.get("context") != context
            or remaining > state.get("remaining", remaining)
            or now - state.get("observed_at", now) > MAX_SAMPLE_GAP
        ):
            state = {"context": context, "players": {}}
        state.update(remaining=remaining, observed_at=now)

        immune_roles = set(self.config.immune_roles)
        candidates = []
        team_data = team_view.get(dominant, {})
        if team_data.get("commander"):
            candidates.append(("Commander", team_data["commander"]))
        for squad_name, squad in team_data.get("squads", {}).items():
            candidates.extend(
                (squad_name, player) for player in squad.get("players", [])
            )

        present = set()
        connected = set()
        for squad_name, player in candidates:
            player_id = player["player_id"]
            if player.get("team") != dominant:
                continue
            connected.add(player_id)
            if player.get("role") in immune_roles:
                continue
            if not inside_protected_sector(
                player.get("world_position"), sector, BOUNDARY_MARGIN_UNITS
            ):
                continue
            present.add(player_id)
            previous = state["players"].get(player_id)
            if previous and now - previous["last_inside"] >= VIOLATION_RESET_SECONDS:
                del state["players"][player_id]
            history = state["players"].setdefault(
                player_id,
                {
                    "entered": now,
                    "last_inside": now,
                    "warnings": [],
                    "punishes": [],
                    "kicks": [],
                },
            )
            if history["entered"] is None:
                history["entered"] = now
            history["last_inside"] = now
            if now - history["entered"] < ENTRY_GRACE_SECONDS:
                continue
            action = self.next_action(history, now)
            if action is None:
                continue
            method, message, actions = action
            actions.append(now)
            details = PunishDetails(
                author=AUTHOR + ("-DryRun" if self.config.dry_run else ""),
                dry_run=self.config.dry_run,
                discord_audit_url=self.config.discord_webhook_url,
                message=message.format(
                    player_name=player["name"],
                    team=dominant,
                    remaining_minutes=int(remaining // 60),
                    attack_allowed_minutes=self.config.allow_final_attack_remaining_minutes,
                    received_warnings=len(history["warnings"]),
                    max_warnings=self.config.number_of_warnings,
                    received_punishes=len(history["punishes"]),
                    max_punishes=self.config.number_of_punishments,
                ),
            )
            aplayer = PunishPlayer(
                player_id=player_id,
                name=player["name"],
                squad=squad_name,
                team=dominant,
                details=details,
            )
            getattr(result, method).append(aplayer)
            logger.info(
                "Anti-Steamroll %s: %s in protected final sector on %s (dry_run=%s)",
                method,
                player_id,
                layer_id,
                self.config.dry_run,
            )

        # A punish kills the player: retain escalation for a subsequent incursion,
        # but require fresh confirmation and never sanction someone outside the zone.
        state["players"] = {
            key: value
            for key, value in state["players"].items()
            if key in connected and now - value["last_inside"] < VIOLATION_RESET_SECONDS
        }
        for player_id, history in state["players"].items():
            if player_id not in present:
                history["entered"] = None
        self.red.set(self.key, json.dumps(state), ex=STATE_TTL)
        return result

    def announce_started(
        self, rcon, context, team_view, layer_id, dominant, remaining
    ) -> None:
        """Tell each team, once per 4-1 situation, that the last point is protected.

        The context (match, layer, score) is tracked separately from the
        enforcement state so a reset of the latter never repeats the announcement;
        a recapture followed by a new 4-1 changes the score and is announced again.
        """
        announced = self._announcement_state()
        if announced.get("context") == context and announced.get("activated"):
            return
        losing = "axis" if dominant == "allies" else "allies"
        deliveries = [
            (team_player_ids(team_view, dominant), self.config.winning_team_message),
            (team_player_ids(team_view, losing), self.config.losing_team_message),
        ]
        summary = (
            f"Protection active on {layer_id} ({dominant} 4-1), lifts at "
            f"{self.config.allow_final_attack_remaining_minutes} minutes remaining"
        )
        if self._announce(rcon, "protection_started", deliveries, remaining, summary):
            self._save_announcement_state(
                {"context": context, "activated": True, "lifted": False}
            )

    def announce_lifted(self, rcon, context, layer_id, remaining) -> None:
        """Tell all players, once, that a running protection has been lifted."""
        announced = self._announcement_state()
        if (
            announced.get("context") != context
            or not announced.get("activated")
            or announced.get("lifted")
        ):
            return
        summary = f"Protection lifted on {layer_id}: the last point can be attacked"
        deliveries = [(None, self.config.protection_lifted_message)]
        if self._announce(rcon, "protection_lifted", deliveries, remaining, summary):
            self._save_announcement_state(dict(announced, lifted=True))

    def _announcement_state(self):
        return json.loads(self.red.get(announcement_key(self.server_info)) or "{}")

    def _save_announcement_state(self, state):
        self.red.set(
            announcement_key(self.server_info),
            json.dumps(state),
            ex=ANNOUNCEMENT_TTL,
        )

    def _announce(self, rcon, kind, deliveries, remaining, summary) -> bool:
        """Send ``(player_ids, message)`` deliveries; ``None`` ids means all players.

        Returns whether the announcement can be considered done. Empty messages
        are skipped. Without an RCON connection, or when no player could be
        messaged, nothing is recorded and the announcement is retried on a later
        cycle instead of stopping the Automod loop.
        """
        values = {
            "remaining_minutes": int(remaining // 60),
            "attack_allowed_minutes": self.config.allow_final_attack_remaining_minutes,
        }
        deliveries = [
            (player_ids, template.format(**values))
            for player_ids, template in deliveries
            if template.strip() and player_ids != []
        ]
        if not self.config.dry_run and (
            rcon is None or not self._send(rcon, kind, deliveries)
        ):
            return False

        logger.info(
            "Anti-Steamroll %s (dry_run=%s): %s", kind, self.config.dry_run, summary
        )
        author = AUTHOR + ("-DryRun" if self.config.dry_run else "")
        self._record_announcement(kind, author, deliveries, summary)
        if self.config.discord_webhook_url is not None:
            try:
                send_to_discord_audit(
                    message=summary,
                    command_name=f"antisteamroll_{kind}",
                    by=author,
                    webhookurls=[self.config.discord_webhook_url],
                    silent=False,
                )
            except Exception:
                logger.exception(
                    "Anti-Steamroll: could not post the %s announcement to Discord",
                    kind,
                )
        return True

    @staticmethod
    def _send(rcon, kind, deliveries) -> bool:
        """Send the deliveries; return False only when no one received anything.

        Once at least one player got the announcement it is considered sent:
        retrying it would repeat it to everyone who already received it.
        """
        attempted = sent = 0
        for player_ids, message in deliveries:
            if player_ids is None:
                attempted += 1
                try:
                    rcon.message_all_players(message)
                    sent += 1
                except Exception:
                    logger.exception("Anti-Steamroll: could not message all players")
            else:
                attempted += len(player_ids)
                sent += message_players(
                    rcon, ((player_id, message) for player_id in player_ids), by=AUTHOR
                )
        if attempted and not sent:
            logger.error("Anti-Steamroll: could not send %s announcement", kind)
            return False
        if sent < attempted:
            logger.warning(
                "Anti-Steamroll: %s announcement reached %d of %d recipients",
                kind,
                sent,
                attempted,
            )
        return True

    def _record_announcement(self, kind, author, deliveries, summary) -> None:
        try:
            with enter_session() as session:
                session.add(
                    AuditLog(
                        username=author,
                        command="antisteamroll_announcement",
                        command_arguments=json.dumps(
                            {
                                "kind": kind,
                                "summary": summary,
                                "messages": [message for _, message in deliveries],
                                "game": self.server_info.game.value,
                                "server_number": self.server_info.number,
                            }
                        ),
                        command_result="dry_run"
                        if self.config.dry_run
                        else "action_applied",
                    )
                )
        except Exception:
            logger.exception(
                "Anti-Steamroll: could not write the audit log for %s", kind
            )

    def current_match(self, layer_id, remaining):
        """Read the match marker, replacing an ended marker that is no longer current."""
        marker = json.loads(self.red.get(match_key(self.server_info)) or "{}")
        if marker.get("active") is False and not ended_match_is_current(
            marker, layer_id, remaining
        ):
            logger.warning(
                "Anti-Steamroll: a new match is running on %s without a MATCH START "
                "log; resuming protection",
                layer_id,
            )
            marker = new_match_marker()
            self.red.set(match_key(self.server_info), json.dumps(marker))
        return marker

    def next_action(self, history, now):
        for method, key, limit, interval, message in (
            (
                "warning",
                "warnings",
                self.config.number_of_warnings,
                self.config.warning_interval_seconds,
                self.config.warning_message,
            ),
            (
                "punish",
                "punishes",
                self.config.number_of_punishments,
                self.config.punish_interval_seconds,
                self.config.punish_message,
            ),
        ):
            actions = history[key]
            if limit == 0:
                continue
            if actions and now - actions[-1] < interval:
                return None
            if limit == -1 or len(actions) < limit:
                return method, message, actions
        last_action = (
            history["punishes"] or history["warnings"] or [history["entered"]]
        )[-1]
        if (
            self.config.kick_after_max_punish
            and now - last_action >= self.config.kick_grace_period_seconds
        ):
            kicks = history["kicks"]
            if not kicks or now - kicks[-1] >= self.config.kick_grace_period_seconds:
                return "kick", self.config.kick_message, kicks
        return None

    def player_punish_failed(self, player):
        if player.details.author not in (AUTHOR, f"{AUTHOR}-DryRun"):
            return
        state = json.loads(self.red.get(self.key) or "{}")
        history = state.get("players", {}).get(player.player_id)
        if history and history["punishes"]:
            history["punishes"].pop()
            self.red.set(self.key, json.dumps(state), ex=STATE_TTL)

    def audit_action(self, method: ActionMethod, player: PunishPlayer):
        """Record an Anti-Steamroll action once it has been applied (or simulated).

        Called by the Automod executor after the game command succeeded, so retried
        failures are not recorded twice. An audit failure is logged but must not
        stop the executor, which also runs the other automods.
        """
        if player.details.author not in (AUTHOR, f"{AUTHOR}-DryRun"):
            return
        try:
            with enter_session() as session:
                session.add(
                    AuditLog(
                        username=player.details.author,
                        command="antisteamroll_detection",
                        command_arguments=json.dumps(
                            {
                                "player_id": player.player_id,
                                "player_name": player.name,
                                "team": player.team,
                                "action": method.name,
                                "message": player.details.message,
                                "game": self.server_info.game.value,
                                "server_number": self.server_info.number,
                            }
                        ),
                        command_result="dry_run"
                        if player.details.dry_run
                        else "action_applied",
                    )
                )
        except Exception:
            logger.exception(
                "Anti-Steamroll: could not write the audit log for %s %s",
                method.name,
                player.player_id,
            )
