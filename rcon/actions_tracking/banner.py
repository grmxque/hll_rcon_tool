"""In-game broadcast thanking the players for their actions of the match (garrisons, nodes).

After a newly counted action (at most once every ``thanks_banner_interval_minutes``), the
broadcast shows the garrison builders, then the node builders, then the previous broadcast comes back.
The next messages are scheduled on the existing rq queue (``rcon.workers.broadcast``).
"""

import logging
from datetime import timedelta

from rcon.actions_tracking.model import PlayerActions, rank_players
from rcon.cache_utils import get_redis_client
from rcon.user_config.end_of_game_rewards import EndOfGameRewardsUserConfig
from rcon.utils import get_server_number

logger = logging.getLogger(__name__)

MAX_NAMES = 8
MAX_NAME_LENGTH = 20

LABELS = {
    "en": {
        "garrison": "Thanks for building garrisons: ",
        "node": "Thanks for building nodes: ",
        "others": "+{count} others",
    },
    "fr": {
        "garrison": "Merci pour la construction de garnisons : ",
        "node": "Merci pour la construction des nœuds : ",
        "others": "+{count} autres",
    },
}


def short_name(name: str, length: int = MAX_NAME_LENGTH) -> str:
    return name if len(name) <= length else name[: length - 1] + "…"


def _builders_line(prefix: str, builders: list[tuple[str, int]], labels: dict) -> str:
    shown = ", ".join(
        f"{short_name(name)} ({count})" for name, count in builders[:MAX_NAMES]
    )
    if len(builders) > MAX_NAMES:
        shown += ", " + labels["others"].format(count=len(builders) - MAX_NAMES)
    return prefix + shown


def build_thanks_messages(
    config: EndOfGameRewardsUserConfig, actions: dict[str, PlayerActions]
) -> list[str]:
    """One message per enabled action with at least one player, in this order: garrisons, nodes.

    Players are sorted like the TOP 3: by count, then by who reached it first.
    """
    labels = LABELS[config.language]
    messages = []
    for action, enabled in (
        ("garrison", config.garrison_rewards_enabled),
        ("node", config.node_rewards_enabled),
    ):
        players = rank_players(actions, action) if enabled else []
        if players:
            messages.append(
                _builders_line(
                    labels[action],
                    [(p.name, p.count(action)) for p in players],
                    labels,
                )
            )
    return messages


def banner_lock_key() -> str:
    return f"actions_thanks_banner:{get_server_number()}"


def send_thanks_banner(
    rcon,
    config: EndOfGameRewardsUserConfig,
    actions: dict[str, PlayerActions],
) -> bool:
    """Broadcast the thanks if the minimum interval has elapsed. Returns True when sent."""
    messages = build_thanks_messages(config, actions)
    if not messages:
        return False
    red = get_redis_client()
    if not red.set(
        banner_lock_key(), 1, nx=True, ex=config.thanks_banner_interval_minutes * 60
    ):
        return False
    if config.dry_run:
        logger.info("Actions thanks banner (dry run): %s", " | ".join(messages))
        return True

    from rcon.workers import broadcast, get_queue

    previous = rcon.set_broadcast(messages[0])
    queue = get_queue()
    delay = timedelta(seconds=config.thanks_banner_seconds)
    for index, message in enumerate(messages[1:], start=1):
        queue.enqueue_in(delay * index, broadcast, message)
    queue.enqueue_in(delay * len(messages), broadcast, previous)
    logger.info("Actions thanks banner sent: %s", " | ".join(messages))
    return True
