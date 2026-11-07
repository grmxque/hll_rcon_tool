"""Private all-time stats message sent to players when they connect.

Port of the HLL_CRCON_All_time_stats plugin, configured from the settings UI
(``WelcomeStatsUserConfig``) instead of an edited copy of ``rcon/hooks.py``.
"""

import logging
import threading
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select

from rcon.logs.loop import on_connected
from rcon.models import LogLine, PlayerID, PlayerName, PlayerStats, enter_session
from rcon.rcon import Rcon
from rcon.types import StructuredLogLineWithMetaData
from rcon.user_config.welcome_stats import WelcomeStatsUserConfig
from rcon.utils import INDEFINITE_VIP_DATE

logger = logging.getLogger(__name__)
AUTHOR = "welcome_stats"

LABELS = {
    "en": {
        "years": "Y",
        "months": "M",
        "days": "D",
        "played_games": "PLAYED GAMES",
        "play_time": "PLAY TIME",
        "victims": "VICTIMS",
        "nemesis": "NEMESIS",
        "kills": "KILLS",
        "deaths": "DEATHS",
        "ratio": "KD",
        "no_vip": "NOT VIP",
        "unlimited_vip": "UNLIMITED VIP",
        "until": "UNTIL",
    },
    "fr": {
        "years": "A",
        "months": "M",
        "days": "J",
        "played_games": "PARTIES JOUÉES",
        "play_time": "TEMPS DE JEU",
        "victims": "VICTIMES",
        "nemesis": "ENNEMIS JURÉS",
        "kills": "KILLS",
        "deaths": "MORTS",
        "ratio": "KD",
        "no_vip": "NON VIP",
        "unlimited_vip": "VIP ILLIMITÉ",
        "until": "JUSQU'AU",
    },
}


@dataclass(frozen=True)
class AllTimeStats:
    games: int
    kills: int
    deaths: int
    playtime_seconds: int
    top_victims: list[tuple[str, int]]
    top_nemesis: list[tuple[str, int]]


def compact_number(number: int) -> str:
    """999 -> "999", 1500 -> "1.5K"."""
    return str(number) if number < 1000 else f"{number / 1000:.1f}K"


def readable_duration(seconds: int, labels: dict[str, str]) -> str:
    """Years/months/days (only from the first non-zero unit) followed by XXhXXmXXs."""
    years, rest = divmod(int(seconds), 365 * 24 * 3600)
    months, rest = divmod(rest, 30 * 24 * 3600)
    days, rest = divmod(rest, 24 * 3600)
    hours, rest = divmod(rest, 3600)
    minutes, secs = divmod(rest, 60)
    parts = []
    if years:
        parts.append(f"{years}{labels['years']}")
    if years or months:
        parts.append(f"{months}{labels['months']}")
    if years or months or days:
        parts.append(f"{days}{labels['days']}")
    parts.append(f"{hours:02d}h{minutes:02d}m{secs:02d}s")
    return " ".join(parts)


def vip_status(vip_entry: dict | None, labels: dict[str, str]) -> str:
    """``vip_entry`` is an item of ``Rcon.get_vip_ids()``, or None when not VIP.

    A VIP without expiration record is treated as unlimited, like the plugin.
    """
    if vip_entry is None:
        return labels["no_vip"]
    expiration: datetime | None = vip_entry.get("vip_expiration")
    if expiration is None or expiration >= INDEFINITE_VIP_DATE:
        return labels["unlimited_vip"]
    return f"VIP {labels['until']} {expiration.strftime('%d/%m/%Y %H:%M')} UTC"


def _top_opponents(session, db_player_id: int, *, as_killer: bool, limit: int):
    """Most killed players (as_killer) or players who killed this one most."""
    own_column = LogLine.player1_player_id if as_killer else LogLine.player2_player_id
    other_column = LogLine.player2_player_id if as_killer else LogLine.player1_player_id
    rows = session.execute(
        select(other_column, func.count().label("total"))
        .where(own_column == db_player_id, LogLine.type == "KILL")
        .where(other_column.is_not(None))
        .group_by(other_column)
        .order_by(func.count().desc())
        .limit(limit)
    ).all()
    result = []
    for other_id, total in rows:
        name = session.execute(
            select(PlayerName.name)
            .where(PlayerName.player_id_id == other_id)
            .order_by(PlayerName.last_seen.desc().nullslast())
            .limit(1)
        ).scalar()
        result.append((name or "?", total))
    return result


def load_all_time_stats(player_id: str, top_count: int) -> AllTimeStats | None:
    """All-time stats across every recorded game, or None for an unknown player."""
    with enter_session() as session:
        player = session.execute(
            select(PlayerID).where(PlayerID.player_id == player_id)
        ).scalar_one_or_none()
        if player is None:
            return None
        games, kills, deaths = session.execute(
            select(
                func.count(PlayerStats.id),
                func.coalesce(func.sum(PlayerStats.kills), 0),
                func.coalesce(func.sum(PlayerStats.deaths), 0),
            ).where(PlayerStats.player_id_id == player.id)
        ).one()
        top_victims = top_nemesis = []
        if top_count:
            top_victims = _top_opponents(
                session, player.id, as_killer=True, limit=top_count
            )
            top_nemesis = _top_opponents(
                session, player.id, as_killer=False, limit=top_count
            )
        return AllTimeStats(
            games=int(games),
            kills=int(kills),
            deaths=int(deaths),
            playtime_seconds=player.get_total_playtime_seconds(),
            top_victims=top_victims,
            top_nemesis=top_nemesis,
        )


def build_message(
    player_name: str,
    stats: AllTimeStats,
    vip_entry: dict | None,
    labels: dict[str, str],
) -> str:
    """Same layout as the original plugin."""
    ratio = round(stats.kills / max(1, stats.deaths), 2)
    lines = [
        f"▒ {player_name} ▒",
        vip_status(vip_entry, labels),
        "",
        f"{labels['played_games']} : {compact_number(stats.games)}",
        f"{labels['play_time']} : {readable_duration(stats.playtime_seconds, labels)}",
        (
            f"{ratio} {labels['ratio']} ({compact_number(stats.kills)} {labels['kills']}"
            f" / {compact_number(stats.deaths)} {labels['deaths']})"
        ),
    ]
    for title, rows, unit in (
        (labels["victims"], stats.top_victims, labels["kills"]),
        (labels["nemesis"], stats.top_nemesis, labels["deaths"]),
    ):
        if rows:
            lines += ["", f"{title} :"]
            lines += [
                f"{name} ({compact_number(total)} {unit})" for name, total in rows
            ]
    return "\n".join(lines)


def welcome_message(
    rcon: Rcon, config: WelcomeStatsUserConfig, player_name: str, player_id: str
) -> str | None:
    stats = load_all_time_stats(player_id, config.top_count)
    if stats is None or stats.games < config.min_games:
        if config.new_player_message.strip():
            return config.new_player_message.format(player_name=player_name)
        return None
    vip_entry = next(
        (vip for vip in rcon.get_vip_ids() if vip["player_id"] == player_id), None
    )
    return build_message(player_name, stats, vip_entry, LABELS[config.language])


def send_welcome_stats(
    rcon: Rcon, config: WelcomeStatsUserConfig, player_name: str, player_id: str
) -> None:
    """Runs in a timer thread: failures are logged and never reach the log loop."""
    try:
        message = welcome_message(rcon, config, player_name, player_id)
        if message:
            rcon.message_player(
                player_id=player_id,
                player_name=player_name,
                message=message,
                by=AUTHOR,
                save_message=False,
            )
    except Exception:
        logger.exception("Welcome stats: could not message %s", player_id)


@on_connected()
def welcome_stats_on_connected(rcon: Rcon, struct_log: StructuredLogLineWithMetaData):
    player_id = struct_log["player_id_1"]
    player_name = struct_log["player_name_1"]
    if not player_id or not player_name:
        return
    config = WelcomeStatsUserConfig.load_from_db()
    if not config.enabled:
        return
    timer = threading.Timer(
        config.delay_seconds,
        send_welcome_stats,
        args=(rcon, config, player_name, player_id),
    )
    timer.daemon = True
    timer.start()
