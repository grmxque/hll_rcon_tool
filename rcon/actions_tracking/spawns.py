"""Spawns observed by the polls, grouped into classified spawn points.

``SpawnLedger`` holds the recent spawns (5 min) and the spawn points the detectors learned for each
player from his spawn credits. ``SpawnPoints`` groups every settled spawn by position and classifies
each group from what the spawns there show:

- who got the spawn credits: only the owner of a garrison or an outpost gets them, the HQ and the
  airheads credit nobody;
- which squads spawned there: an outpost only takes its own squad, the commander and players without
  a squad never use an outpost;
- when the point was first used and for how long (the HQ from the start of the match, until its end).
"""

from __future__ import annotations

from collections.abc import Iterable

from rcon.actions_tracking.context import (
    FAST_RESPAWN_DISTANCE,
    MAX_POLL_INTERVAL,
    REVIVE_RADIUS,
    SPAWN_HISTORY_SECONDS,
    SPAWN_POINT_RADIUS,
    SPAWN_POINT_TTL,
    distance,
)
from rcon.actions_tracking.model import PlayerSample


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


# A spawn joins its point once its credit is known: the credit can come one or two polls later.
SETTLE_SECONDS = MAX_POLL_INTERVAL
# Spawn point kinds.
HQ = "hq"
GARRISON = "garrison"
OUTPOST = "outpost"
AIRHEAD = "airhead"
UNKNOWN = "unknown"
COMMANDER_UNIT = -1
NO_SQUAD = "none"
# Uncredited spawns of at least this many teammates: a point that credits nobody (HQ, airhead).
UNCREDITED_SPAWNS = 3
# Used this soon after the start of the tracked match, or for this long, a point crediting nobody is
# an HQ: an airhead is placed during the match and only lasts a few minutes.
HQ_EARLY_SECONDS = 180.0
HQ_LIFETIME_SECONDS = 600.0


def unit_key(unit_id: int | None) -> str:
    """JSON key of a squad (players without a squad have no unit)."""
    return NO_SQUAD if unit_id is None else str(unit_id)


def classify(point: dict, match_start: float | None) -> tuple[str, str]:
    """Kind of a spawn point and the reason, from the spawns settled on it."""
    if point.get("garrison"):
        return GARRISON, "a confirmed garrison"
    squads = {unit for unit in point["squads"] if unit not in (NO_SQUAD, "-1")}
    no_outpost = point["commander"] + point["no_squad"]
    credited = point["credited"]
    if credited:
        if len(squads) >= 2 or no_outpost:
            return GARRISON, "credits its owner, used by several squads"
        if point["spawns"] >= 2 and len(credited) == 1:
            return OUTPOST, "credits its owner, used by one squad only"
        return UNKNOWN, "credits its owner, used once"
    others = point["uncredited"] - point["commander"]
    if others < UNCREDITED_SPAWNS:
        if point["commander"]:
            return UNKNOWN, "only the commander spawned there (HQ or his own garrison)"
        return UNKNOWN, "few spawns, crediting nobody"
    early = (
        match_start is not None and point["first_ts"] - match_start <= HQ_EARLY_SECONDS
    )
    if early:
        return HQ, "credits nobody, used since the start of the match"
    if point["last_ts"] - point["first_ts"] >= HQ_LIFETIME_SECONDS:
        return HQ, "credits nobody, used for a long time"
    if point["commander"]:
        return HQ, "credits nobody, the commander spawned there"
    if len(squads) >= 2:
        return AIRHEAD, "credits nobody, placed during the match, several squads"
    return UNKNOWN, "credits nobody, one squad only"


class SpawnPoints:
    """Spawn points of the match, built from the settled spawns of the ``SpawnLedger``."""

    def __init__(self, state: dict | None = None):
        state = state or {}
        self.points: list[dict] = state.get("points", [])
        self.next_id: int = state.get("next_id", 1)
        # Confirmed garrisons [{x, y, team, owner}]: the points there are garrisons.
        self.garrisons: list[dict] = state.get("garrisons", [])

    def to_state(self) -> dict:
        return {
            "points": self.points,
            "next_id": self.next_id,
            "garrisons": self.garrisons,
        }

    def update(self, ledger: SpawnLedger, ts: float, match_start: float | None) -> None:
        """Add the spawns settled since the last update, classify, forget unused points."""
        for spawn in ledger.spawns:
            if "point" not in spawn and ts - spawn["ts"] >= SETTLE_SECONDS:
                spawn["point"] = self._add(spawn)["id"]
        self.points = [p for p in self.points if ts - p["last_ts"] <= SPAWN_POINT_TTL]
        for point in self.points:
            point["kind"], point["reason"] = classify(point, match_start)

    def garrison_confirmed(self, owner: str, x: float, y: float, team: str) -> None:
        """A garrison counted by the garrison detector: its points are garrisons."""
        self.garrisons.append({"x": x, "y": y, "team": team, "owner": owner})
        for point in self.points:
            if point["team"] == team and self._near(point, x, y):
                point["garrison"] = owner

    def near(self, team: str | None, x: float, y: float) -> dict | None:
        """The point of ``team`` closest to (x, y), within ``SPAWN_POINT_RADIUS``."""
        found = [
            (distance(p["x"], p["y"], x, y), p)
            for p in self.points
            if p["team"] == team and self._near(p, x, y)
        ]
        return min(found, key=lambda f: f[0])[1] if found else None

    def of_kind(self, *kinds: str) -> list[dict]:
        return [p for p in self.points if p["kind"] in kinds]

    @staticmethod
    def _near(point: dict, x: float, y: float) -> bool:
        return distance(point["x"], point["y"], x, y) <= SPAWN_POINT_RADIUS

    def _add(self, spawn: dict) -> dict:
        point = self.near(spawn["team"], spawn["x"], spawn["y"])
        if point is None:
            point = {
                "id": self.next_id,
                "team": spawn["team"],
                "x": spawn["x"],
                "y": spawn["y"],
                "first_ts": spawn["ts"],
                "last_ts": spawn["ts"],
                "spawns": 0,
                "squads": {},
                "commander": 0,
                "no_squad": 0,
                "credited": {},
                "uncredited": 0,
                "garrison": next(
                    (
                        g["owner"]
                        for g in self.garrisons
                        if g["team"] == spawn["team"]
                        and distance(g["x"], g["y"], spawn["x"], spawn["y"])
                        <= SPAWN_POINT_RADIUS
                    ),
                    None,
                ),
                "kind": UNKNOWN,
                "reason": "",
            }
            self.next_id += 1
            self.points.append(point)
        # The position is the mean of its spawns: they land around the point.
        count = point["spawns"]
        point["x"] = (point["x"] * count + spawn["x"]) / (count + 1)
        point["y"] = (point["y"] * count + spawn["y"]) / (count + 1)
        point["spawns"] = count + 1
        point["first_ts"] = min(point["first_ts"], spawn["ts"])
        point["last_ts"] = max(point["last_ts"], spawn["ts"])
        unit = unit_key(spawn["unit_id"])
        point["squads"][unit] = point["squads"].get(unit, 0) + 1
        if spawn["unit_id"] == COMMANDER_UNIT:
            point["commander"] += 1
        elif spawn["unit_id"] is None:
            point["no_squad"] += 1
        if spawn["credited_to"]:
            owner = spawn["credited_to"]
            point["credited"][owner] = point["credited"].get(owner, 0) + 1
        else:
            point["uncredited"] += 1
        return point
