"""Follows the players of one match poll by poll and runs the action detectors.

Pure (no I/O): the state is JSON serialisable, so it can be persisted between polls (``tracker.py``)
and recorded polls can be replayed (``__main__.py``).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import replace

from rcon.actions_tracking.context import (
    MAX_POLL_INTERVAL,
    SPAWN_HISTORY_SECONDS,
    Gain,
    PollContext,
)
from rcon.actions_tracking.detectors import DETECTORS, ActionDetector
from rcon.actions_tracking.model import (
    COUNTER,
    IGNORED,
    RESET,
    Decision,
    PlayerActions,
    PlayerSample,
    PollSnapshot,
)
from rcon.actions_tracking.rules import (
    DEFAULT_GAME_RULES,
    DEFAULT_SCORE_TABLE,
    GameRules,
    ScoreTable,
)
from rcon.actions_tracking.sectors import track_owners
from rcon.actions_tracking.spawns import SpawnLedger, SpawnPoints


class ActionsEngine:
    """Detection state of one match."""

    def __init__(
        self,
        state: dict | None = None,
        *,
        roles: Mapping[str, Iterable[str]] | None = None,
        table: ScoreTable = DEFAULT_SCORE_TABLE,
        rules: GameRules = DEFAULT_GAME_RULES,
        detectors: Iterable[type[ActionDetector]] = DETECTORS,
    ):
        state = state or {}
        roles = roles or {}
        self.table = table
        self.last_ts: float | None = state.get("last_ts")
        self.players: dict[str, dict] = state.get("players", {})
        self.names: dict[str, str] = state.get("names", {})
        self.finalized: bool = state.get("finalized", False)
        # First poll of the tracked match: the HQ points are used from the start.
        self.first_ts: float | None = state.get("first_ts")
        self.spawns = SpawnLedger(state.get("spawns"))
        self.points = SpawnPoints(state.get("spawn_points"))
        # Sectors with their current and last known owner, and the captures seen (diagnostics).
        self.sectors: list[dict] = state.get("sectors", [])
        self.captures: list[dict] = state.get("captures", [])
        detector_states = state.get("detectors", {})
        self.detectors: dict[str, ActionDetector] = {
            cls.action: cls(
                detector_states.get(cls.action),
                roles=roles.get(cls.action),
                table=table,
                rules=rules,
            )
            for cls in detectors
        }

    def to_state(self) -> dict:
        return {
            "last_ts": self.last_ts,
            "players": self.players,
            "names": self.names,
            "finalized": self.finalized,
            "first_ts": self.first_ts,
            "spawns": self.spawns.to_state(),
            "spawn_points": self.points.to_state(),
            "sectors": self.sectors,
            "captures": self.captures,
            "detectors": {
                action: detector.to_state()
                for action, detector in self.detectors.items()
            },
        }

    def observe(self, snapshot: PollSnapshot) -> list[Decision]:
        """Process the interval between the previous poll and ``snapshot``."""
        if self.finalized or (self.last_ts is not None and snapshot.ts <= self.last_ts):
            return []  # already processed (replayed poll) or match over
        ts = snapshot.ts
        global_gap = self.last_ts is not None and ts - self.last_ts > MAX_POLL_INTERVAL
        raw = {p.player_id: p for p in snapshot.players}
        current = {
            player_id: sample
            if self._deployed(sample, self.players.get(player_id))
            else replace(sample, x=0.0, y=0.0, z=0.0)
            for player_id, sample in raw.items()
        }
        for sample in snapshot.players:
            self.names[sample.player_id] = sample.name
        if (
            not global_gap
        ):  # spawns of a gap are not observed: bounded from deaths instead
            self.spawns.record(current, self.players, ts, self.last_ts is None)

        decisions: list[Decision] = []
        gains: dict[str, Gain] = {}
        for player_id, sample in current.items():
            prev = self.players.get(player_id)
            if prev is None:
                continue  # first sample: it becomes the reference
            if decision := self._check_counter(sample, prev, ts, global_gap):
                decisions.append(decision)
            elif sample.support > prev["support"]:
                gains[player_id] = Gain(
                    sample, prev, sample.support - prev["support"], ts - prev["ts"]
                )

        ctx = PollContext(
            ts=ts,
            global_gap=global_gap,
            current=current,
            players=self.players,
            names=self.names,
            gains=gains,
            spawns=self.spawns,
            points=self.points,
        )
        if snapshot.sectors:
            self.sectors, captures = track_owners(self.sectors, snapshot.sectors)
            for capture in captures:
                decisions += self._sector_captured(capture, ctx)
        for detector in self.detectors.values():
            decisions += detector.observe(ctx)

        for player_id, sample in current.items():
            self._remember(sample, raw[player_id], ts, global_gap)
        if self.first_ts is None:
            self.first_ts = ts
        self.points.update(self.spawns, ts, self.first_ts)
        self.spawns.prune(ts)
        self.last_ts = ts
        return decisions

    def _sector_captured(self, capture: dict, ctx: PollContext) -> list[Decision]:
        """The garrisons and outposts of the team that lost the sector are destroyed."""
        self.captures.append(
            {"ts": ctx.ts, "team": capture["team"], "index": capture["index"]}
        )
        self.points.sector_captured(capture["team"], capture["sector"], self.spawns)
        decisions = []
        for detector in self.detectors.values():
            decisions += detector.sector_captured(capture, ctx)
        return decisions

    def finalize(self, end_ts: float | None = None) -> list[Decision]:
        """Settle what is still pending at the end of the match; later polls are ignored."""
        if self.finalized:
            return []
        end_ts = end_ts if end_ts is not None else (self.last_ts or 0)
        decisions = []
        for detector in self.detectors.values():
            decisions += detector.finalize(end_ts, self.names)
        self.finalized = True
        return decisions

    def summary(self) -> dict[str, PlayerActions]:
        """Actions per player (decided ones only, pending ones excluded)."""
        counts: dict[str, dict[str, int]] = {}
        reached: dict[str, dict[str, float | None]] = {}
        for action, detector in self.detectors.items():
            for player_id, (count, ts) in detector.results().items():
                if count > 0:
                    counts.setdefault(player_id, {})[action] = count
                    reached.setdefault(player_id, {})[action] = ts
        return {
            player_id: PlayerActions(
                player_id=player_id,
                name=self.names.get(player_id, player_id),
                counts=counts[player_id],
                reached=reached[player_id],
            )
            for player_id in sorted(counts)
        }

    # ------------------------------------------------------------------ counters

    def _check_counter(
        self, sample: PlayerSample, prev: dict, ts: float, global_gap: bool
    ) -> Decision | None:
        """Decisions that only reset the reference of a player (no action possible)."""

        def reset(verdict: str, reason: str) -> Decision:
            return Decision(ts, sample.player_id, sample.name, COUNTER, verdict, reason)

        interval = ts - prev["ts"]
        if not global_gap and interval > SPAWN_HISTORY_SECONDS:
            # Absent from the polls for a long time (disconnected) while the others were observed:
            # the spawns of that period are no longer known, nothing can be decided.
            return reset(RESET, f"absent from the polls for {interval:.0f}s")
        if sample.team != prev["team"]:
            return reset(RESET, "team changed")
        if sample.support < prev["support"]:
            return reset(
                RESET,
                f"support dropped {prev['support']} -> {sample.support} (reconnection)",
            )
        gain = sample.support - prev["support"]
        limit = max(
            self.table.min_implausible_gain, self.table.max_gain_per_second * interval
        )
        if gain > limit:
            return reset(
                IGNORED,
                f"implausible gain +{gain} in {interval:.0f}s (stats leak, counter glitch)",
            )
        return None

    @staticmethod
    def _deployed(sample: PlayerSample, prev: dict | None) -> bool:
        """Whether the player is in the game, not dead nor in the menus.

        The game reports (0, 0, 0) for a dead player, but a player in the menus (just connected,
        choosing a team or a role) is reported at a fixed position near the edge of the map, and a
        dead body stays at the place of the death for a few seconds with the death already counted.
        So after connecting, dying or changing team, a player is only deployed once his position
        changes (seen in game, see docs/actions-tracking.md).
        """
        position = (sample.x, sample.y, sample.z)
        if position == (0.0, 0.0, 0.0) or prev is None:
            return False
        if sample.deaths > prev["deaths"] or sample.team != prev["team"]:
            return False
        if prev.get("deployed", prev["alive"]):
            return True
        return position != tuple(prev.get("raw", (0.0, 0.0, 0.0)))

    def _remember(
        self, sample: PlayerSample, raw: PlayerSample, ts: float, gap: bool = False
    ) -> None:
        """``sample`` as the detectors saw it (undeployed: at (0, 0, 0)), ``raw`` as reported.

        ``gap``: the polls stopped before this one. A player back in the game with a new death
        respawned during the gap, somewhere unknown: his next move is not a spawn.
        """
        prev = self.players.get(sample.player_id)
        respawned_unseen = (
            gap
            and prev is not None
            and raw.deaths > prev["deaths"]
            and (raw.x, raw.y, raw.z) != (0.0, 0.0, 0.0)
        )
        raw_position = (raw.x, raw.y, raw.z)
        if prev is None and raw_position != (0.0, 0.0, 0.0):
            # First seen at a position: if he only walks from there he was already in the game
            # (CRCON started mid-match), not spawning.
            last_alive, deaths_alive = [raw.x, raw.y], raw.deaths
        else:
            prev = prev or {}
            last_alive = (
                [sample.x, sample.y] if sample.alive else prev.get("last_alive")
            )
            deaths_alive = (
                sample.deaths
                if sample.alive
                else prev.get("deaths_alive", sample.deaths)
            )
        self.players[sample.player_id] = {
            "ts": ts,
            "team": sample.team,
            "unit_id": sample.unit_id,
            "role": sample.role,
            "support": sample.support,
            "deaths": sample.deaths,
            "x": sample.x,
            "y": sample.y,
            "alive": sample.alive,
            "deployed": sample.alive,
            "raw": list(raw_position),
            "last_alive": last_alive,
            "deaths_alive": deaths_alive,
            "respawned_unseen": respawned_unseen,
        }
