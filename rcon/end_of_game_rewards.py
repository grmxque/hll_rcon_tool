"""End-of-game stats messages and VIP rewards for the best players.

Port of the HLL_CRCON_Top_stats plugin, configured from the settings UI
(``EndOfGameRewardsUserConfig``) instead of an edited copy of ``rcon/hooks.py``, plus two
construction categories (TOP 3 garrison builders, node builders) detected by
``rcon.actions_tracking``.
"""

import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from rcon.actions_tracking.banner import short_name
from rcon.actions_tracking.model import PlayerActions, rank_players
from rcon.actions_tracking.tracker import final_actions
from rcon.cache_utils import get_redis_client
from rcon.discord import send_to_discord_audit
from rcon.logs.loop import on_match_end
from rcon.models import AuditLog, enter_session
from rcon.player_messages import message_players
from rcon.player_stats import get_cached_live_game_stats
from rcon.rcon import Rcon
from rcon.types import StructuredLogLineWithMetaData
from rcon.user_config.end_of_game_rewards import EndOfGameRewardsUserConfig
from rcon.utils import INDEFINITE_VIP_DATE, MapsHistory, get_server_number

logger = logging.getLogger(__name__)
AUTHOR = "end_of_game_rewards"
# Long enough to cover a log loop restart replaying the MATCH ENDED line.
PROCESSED_TTL = 6 * 60 * 60
TOP_GARRISON_BUILDERS = 3
# Console players cannot display more lines in a private message.
MAX_MESSAGE_LINES = 16

LABELS = {
    "en": {
        "end_of_game": "END OF GAME",
        "offense": "Offense",
        "defense": "Defense",
        "support": "Support",
        "combat": "Combat",
        "kills": "KILLS",
        "kills_streak": "STREAK",
        "kills_per_minute": "K/MIN",
        "kill_death_ratio": "K/D",
        "garrisons": "GARRISONS",
        "nodes": "NODES",
        "node_builders": "Nodes{bonus} : {count} builders",
        "day": "d",
        "days_of_vip": "DAYS OF VIP",
        "you_won": "YOU WON",
    },
    "fr": {
        "end_of_game": "FIN DE PARTIE",
        "offense": "Attaque",
        "defense": "Défense",
        "support": "Soutien",
        "combat": "Combat",
        "kills": "KILLS",
        "kills_streak": "SÉRIE",
        "kills_per_minute": "K/MIN",
        "kill_death_ratio": "K/D",
        "garrisons": "GARNISONS",
        "nodes": "NODES",
        "node_builders": "Nodes{bonus} : {count} constructeurs",
        "day": "j",
        "days_of_vip": "JOURS DE VIP",
        "you_won": "TU AS GAGNÉ",
    },
}
INDIVIDUAL_STATS = ("kills", "kills_streak", "kills_per_minute", "kill_death_ratio")


@dataclass
class GameRewards:
    """Outcome of one game: best players, threshold hits and VIP days per player."""

    rewards_allowed: bool
    top_players: list[tuple[str, dict]] = field(default_factory=list)
    hits: dict[str, set[str]] = field(default_factory=dict)
    days: dict[str, int] = field(default_factory=dict)
    names: dict[str, str] = field(default_factory=dict)
    # Why each player gets VIP days, e.g. ["top_offense", "threshold_kills", "top_garrison"].
    reasons: dict[str, list[str]] = field(default_factory=dict)
    actions: dict[str, PlayerActions] = field(default_factory=dict)
    top_garrison_builders: list[PlayerActions] = field(default_factory=list)
    node_builders: list[PlayerActions] = field(default_factory=list)


def _stat(player: dict, key: str) -> float:
    return player.get(key) or 0


def is_unlimited_vip(vip_entry: dict) -> bool:
    """A VIP without expiration record is unlimited, like in the original plugin."""
    expiration = vip_entry.get("vip_expiration")
    return expiration is None or expiration >= INDEFINITE_VIP_DATE


def individual_hits(player: dict, config: EndOfGameRewardsUserConfig) -> set[str]:
    """Individual thresholds reached; a threshold of 0 disables its rule."""
    thresholds = {
        "kills": config.kills_threshold,
        "kills_streak": config.kills_streak_threshold,
        "kills_per_minute": config.kills_per_minute_threshold,
        "kill_death_ratio": config.kill_death_ratio_threshold,
    }
    hits = {
        key
        for key, value in thresholds.items()
        if value and _stat(player, key) >= value
    }
    if _stat(player, "kills") < config.kill_death_ratio_min_kills:
        hits.discard("kill_death_ratio")
    return hits


def compute_rewards(
    stats: list[dict],
    config: EndOfGameRewardsUserConfig,
    vips: dict[str, dict],
    online_count: int,
    actions: dict[str, PlayerActions] | None = None,
) -> GameRewards:
    """Apply the reward rules to the final stats of a game. No side effect.

    Rewards require ``vip_rewards_enabled`` and ``min_players_for_rewards``
    connected players. Every player in the final stats or in ``actions`` can be
    rewarded, connected or not; unlimited VIPs are never rewarded. Each category gives its
    days at most once per player and the categories add up.
    """
    players = [p for p in stats if p.get("player_id")]
    result = GameRewards(
        rewards_allowed=config.vip_rewards_enabled
        and online_count >= config.min_players_for_rewards
    )
    result.names = {p["player_id"]: p.get("player") or p["player_id"] for p in players}
    result.actions = actions or {}

    def add_days(player_id: str, days: int, reason: str) -> None:
        if result.rewards_allowed and days > 0:
            result.days[player_id] = result.days.get(player_id, 0) + days
            result.reasons.setdefault(player_id, []).append(reason)

    if players:
        for category in config.top_categories:
            best = max(players, key=lambda p: _stat(p, category))
            result.top_players.append((category, best))
            add_days(
                best["player_id"], config.top_category_reward_days, f"top_{category}"
            )

    for player in players:
        player_id = player["player_id"]
        hits = individual_hits(player, config)
        result.hits[player_id] = hits
        for key in sorted(hits):
            add_days(player_id, config.individual_reward_days, f"threshold_{key}")
        if player_id in vips:
            add_days(player_id, config.fidelity_reward_days, "fidelity")

    if config.garrison_rewards_enabled:
        top = rank_players(result.actions, "garrison")[:TOP_GARRISON_BUILDERS]
        result.top_garrison_builders = top
        for builder in top:
            result.names.setdefault(builder.player_id, builder.name)
            add_days(builder.player_id, config.garrison_reward_days, "top_garrison")
    if config.node_rewards_enabled:
        result.node_builders = rank_players(result.actions, "node")
        for builder in result.node_builders:
            result.names.setdefault(builder.player_id, builder.name)
            add_days(builder.player_id, config.node_reward_days, "node_builder")

    for player_id in list(result.days):
        if player_id in vips and is_unlimited_vip(vips[player_id]):
            del result.days[player_id]
            del result.reasons[player_id]
    return result


def new_expiration(vip_entry: dict | None, days: int, now: datetime) -> datetime | None:
    """Expiration after adding ``days``; None for an unlimited VIP (left untouched).

    An existing VIP is extended from its current expiration, or from now when it
    has already expired.
    """
    if vip_entry is None:
        return now + timedelta(days=days)
    if is_unlimited_vip(vip_entry):
        return None
    return max(vip_entry["vip_expiration"], now) + timedelta(days=days)


def _format_value(value: float) -> str:
    return str(round(value, 2)) if isinstance(value, float) else str(value)


def build_player_message(
    player: dict,
    rewards: GameRewards,
    config: EndOfGameRewardsUserConfig,
) -> str:
    """Compact layout, without blank lines, that fits the 16 lines a console can display.

    Disabled or empty sections are left out, and VIP days only show when they are granted.
    """
    labels = LABELS[config.language]

    def granted(days: int) -> bool:
        return rewards.rewards_allowed and days > 0

    def inline(days: int) -> str:
        return f" +{days} {labels['day']}" if granted(days) else ""

    def bracketed(days: int) -> str:
        return f" (+{days} {labels['day']})" if granted(days) else ""

    header = f"█ {labels['end_of_game']} █"
    if rewards.top_players and granted(config.top_category_reward_days):
        header += f"{inline(config.top_category_reward_days)}/TOP"
    game_lines = [header]
    for category, best in rewards.top_players:
        game_lines.append(
            f"{labels[category]} : {short_name(str(best.get('player')))} "
            f"({_format_value(_stat(best, category))})"
        )
    if rewards.top_garrison_builders:
        game_lines.append(
            f"▒ {labels['garrisons']}{inline(config.garrison_reward_days)} ▒"
        )
        for rank, builder in enumerate(rewards.top_garrison_builders, start=1):
            game_lines.append(
                f"{rank}. {short_name(builder.name)} ({builder.count('garrison')})"
            )
    if rewards.node_builders:
        game_lines.append(
            labels["node_builders"].format(
                bonus=inline(config.node_reward_days), count=len(rewards.node_builders)
            )
        )

    player_id = player["player_id"]
    hits = rewards.hits.get(player_id, set())

    def stat(key: str) -> str:
        text = f"{labels[key]} {_format_value(_stat(player, key))}"
        return text + bracketed(config.individual_reward_days) if key in hits else text

    player_lines = [
        f"▒ {short_name(str(player.get('player')), 24)} ▒",
        f"{stat('kills')} · {stat('kills_streak')}",
        f"{stat('kills_per_minute')} · {stat('kill_death_ratio')}",
    ]
    mine = rewards.actions.get(player_id)
    built = []
    if mine and config.garrison_rewards_enabled and mine.count("garrison"):
        rewarded = any(b.player_id == player_id for b in rewards.top_garrison_builders)
        built.append(
            f"{labels['garrisons']} {mine.count('garrison')}"
            + (bracketed(config.garrison_reward_days) if rewarded else "")
        )
    if mine and config.node_rewards_enabled and mine.count("node"):
        built.append(
            f"{labels['nodes']} {mine.count('node')}"
            + bracketed(config.node_reward_days)
        )
    if built:
        player_lines.append(" · ".join(built))
    if days := rewards.days.get(player_id):
        player_lines.append(f"{labels['you_won']} {days} {labels['days_of_vip']}!")
    # The layout fits by construction; a duplicated top category must not hide the player's part.
    game_lines = game_lines[: MAX_MESSAGE_LINES - len(player_lines)]
    return "\n".join(game_lines + player_lines)


def processed_key(struct_log: StructuredLogLineWithMetaData) -> str:
    return f"end_of_game_rewards:{get_server_number()}:{struct_log.get('timestamp_ms')}"


def process_end_of_game(
    rcon: Rcon,
    config: EndOfGameRewardsUserConfig,
    stats: list[dict],
    now: datetime | None = None,
    actions: dict[str, PlayerActions] | None = None,
) -> GameRewards:
    """Message connected players and grant VIP days (nothing is sent in dry-run)."""
    now = now or datetime.now(UTC)
    online = set(rcon.get_player_ids(as_dict=True).values())
    vips = {vip["player_id"]: vip for vip in rcon.get_vip_ids()}
    rewards = compute_rewards(stats, config, vips, len(online), actions)

    if config.send_stats_message and not config.dry_run:
        recipients = [p for p in stats if p.get("player_id") in online]
        message_players(
            rcon,
            (
                (p["player_id"], build_player_message(p, rewards, config))
                for p in recipients
            ),
            by=AUTHOR,
        )

    granted = []
    for player_id, days in rewards.days.items():
        expiration = new_expiration(vips.get(player_id), days, now)
        if expiration is None:
            continue
        name = rewards.names[player_id]
        if not config.dry_run:
            try:
                rcon.add_vip(
                    player_id,
                    f"{name} - {config.vip_description}",
                    expiration.isoformat(),
                )
            except Exception:
                logger.exception(
                    "End of game rewards: could not grant VIP to %s", player_id
                )
                continue
        granted.append((player_id, name, days, expiration, rewards.reasons[player_id]))
    _report(config, granted)
    return rewards


def _report(config: EndOfGameRewardsUserConfig, granted) -> None:
    """Audit log row per reward and one Discord summary; failures are only logged."""
    if not granted:
        return
    author = AUTHOR + ("-DryRun" if config.dry_run else "")
    result = "dry_run" if config.dry_run else "vip_granted"
    try:
        with enter_session() as session:
            for player_id, name, days, expiration, reasons in granted:
                session.add(
                    AuditLog(
                        username=author,
                        command="end_of_game_vip_reward",
                        command_arguments=json.dumps(
                            {
                                "player_id": player_id,
                                "player_name": name,
                                "days": days,
                                "expiration": expiration.isoformat(),
                                "reasons": reasons,
                            }
                        ),
                        command_result=result,
                    )
                )
    except Exception:
        logger.exception("End of game rewards: could not write the audit log")
    if config.discord_webhook_url is not None:
        summary = ", ".join(
            f"{name} +{days}d [{', '.join(reasons)}] (until {expiration:%d/%m/%Y %H:%M} UTC)"
            for _, name, days, expiration, reasons in granted
        )
        try:
            send_to_discord_audit(
                message=f"VIP rewards: {summary}",
                command_name="end_of_game_vip_reward",
                by=author,
                webhookurls=[config.discord_webhook_url],
                silent=False,
            )
        except Exception:
            logger.exception("End of game rewards: could not post to Discord")


def _match_start(end_ts: float) -> int | None:
    """Start of the match that ended at ``end_ts`` (the MATCH ENDED line can be an old one)."""
    for match in MapsHistory()[:3]:
        start, end = match.get("start"), match.get("end")
        if start and start <= end_ts and (end is None or end >= end_ts - 1):
            return start
    return None


def _actions(
    config: EndOfGameRewardsUserConfig, end_ts: float
) -> dict[str, PlayerActions]:
    """The construction categories must never prevent the other rewards."""
    try:
        return final_actions(config, _match_start(end_ts), end_ts)
    except Exception:
        logger.exception("End of game rewards: actions tracking unavailable")
        return {}


@on_match_end
def end_of_game_rewards_on_match_end(
    rcon: Rcon, struct_log: StructuredLogLineWithMetaData
):
    try:
        config = EndOfGameRewardsUserConfig.load_from_db()
        if not config.enabled:
            return
        # At most once per MATCH ENDED line: VIP days must never be granted twice.
        if not get_redis_client().set(
            processed_key(struct_log), 1, nx=True, ex=PROCESSED_TTL
        ):
            logger.info("End of game rewards: match end already processed")
            return
        live_stats = get_cached_live_game_stats() or {}
        stats = live_stats.get("stats") or []
        if not stats:
            logger.warning("End of game rewards: no live game stats available")
            return
        end_ts = struct_log["timestamp_ms"] / 1000
        process_end_of_game(rcon, config, stats, actions=_actions(config, end_ts))
    except Exception:
        logger.exception("End of game rewards: processing failed")
