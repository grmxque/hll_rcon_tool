"""End-of-game stats messages and VIP rewards for the best players.

Port of the HLL_CRCON_Top_stats plugin, configured from the settings UI
(``EndOfGameRewardsUserConfig``) instead of an edited copy of ``rcon/hooks.py``.
"""

import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from rcon.cache_utils import get_redis_client
from rcon.discord import send_to_discord_audit
from rcon.logs.loop import on_match_end
from rcon.models import AuditLog, enter_session
from rcon.player_stats import get_cached_live_game_stats
from rcon.rcon import Rcon
from rcon.types import StructuredLogLineWithMetaData
from rcon.user_config.end_of_game_rewards import EndOfGameRewardsUserConfig
from rcon.utils import INDEFINITE_VIP_DATE, get_server_number

logger = logging.getLogger(__name__)
AUTHOR = "end_of_game_rewards"
# Long enough to cover a log loop restart replaying the MATCH ENDED line.
PROCESSED_TTL = 6 * 60 * 60

LABELS = {
    "en": {
        "end_of_game": "END OF GAME",
        "best_scores": "BEST SCORES",
        "offense": "OFFENSE",
        "defense": "DEFENSE",
        "support": "SUPPORT",
        "combat": "COMBAT",
        "kills": "KILLS",
        "kills_streak": "KILLS STREAK",
        "kills_per_minute": "KILLS/MINUTE",
        "kill_death_ratio": "KILLS/DEATHS",
        "days_of_vip": "DAYS OF VIP",
        "you_won": "YOU WON",
    },
    "fr": {
        "end_of_game": "FIN DE PARTIE",
        "best_scores": "MEILLEURS SCORES",
        "offense": "ATTAQUE",
        "defense": "DÉFENSE",
        "support": "SUPPORT",
        "combat": "COMBAT",
        "kills": "KILLS",
        "kills_streak": "SÉRIE DE KILLS",
        "kills_per_minute": "KILLS/MINUTE",
        "kill_death_ratio": "KILLS/MORTS",
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
) -> GameRewards:
    """Apply the reward rules to the final stats of a game. No side effect.

    Rewards require ``vip_rewards_enabled`` and ``min_players_for_rewards``
    connected players. Every player in the final stats can be rewarded, connected
    or not; unlimited VIPs are never rewarded.
    """
    players = [p for p in stats if p.get("player_id")]
    result = GameRewards(
        rewards_allowed=config.vip_rewards_enabled
        and online_count >= config.min_players_for_rewards
    )
    result.names = {p["player_id"]: p.get("player") or p["player_id"] for p in players}

    def add_days(player_id: str, days: int) -> None:
        if result.rewards_allowed and days > 0:
            result.days[player_id] = result.days.get(player_id, 0) + days

    if players:
        for category in config.top_categories:
            best = max(players, key=lambda p: _stat(p, category))
            result.top_players.append((category, best))
            add_days(best["player_id"], config.top_category_reward_days)

    for player in players:
        player_id = player["player_id"]
        hits = individual_hits(player, config)
        result.hits[player_id] = hits
        add_days(player_id, config.individual_reward_days * len(hits))
        if player_id in vips:
            add_days(player_id, config.fidelity_reward_days)

    for player_id in list(result.days):
        if player_id in vips and is_unlimited_vip(vips[player_id]):
            del result.days[player_id]
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
    """Same layout as the original plugin."""
    labels = LABELS[config.language]
    days_suffix = f"{labels['days_of_vip']}"
    lines = [f"█ {labels['end_of_game']} █", "", f"▒ {labels['best_scores']} ▒"]
    if rewards.rewards_allowed and config.top_category_reward_days:
        lines.append(f"+{config.top_category_reward_days} {days_suffix} / TOP")
    for category, best in rewards.top_players:
        lines.append(
            f"Top {labels[category]} : {best.get('player')} "
            f"({_format_value(_stat(best, category))})"
        )

    player_id = player["player_id"]
    hits = rewards.hits.get(player_id, set())
    lines += ["", f"▒ {player.get('player')} ▒"]
    for key in INDIVIDUAL_STATS:
        line = f"{labels[key]} : {_format_value(_stat(player, key))}"
        if key in hits and rewards.rewards_allowed and config.individual_reward_days:
            line += f" (+{config.individual_reward_days} {days_suffix})"
        lines.append(line)

    if days := rewards.days.get(player_id):
        lines += ["", f"{labels['you_won']} {days} {days_suffix}!"]
    return "\n".join(lines)


def processed_key(struct_log: StructuredLogLineWithMetaData) -> str:
    return f"end_of_game_rewards:{get_server_number()}:{struct_log.get('timestamp_ms')}"


def process_end_of_game(
    rcon: Rcon,
    config: EndOfGameRewardsUserConfig,
    stats: list[dict],
    now: datetime | None = None,
) -> GameRewards:
    """Message connected players and grant VIP days (nothing is sent in dry-run)."""
    now = now or datetime.now(UTC)
    online = set(rcon.get_player_ids(as_dict=True).values())
    vips = {vip["player_id"]: vip for vip in rcon.get_vip_ids()}
    rewards = compute_rewards(stats, config, vips, len(online))

    if config.send_stats_message and not config.dry_run:
        recipients = [p for p in stats if p.get("player_id") in online]
        if recipients:
            try:
                rcon.bulk_message_players(
                    [p["player_id"] for p in recipients],
                    [build_player_message(p, rewards, config) for p in recipients],
                )
            except Exception:
                logger.exception("End of game rewards: could not message players")

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
        granted.append((player_id, name, days, expiration))
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
            for player_id, name, days, expiration in granted:
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
                            }
                        ),
                        command_result=result,
                    )
                )
    except Exception:
        logger.exception("End of game rewards: could not write the audit log")
    if config.discord_webhook_url is not None:
        summary = ", ".join(
            f"{name} +{days}d (until {expiration:%d/%m/%Y %H:%M} UTC)"
            for _, name, days, expiration in granted
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
        process_end_of_game(rcon, config, stats)
    except Exception:
        logger.exception("End of game rewards: processing failed")
