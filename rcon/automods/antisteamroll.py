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
from rcon.logs.loop import on_match_end, on_match_start
from rcon.maps import GameMode
from rcon.models import AuditLog, enter_session
from rcon.types import GameEnum
from rcon.user_config.auto_mod_antisteamroll import AutoModAntiSteamrollUserConfig

logger = logging.getLogger(__name__)
AUTHOR = "AutoMod_AntiSteamroll"
STATE_TTL = 120
MAX_SAMPLE_GAP = 15


def state_key(server_info):
    return f"antisteamroll:{server_info.game.value}:{server_info.number}"


def reset_match(rcon, *, active):
    red = get_redis_client()
    key = state_key(server_info_for_automod(rcon))
    # The lifecycle marker also prevents sanctions on transitional post-match data.
    red.set(f"{key}:match", json.dumps({"id": uuid.uuid4().hex, "active": active}))
    red.delete(key)


@on_match_start
def match_started(rcon, _log):
    reset_match(rcon, active=True)


@on_match_end
def match_ended(rcon, _log):
    reset_match(rcon, active=False)


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

    def server_punitions_to_apply(self, team_view, game_state):
        """Evaluate one server snapshot, including empty teams and score transitions."""
        result = PunitionsToApply()
        remaining = game_state["time_remaining"].total_seconds()
        scores = (game_state["allied_score"], game_state["axis_score"])
        match = json.loads(self.red.get(f"{self.key}:match") or "{}")
        dominant = "allies" if scores[0] > scores[1] else "axis"
        if (
            not self.config.enabled
            or game_state["game_mode"] != GameMode.WARFARE
            or scores[0] + scores[1] != 5
            or min(scores) <= 0
            or max(scores) < self.config.caps_threshold
            or remaining <= self.config.allow_final_attack_remaining_minutes * 60
            or sum(
                team_view.get(team, {}).get("count", 0) for team in ("allies", "axis")
            )
            < self.config.min_players
            or match.get("active") is False
        ):
            self.red.delete(self.key)
            return result

        layer_id = game_state["current_map"]["id"]
        sector = final_sector(layer_id, dominant, self.server_info.game)
        if sector is None:
            logger.warning(
                "Anti-Steamroll: no verified final-sector geometry for %s; skipping",
                layer_id,
            )
            self.red.delete(self.key)
            return result

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
            state = {"context": context, "started_remaining": remaining, "players": {}}
        state.update(remaining=remaining, observed_at=now)
        duration = self.config.protection_duration_minutes * 60
        # Retain released state until the score/context changes, rather than restarting protection.
        if duration and state["started_remaining"] - remaining >= duration:
            state["players"] = {}
            self.red.set(self.key, json.dumps(state), ex=STATE_TTL)
            return result

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
            if not inside_protected_sector(
                player.get("world_position"), sector, self.config.boundary_margin_units
            ):
                continue
            present.add(player_id)
            previous = state["players"].get(player_id)
            if (
                previous
                and now - previous["last_inside"] >= self.config.violation_reset_seconds
            ):
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
            if now - history["entered"] < self.config.entry_grace_seconds:
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
                    remaining_minutes=self.config.allow_final_attack_remaining_minutes,
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
            if key in connected
            and now - value["last_inside"] < self.config.violation_reset_seconds
        }
        for player_id, history in state["players"].items():
            if player_id not in present:
                history["entered"] = None
        self.red.set(self.key, json.dumps(state), ex=STATE_TTL)
        return result

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
        if player.details.author not in (AUTHOR, f"{AUTHOR}-DryRun"):
            return
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
                    else "action_scheduled",
                )
            )
