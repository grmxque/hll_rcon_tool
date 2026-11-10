"""What every detector sees of a poll: players, support gains, teammate spawns and spawn points."""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass

from rcon.actions_tracking.model import PlayerSample

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


class SpawnLedger:
    """Teammate spawns observed by the polls and the spawn points learned for each player.

    A spawn credits a single spawn point owner: the detectors claim the spawns they use
    (``credit``), so that the same spawn cannot explain the gains of two players.
    """

    def __init__(self, state: dict | None = None):
        state = state or {}
        self.spawns: list[dict] = state.get("spawns", [])
        self.points: dict[str, list[dict]] = state.get("points", {})

    def to_state(self) -> dict:
        return {"spawns": self.spawns, "points": self.points}

    def record(
        self,
        current: dict[str, PlayerSample],
        players: dict[str, dict],
        ts: float,
        first_poll: bool,
    ) -> None:
        """Spawns between the previous poll (``players``) and this one."""
        for player_id, sample in current.items():
            if not sample.alive or sample.team is None:
                continue
            prev = players.get(player_id)
            if prev is None:
                spawned = not first_poll  # connected and deployed between two polls
            elif not prev["alive"]:
                # Back alive where they went down without a new death: revived, not respawned.
                last = prev.get("last_alive")
                spawned = (
                    last is None
                    or sample.deaths > prev.get("deaths_alive", prev["deaths"])
                    or distance(sample.x, sample.y, last[0], last[1]) > REVIVE_RADIUS
                )
            else:
                spawned = (
                    sample.deaths > prev["deaths"]
                    and distance(sample.x, sample.y, prev["x"], prev["y"])
                    > FAST_RESPAWN_DISTANCE
                )
            if spawned:
                self.spawns.append(
                    {
                        "player_id": player_id,
                        "team": sample.team,
                        "unit_id": sample.unit_id,
                        "x": sample.x,
                        "y": sample.y,
                        "ts": ts,
                        "credited_to": None,
                    }
                )

    def at(self, ts: float) -> list[dict]:
        return [s for s in self.spawns if s["ts"] == ts]

    def pool(self, sample: PlayerSample, since: float, ts: float) -> list[dict]:
        """Unclaimed teammate spawns that may have credited ``sample`` during (since, ts].

        Spawns of the previous interval stay usable: the credit and the new position are not always
        reported by the same poll.
        """
        return [
            s
            for s in self.spawns
            if s["credited_to"] is None
            and s["team"] == sample.team
            and s["player_id"] != sample.player_id
            and since - MAX_POLL_INTERVAL / 4 < s["ts"] <= ts
        ]

    def bounds(
        self,
        sample: PlayerSample,
        prev: dict,
        ts: float,
        current: dict[str, PlayerSample],
        players: dict[str, dict],
    ) -> tuple[int, int]:
        """Largest number of squad / other teammate spawns since the player's previous sample.

        Spawns observed by the polls of the window count (claimed or not). When no poll observed the
        window (data gap), each teammate may have spawned once if it was dead or not connected, plus
        once per death (redeploys count as deaths).
        """
        squad = team = 0
        for spawn in self.spawns:
            if (
                spawn["team"] == sample.team
                and spawn["player_id"] != sample.player_id
                and prev["ts"] < spawn["ts"] <= ts
            ):
                if spawn["unit_id"] == sample.unit_id:
                    squad += 1
                else:
                    team += 1
        for player_id, other in current.items():
            if player_id == sample.player_id or other.team != sample.team:
                continue
            before = players.get(player_id)
            if before is not None and before["ts"] > prev["ts"]:
                continue  # observed during the window: its spawns are in the history
            if before is None:
                unobserved = 1
            else:
                unobserved = (0 if before["alive"] else 1) + max(
                    0, other.deaths - before["deaths"]
                )
            if other.unit_id == sample.unit_id:
                squad += unobserved
            else:
                team += unobserved
        return squad, team

    def learn(self, player_id: str, x: float, y: float, ts: float, kind: str) -> None:
        """Remember (or refresh) a spawn point of ``player_id``."""
        points = self.points.setdefault(player_id, [])
        for point in points:
            if distance(point["x"], point["y"], x, y) <= SPAWN_POINT_RADIUS:
                point["last_ts"] = ts
                return
        points.append({"x": x, "y": y, "last_ts": ts, "since": ts, "kind": kind})

    def forget(self, player_id: str, x: float, y: float) -> None:
        """Forget the spawn points of ``player_id`` near (x, y) (destroyed garrison)."""
        kept = [
            p
            for p in self.points.get(player_id, [])
            if distance(p["x"], p["y"], x, y) > SPAWN_POINT_RADIUS
        ]
        if kept:
            self.points[player_id] = kept
        else:
            self.points.pop(player_id, None)

    def of(self, player_id: str) -> list[dict]:
        return self.points.get(player_id, [])

    def of_teammates(
        self, player_id: str, team: str | None, players: dict[str, dict]
    ) -> list[dict]:
        return [
            point
            for other_id, points in self.points.items()
            if other_id != player_id and players.get(other_id, {}).get("team") == team
            for point in points
        ]

    @staticmethod
    def near(spawn: dict, points: Iterable[dict]) -> bool:
        return any(
            distance(spawn["x"], spawn["y"], p["x"], p["y"]) <= SPAWN_POINT_RADIUS
            for p in points
        )

    def credit(
        self,
        sample: PlayerSample,
        pool: list[dict],
        squad_used: int,
        team_used: int,
        ts: float,
        learn: bool,
    ) -> None:
        """Claim spawns for ``sample``, those on its known spawn points first."""
        own = self.of(sample.player_id)
        ordered = sorted(pool, key=lambda s: not self.near(s, own))
        squad = [s for s in ordered if s["unit_id"] == sample.unit_id][:squad_used]
        team = [s for s in ordered if s["unit_id"] != sample.unit_id][:team_used]
        for spawn in squad + team:
            spawn["credited_to"] = sample.player_id
            if learn:
                self.learn(sample.player_id, spawn["x"], spawn["y"], ts, "spawn")

    def prune(self, ts: float) -> None:
        self.spawns = [s for s in self.spawns if ts - s["ts"] <= SPAWN_HISTORY_SECONDS]
        for player_id, points in list(self.points.items()):
            kept = [p for p in points if ts - p["last_ts"] <= SPAWN_POINT_TTL]
            if kept:
                self.points[player_id] = kept
            else:
                del self.points[player_id]


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

    def name(self, player_id: str) -> str:
        return self.names.get(player_id, player_id)
