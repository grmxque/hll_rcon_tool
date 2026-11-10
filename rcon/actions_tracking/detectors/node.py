"""Resource nodes, detected from the support gain of their builder and their passive production.

A construction value (blueprint 40, built 50) alone cannot tell a node from a repair station: the
candidate is confirmed when a new passive production stream (+10 per minute, credited to the builder)
starts after it. A regular stream also proves a node whose construction was missed (data gap,
tracking started later), even after the builder changed class. Passive production never counts as a
new construction. The game limits (one node of each type per player, 9 per team) exclude the
candidates that cannot be nodes.
"""

from __future__ import annotations

import math
from itertools import product

from rcon.actions_tracking.context import MAX_POLL_INTERVAL, Gain, PollContext
from rcon.actions_tracking.detectors.base import ActionDetector
from rcon.actions_tracking.model import (
    CONFIRMED,
    NONE,
    PENDING,
    REJECTED,
    Decision,
    PlayerSample,
)

CONFIRMATION_SECONDS = 150.0
STREAM_MAX_MISSES = 2
STREAM_CONFIRMED_HITS = 3
REPORTED_GAIN = 40
MAX_NEW_STREAMS_PER_POLL = 3
# Their passive income (outposts: +10 per minute) looks like a node's.
OUTPOST_ROLES = frozenset({"officer", "squadleader", "spotter"})


class NodeDetector(ActionDetector):
    action = "node"
    default_roles = ("engineer",)

    def load(self, state: dict) -> None:
        self.builds: dict[str, list[dict]] = state.get("builds", {})
        self.streams: dict[str, list[dict]] = state.get("streams", {})
        self.max_streams: dict[str, dict] = state.get("max_streams", {})
        self.pending: list[dict] = state.get("pending", [])

    def to_state(self) -> dict:
        return {
            "builds": self.builds,
            "streams": self.streams,
            "max_streams": self.max_streams,
            "pending": self.pending,
        }

    def results(self) -> dict[str, tuple[int, float | None]]:
        """The larger of the confirmed constructions and the most simultaneous streams."""
        result = {}
        for player_id in set(self.builds) | set(self.max_streams):
            builds = sorted(b["ts"] for b in self.builds.get(player_id, []))
            streams = self.max_streams.get(player_id, {"count": 0, "ts": None})
            if streams["count"] > len(builds):
                result[player_id] = (streams["count"], streams["ts"])
            elif builds:
                result[player_id] = (len(builds), builds[-1])
        return result

    # ------------------------------------------------------------------ poll

    def observe(self, ctx: PollContext) -> list[Decision]:
        decisions = []
        for player_id in list(self.streams):
            if player_id not in ctx.gains:
                self._miss_due_streams(player_id, ctx.ts)
        for player_id, gain in ctx.gains.items():
            is_builder = gain.prev["role"] in self.roles
            if is_builder or player_id in self.streams:
                decisions += self._analyse(ctx, gain, is_builder)
        decisions += self._settle_pending(ctx)
        return decisions

    def finalize(self, end_ts: float, names: dict[str, str]) -> list[Decision]:
        return [
            self._confirm(pending, end_ts, "match ended before confirmation", names)
            for pending in list(self.pending)
        ]

    # ------------------------------------------------------------------ game limits

    def _standing(self, player_id: str, with_pending: bool) -> int:
        """Nodes this player is known to have: producing streams, plus unconfirmed constructions
        when ``with_pending`` (their own stream will confirm them, it does not add a node)."""
        pending = sum(1 for p in self.pending if p["player_id"] == player_id)
        return len(self.streams.get(player_id, [])) + (pending if with_pending else 0)

    def _team_standing(
        self, ctx: PollContext, team: str | None, with_pending: bool
    ) -> int:
        def team_of(player_id):
            sample = ctx.current.get(player_id)
            return sample.team if sample else ctx.players.get(player_id, {}).get("team")

        owners = set(self.streams) | {p["player_id"] for p in self.pending}
        return sum(
            self._standing(pid, with_pending) for pid in owners if team_of(pid) == team
        )

    def _free_slots(
        self, ctx: PollContext, sample: PlayerSample, with_pending: bool
    ) -> int:
        """Nodes the player can still have, given the per player and per team limits."""
        rules = self.rules
        return min(
            rules.max_nodes_per_player - self._standing(sample.player_id, with_pending),
            rules.max_nodes_per_team
            - self._team_standing(ctx, sample.team, with_pending),
        )

    # ------------------------------------------------------------------ analysis

    def _due_streams(self, player_id: str, since: float, ts: float, interval: float):
        """Streams whose next tick may be reported by this poll (anchored on an observed poll, so
        the real tick may come up to one interval earlier than predicted)."""
        tolerance = interval + 2.0
        return [
            s
            for s in self.streams.get(player_id, [])
            if since - tolerance < s["next_due"] <= ts + tolerance
        ]

    def _miss_due_streams(self, player_id: str, ts: float) -> None:
        """Overdue ticks count as misses; a stream missing repeatedly is a destroyed node."""
        kept = []
        for stream in self.streams.get(player_id, []):
            while stream["next_due"] + 2 * MAX_POLL_INTERVAL < ts:
                stream["misses"] += 1
                stream["next_due"] += self.table.node_passive_period
            if stream["misses"] <= STREAM_MAX_MISSES:
                kept.append(stream)
        if kept:
            self.streams[player_id] = kept
        else:
            self.streams.pop(player_id, None)

    def _analyse(
        self, ctx: PollContext, gain: Gain, is_builder: bool
    ) -> list[Decision]:
        sample, prev, ts, table = gain.sample, gain.prev, ctx.ts, self.table
        if gain.is_gap:
            # Over a gap the passive ticks and repairs make the gain meaningless; a node built
            # during the gap shows up as a new passive stream afterwards.
            self._miss_due_streams(sample.player_id, ts)
            return []
        due = self._due_streams(sample.player_id, prev["ts"], ts, gain.interval)
        repairs = math.ceil(gain.interval / table.repair_period)
        limit = None
        if is_builder:
            rules = self.rules
            if self._standing(sample.player_id, True) >= rules.max_nodes_per_player:
                limit = f"already {rules.max_nodes_per_player} nodes (one of each type)"
            elif (
                self._team_standing(ctx, sample.team, True) >= rules.max_nodes_per_team
            ):
                limit = f"the team already has {rules.max_nodes_per_team} nodes"
        can_build = is_builder and limit is None
        # New streams come from nodes not producing yet (pending or missed constructions); never
        # more than the player can own.
        new_cap = (
            max(0, min(MAX_NEW_STREAMS_PER_POLL, self._free_slots(ctx, sample, False)))
            if is_builder and prev["role"] not in OUTPOST_ROLES
            else 0
        )

        best = None
        # Cost: prefer known passive ticks, then repairs, then new streams, then a construction.
        for blueprint, built, known, new, repair in product(
            (0, 1) if can_build else (0,),
            (0, 1) if can_build else (0,),
            range(len(due) + 1),
            range(new_cap + 1),
            range(repairs + 1),
        ):
            residual = gain.points - (
                blueprint * table.node_blueprint
                + built * table.node_built
                + (known + new) * table.node_passive
                + repair * table.repair
            )
            if residual < 0:
                continue
            cost = 2 * (blueprint + built) + 1.5 * new + repair + 0.1 * known
            if best is None or (residual, cost) < best[0]:
                best = ((residual, cost), blueprint, built, known, new, repair)
        (residual, _), blueprint, built, known, new, repair = best
        without_build = min(
            self._residuals_without_build(gain.points, len(due), new_cap, repairs),
            default=gain.points,
        )

        # Known streams that ticked are re-anchored on this poll (the tick happened at most one
        # interval earlier).
        for stream in sorted(due, key=lambda s: s["next_due"])[:known]:
            stream["next_due"] = ts + table.node_passive_period
            stream["hits"] += 1
        self._miss_due_streams(sample.player_id, ts)
        for _ in range(new):
            self._add_stream(sample.player_id, ts)
        decisions = self._confirm_by_stream(sample, ts, new, ctx.names)
        self._update_max_streams(sample.player_id, ts)

        details = {
            "role": prev["role"],
            "known_streams_due": len(due),
            "explanation": {
                "blueprint": blueprint,
                "built": built,
                "passive_known": known,
                "passive_new": new,
                "repair": repair,
                "unexplained": residual,
            },
            "unexplained_without_construction": without_build,
        }

        def decision(verdict: str, reason: str) -> Decision:
            return Decision(
                ts,
                sample.player_id,
                sample.name,
                self.action,
                verdict,
                reason,
                gain.points,
                gain.interval,
                details,
            )

        # The best explanation already prefers passive ticks and repairs at equal residual: a
        # construction is retained only when it explains more than they can.
        if (blueprint or built) and without_build > residual:
            self.pending.append(
                {
                    "player_id": sample.player_id,
                    "ts": ts,
                    "deadline": ts + CONFIRMATION_SECONDS,
                    "gain": gain.points,
                }
            )
            decisions.append(
                decision(
                    PENDING,
                    "construction value (blueprint 40 / built 50); waiting for its passive "
                    "production to tell a node from a repair station",
                )
            )
        elif limit and gain.points >= table.node_blueprint:
            decisions.append(decision(NONE, f"cannot be a node: {limit}"))
        elif gain.points >= REPORTED_GAIN:
            decisions.append(
                decision(NONE, "passive production / repairs explain the gain")
            )
        return decisions

    def _residuals_without_build(self, gain: int, due: int, new_cap: int, repairs: int):
        table = self.table
        for known, new, repair in product(
            range(due + 1), range(new_cap + 1), range(repairs + 1)
        ):
            residual = gain - (known + new) * table.node_passive - repair * table.repair
            if residual >= 0:
                yield residual

    def _add_stream(self, player_id: str, ts: float) -> None:
        self.streams.setdefault(player_id, []).append(
            {
                "next_due": ts + self.table.node_passive_period,
                "hits": 1,
                "misses": 0,
                "since": ts,
            }
        )

    def _update_max_streams(self, player_id: str, ts: float) -> None:
        confirmed = sum(
            1
            for s in self.streams.get(player_id, [])
            if s["hits"] >= STREAM_CONFIRMED_HITS
        )
        best = self.max_streams.get(player_id, {"count": 0, "ts": None})
        if confirmed > best["count"]:
            self.max_streams[player_id] = {"count": confirmed, "ts": ts}

    # ------------------------------------------------------------------ confirmation

    def _confirm_by_stream(
        self, sample: PlayerSample, ts: float, new_streams: int, names: dict[str, str]
    ) -> list[Decision]:
        """A passive stream that starts after a node candidate proves it was a node."""
        decisions = []
        for pending in [p for p in self.pending if p["player_id"] == sample.player_id]:
            if new_streams <= 0:
                break
            if pending["ts"] < ts <= pending["deadline"]:
                new_streams -= 1
                decisions.append(
                    self._confirm(
                        pending,
                        ts,
                        "passive production started after the construction",
                        names,
                    )
                )
        return decisions

    def _confirm(
        self, pending: dict, ts: float, reason: str, names: dict[str, str]
    ) -> Decision:
        self.pending.remove(pending)
        player_id = pending["player_id"]
        self.builds.setdefault(player_id, []).append({"ts": pending["ts"]})
        return Decision(
            ts,
            player_id,
            names.get(player_id, player_id),
            self.action,
            CONFIRMED,
            reason,
            pending["gain"],
            details={"built_at": pending["ts"]},
        )

    def _settle_pending(self, ctx: PollContext) -> list[Decision]:
        decisions = []
        for pending in list(self.pending):
            if ctx.ts < pending["deadline"]:
                continue
            player_id = pending["player_id"]
            seen = ctx.players.get(player_id, {}).get("ts", 0)
            if player_id not in ctx.current and seen < pending["deadline"]:
                decisions.append(
                    self._confirm(
                        pending,
                        ctx.ts,
                        "player left before the confirmation window ended",
                        ctx.names,
                    )
                )
                continue
            self.pending.remove(pending)
            decisions.append(
                Decision(
                    ctx.ts,
                    player_id,
                    ctx.name(player_id),
                    self.action,
                    REJECTED,
                    "no passive production followed: repair station, destroyed node or other",
                    pending["gain"],
                    details={"built_at": pending["ts"]},
                )
            )
        return decisions
