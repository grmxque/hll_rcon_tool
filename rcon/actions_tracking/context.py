"""What every detector sees of a poll: players, support gains, teammate spawns and spawn points."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from rcon.actions_tracking.model import PlayerSample

if TYPE_CHECKING:
    from rcon.actions_tracking.spawns import SpawnLedger, SpawnPoints

# Positions are in game centimetres.
SPAWN_POINT_RADIUS = 5_000  # spawned players appear within ~50 m of their spawn point
REVIVE_RADIUS = (
    2_500  # back alive within 25 m of where they went down: revived, not respawned
)
FAST_RESPAWN_DISTANCE = (
    10_000  # died and respawned between two polls: jumped more than 100 m
)
MAX_POLL_INTERVAL = (
    20.0  # above this, the interval is a data gap (RCON outage, service restart)
)
SPAWN_HISTORY_SECONDS = 300.0
SPAWN_POINT_TTL = 20 * 60.0  # spawn point not used for 20 min: destroyed or moved


def distance(ax: float, ay: float, bx: float, by: float) -> float:
    return math.hypot(ax - bx, ay - by)


@dataclass(frozen=True)
class Gain:
    """Support gain of a player since their previous sample (``prev``, a remembered sample)."""

    sample: PlayerSample
    prev: dict
    points: int
    interval: float

    @property
    def is_gap(self) -> bool:
        return self.interval > MAX_POLL_INTERVAL


@dataclass
class PollContext:
    """One poll as the detectors see it. ``players`` still holds the previous samples."""

    ts: float
    global_gap: bool
    current: dict[str, PlayerSample]
    players: dict[str, dict]
    names: dict[str, str]
    gains: dict[str, Gain]
    spawns: SpawnLedger
    points: SpawnPoints | None = field(default=None)

    def name(self, player_id: str) -> str:
        return self.names.get(player_id, player_id)
