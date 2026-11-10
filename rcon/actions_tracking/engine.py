"""Follows the players of one match poll by poll and runs the action detectors.

Pure (no I/O): the state is JSON serialisable, so it can be persisted between polls (``tracker.py``)
and recorded polls can be replayed (``__main__.py``).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from rcon.actions_tracking.context import (
    MAX_POLL_INTERVAL,
    SPAWN_HISTORY_SECONDS,
    Gain,
    PollContext,
    SpawnLedger,
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
        self.spawns = SpawnLedger(state.get("spawns"))
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
            "spawns": self.spawns.to_state(),
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
        current = {p.player_id: p for p in snapshot.players}
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
        )
        for detector in self.detectors.values():
            decisions += detector.observe(ctx)

        for sample in current.values():
            self._remember(sample, ts)
        self.spawns.prune(ts)
        self.last_ts = ts
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

    def _remember(self, sample: PlayerSample, ts: float) -> None:
        prev = self.players.get(sample.player_id) or {}
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
            "last_alive": [sample.x, sample.y]
            if sample.alive
            else prev.get("last_alive"),
            "deaths_alive": sample.deaths
            if sample.alive
            else prev.get("deaths_alive", sample.deaths),
        }
