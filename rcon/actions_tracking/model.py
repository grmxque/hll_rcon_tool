"""Data exchanged by the actions tracking: polled samples, decisions and results per player."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
from typing import Any


@dataclass(frozen=True)
class PlayerSample:
    """One player in one poll: every counter the game server reports, so that recorded polls can
    also be replayed for actions added later."""

    player_id: str
    name: str
    team: str | None
    unit_id: int | None
    role: str | None
    support: int
    deaths: int
    x: float
    y: float
    z: float
    combat: int = 0
    offense: int = 0
    defense: int = 0
    kills: int = 0
    team_kills: int = 0
    vehicle_kills: int = 0
    vehicles_destroyed: int = 0
    loadout: str | None = None
    level: int = 0

    @property
    def alive(self) -> bool:
        """The game reports (0, 0, 0) for a dead player or one on the deployment screen."""
        return (self.x, self.y, self.z) != (0, 0, 0)

    @classmethod
    def from_dict(cls, data: dict) -> PlayerSample:
        """Tolerant to missing keys (recordings made before a field was added)."""
        known = {f.name for f in fields(cls)}
        values = {key: value for key, value in data.items() if key in known}
        values["player_id"] = str(data["player_id"])
        values.setdefault("name", values["player_id"])
        for key in ("team", "unit_id", "role"):
            values.setdefault(key, None)
        for key in ("support", "deaths", "x", "y", "z"):
            values.setdefault(key, 0)
        return cls(**values)


@dataclass(frozen=True)
class PollSnapshot:
    ts: float
    players: tuple[PlayerSample, ...]

    def to_dict(self) -> dict:
        return {"ts": self.ts, "players": [asdict(p) for p in self.players]}

    @classmethod
    def from_dict(cls, data: dict) -> PollSnapshot:
        return cls(
            ts=float(data["ts"]),
            players=tuple(PlayerSample.from_dict(p) for p in data["players"]),
        )


# Verdicts of a decision. "counted" and "confirmed" add an action to the player.
COUNTED = "counted"
NONE = "none"
PENDING = "pending"
CONFIRMED = "confirmed"
REJECTED = "rejected"
RESET = "reset"
IGNORED = "ignored"
COUNTER = "counter"  # kind of the decisions about a player's counters (not an action)


@dataclass
class Decision:
    """Why a change of a player's counters was or was not counted as an action (diagnostics)."""

    ts: float
    player_id: str
    name: str
    kind: str  # action name ("garrison", "node"…) or COUNTER
    verdict: str
    reason: str
    gain: int = 0
    interval: float | None = None
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def counted(self) -> bool:
        return self.verdict in (COUNTED, CONFIRMED) and self.kind != COUNTER

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class PlayerActions:
    """Actions of one player in the match: count per action, and when that count was reached."""

    player_id: str
    name: str
    counts: dict[str, int] = field(default_factory=dict)
    reached: dict[str, float | None] = field(default_factory=dict)

    def count(self, action: str) -> int:
        return self.counts.get(action, 0)


def rank_players(actions: dict[str, PlayerActions], action: str) -> list[PlayerActions]:
    """Players with at least one ``action``, best first.

    Ties are broken by who reached their count first in the match, then by player ID, so the
    ranking is stable and reproducible.
    """
    return sorted(
        (player for player in actions.values() if player.count(action) > 0),
        key=lambda p: (-p.count(action), p.reached.get(action) or 0.0, p.player_id),
    )
