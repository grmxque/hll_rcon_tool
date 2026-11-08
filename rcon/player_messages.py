"""Private messages to several players, one RCON exchange per player.

``ServerCtl.bulk_message_players`` pipelines every ``MessagePlayer`` request on one
connection before reading the replies. Game servers have been seen closing the
connection in the middle of such a burst ("Failed to unpack response header: b''"),
which fails the whole batch while an unknown number of players already got the
message. Sending one request at a time keeps a failure limited to one player.
"""

import logging
from collections.abc import Iterable

from rcon.rcon import Rcon

logger = logging.getLogger(__name__)


def message_players(
    rcon: Rcon, deliveries: Iterable[tuple[str, str]], *, by: str
) -> int:
    """Send each ``(player_id, message)`` and return how many were delivered.

    A failed send is logged and does not stop the others.
    """
    sent = 0
    for player_id, message in deliveries:
        try:
            rcon.message_player(player_id=player_id, message=message, by=by)
        except Exception:
            logger.exception("%s: could not message %s", by, player_id)
            continue
        sent += 1
    return sent
