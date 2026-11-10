"""Garrisons, detected from the support gain of their builder and confirmed by spawns on them.

Between two polls, the gain of a player whose role can build a garrison is explained in two ways:

- H0, no construction: spawn credits (+5 per squad member, +3 per other teammate spawning on the
  player's spawn point), outpost and marking income, bounded by what the polls observed;
- H1, a garrison: the same plus +50, plus +50 more when it was built on the player's own supplies
  (the supplies consumed are credited to whoever placed them).

A gain that a garrison can explain only makes a **candidate**. Several sources give the same +50
(commander abilities, the supplies of a player consumed by someone else's construction, a spawn
wave), so a garrison is only counted once teammates spawn at its position **and** the builder gets
the matching spawn credits: only the owner of a spawn point gets them. A garrison nobody spawns on
within ``CONFIRMATION_SECONDS`` is not counted.

One garrison at most is considered between two polls: building another one needs 50 more supplies
and a new spot.
"""

from __future__ import annotations

import math

from rcon.actions_tracking.context import (
    MAX_POLL_INTERVAL,
    SPAWN_POINT_RADIUS,
    Gain,
    PollContext,
    distance,
)
from rcon.actions_tracking.detectors.base import ActionDetector
from rcon.actions_tracking.model import (
    CONFIRMED,
    NONE,
    PENDING,
    REJECTED,
    Decision,
    PlayerSample,
)
from rcon.actions_tracking.rules import ScoreTable

CONFIRMATION_SECONDS = 300.0
# Unexplained points tolerated besides a construction (bandages, sources missing from the table).
RESIDUAL_TOLERANCE = 20
# A spawn confirms a candidate only if it is clearly closer to it than to a spawn point the builder
# already had (his outpost or another garrison), in game centimetres.
CONFIRMATION_MARGIN = 1_500
# Uncredited spawns at a spot before a candidate was made there: a point that credits nobody (HQ).
UNCREDITED_SPAWNS_HQ = 3
# Decisions with a smaller gain and no candidate are not reported (noise).
REPORTED_GAIN = 40
OUTPOST_ROLES = frozenset({"officer", "spotter"})


def min_residual(
    gain: int, fixed: int, squad: int, team: int, fives: int, table: ScoreTable
) -> tuple[int, int, int]:
    """Smallest unexplained part of ``gain - fixed`` with spawn credits and 5-point sources.

    ``squad`` and ``team`` bound the squad / other teammate spawns that can credit the player,
    ``fives`` bounds the other 5-point sources (outpost tick = 2, marking = 1). Returns
    (residual, squad spawns used, team spawns used). At equal residual, the explanation using the
    observed spawns (squad members first) is preferred over the outpost / marking sources, which are
    only possible.
    """
    rest = gain - fixed
    if rest < 0:
        return (gain, 0, 0)
    best = None
    for team_used in range(min(team, rest // table.spawn_team) + 1):
        left = rest - team_used * table.spawn_team
        squad_used = min(left // table.spawn_unit, squad)
        left -= squad_used * table.spawn_unit
        left -= min(left // table.spawn_unit, fives) * table.spawn_unit
        key = (left, -squad_used, -team_used)
        if best is None or key < best[0]:
            best = (key, squad_used, team_used)
    return (best[0][0], best[1], best[2])


class GarrisonDetector(ActionDetector):
    action = "garrison"
    default_roles = ("officer", "armycommander")

    def load(self, state: dict) -> None:
        # Confirmed garrisons per builder: {ts (built), x, y, team}
        self.garrisons: dict[str, list[dict]] = state.get("garrisons", {})
        self.candidates: list[dict] = state.get("candidates", [])

    def to_state(self) -> dict:
        return {"garrisons": self.garrisons, "candidates": self.candidates}

    def results(self) -> dict[str, tuple[int, float | None]]:
        return {
            player_id: (len(built), max(g["ts"] for g in built))
            for player_id, built in self.garrisons.items()
            if built
        }

    # ------------------------------------------------------------------ poll

    def observe(self, ctx: PollContext) -> list[Decision]:
        builders = {
            pid: gain
            for pid, gain in ctx.gains.items()
            if gain.prev["role"] in self.roles
        }
        decisions = []
        normal = {}
        for player_id, gain in builders.items():
            if ctx.global_gap or gain.is_gap:
                # Positions during the gap are unknown: nothing could confirm a garrison built then.
                if gain.points >= self.table.garrison_built:
                    decisions.append(
                        self._decision(
                            gain,
                            ctx.ts,
                            NONE,
                            f"data gap of {gain.interval:.0f}s: a garrison built during it "
                            "cannot be confirmed",
                        )
                    )
            else:
                normal[player_id] = gain

        # Least ambiguous builders first: they claim the spawn credits they need, which settles
        # some ambiguous cases of the others (a spawn credits only one builder).
        ranks = {pid: self._analyse(ctx, gain)["rank"] for pid, gain in normal.items()}
        for player_id in sorted(normal, key=lambda pid: (ranks[pid], pid)):
            gain = normal[player_id]
            decisions += self._decide(ctx, gain, self._analyse(ctx, gain))
        decisions += self._confirm_candidates(ctx)
        decisions += self._expire_candidates(ctx)
        return decisions

    def finalize(self, end_ts: float, names: dict[str, str]) -> list[Decision]:
        return [
            self._reject(
                candidate, end_ts, "match ended before anybody spawned on it", names
            )
            for candidate in list(self.candidates)
        ]

    def _decision(
        self, gain: Gain, ts: float, verdict: str, reason: str, details=None
    ) -> Decision:
        return Decision(
            ts,
            gain.sample.player_id,
            gain.sample.name,
            self.action,
            verdict,
            reason,
            gain.points,
            gain.interval,
            details or {"role": gain.prev["role"]},
        )

    # ------------------------------------------------------------------ analysis

    def _five_point_sources(self, role: str | None, interval: float) -> int:
        """Bound of the 5-point units from outpost ticks (10 = 2 units) and marking (5)."""
        table = self.table
        outpost = (
            math.ceil(interval / table.outpost_passive_period)
            * (table.outpost_passive // table.spawn_unit)
            if role in OUTPOST_ROLES
            else 0
        )
        marks = math.ceil(interval / table.mark_period) * (
            table.mark // table.spawn_unit
        )
        return outpost + marks

    def _analyse(self, ctx: PollContext, gain: Gain) -> dict:
        sample, prev, table = gain.sample, gain.prev, self.table
        ledger = ctx.spawns
        pool = ledger.pool(sample, prev["ts"], ctx.ts)
        own = ledger.of(sample.player_id)
        foreign = ledger.of_teammates(sample.player_id, sample.team, ctx.players)
        located = [s for s in pool if ledger.near(s, own)]
        claimable = [s for s in pool if s in located or not ledger.near(s, foreign)]
        fives = self._five_point_sources(prev["role"], gain.interval)

        def residual(spawns, fixed):
            squad = sum(1 for s in spawns if s["unit_id"] == sample.unit_id)
            return min_residual(
                gain.points, fixed, squad, len(spawns) - squad, fives, table
            )

        def with_garrison(spawns):
            # One garrison at most between two polls, built on someone else's supplies or on the
            # builder's own (+50 more).
            return min(
                residual(spawns, table.garrison_built + supplies)
                for supplies in (0, table.supplies_consumed)
            )

        x, y = (sample.x, sample.y) if sample.alive else (prev["x"], prev["y"])
        result = {
            "pool": pool,
            "located": located,
            "claimable": claimable,
            "h0_all": residual(pool, 0),
            "h0_claimable": residual(claimable, 0),
            "h0_located": residual(located, 0),
            "h1": with_garrison(pool),
            "h1_claimable": with_garrison(claimable),
            "can_build": sample.alive or prev["alive"],
            "position": (x, y),
        }
        ambiguous = result["h0_all"][0] == 0 and result["h1"][0] == 0
        # Processing order: unambiguous builders, then those whose spawns are on their own spawn
        # point, then the others.
        result["rank"] = (
            0 if not ambiguous else 1 if result["h0_located"][0] == 0 else 2
        )
        return result

    def _credit(
        self,
        ctx: PollContext,
        sample: PlayerSample,
        analysis: dict,
        construction: bool,
        learn: bool,
    ) -> None:
        """Claim the spawns of the retained explanation, preferring unclaimed spawn points."""
        key = "h1" if construction else "h0"
        if analysis[f"{key}_claimable"][0] == 0:
            pool, explanation = analysis["claimable"], analysis[f"{key}_claimable"]
        else:
            pool = analysis["pool"]
            explanation = analysis["h1" if construction else "h0_all"]
        _, squad_used, team_used = explanation
        ctx.spawns.credit(sample, pool, squad_used, team_used, ctx.ts, learn)

    def _decide(self, ctx: PollContext, gain: Gain, analysis: dict) -> list[Decision]:
        sample, prev, ts = gain.sample, gain.prev, ctx.ts
        h0, h1 = analysis["h0_all"], analysis["h1"]
        details = {
            "role": prev["role"],
            "alive": [prev["alive"], sample.alive],
            "spawns_available": len(analysis["pool"]),
            "spawns_near_own_points": len(analysis["located"]),
            "spawns_claimable": len(analysis["claimable"]),
            "h0_no_construction": {"unexplained": h0[0], "squad": h0[1], "team": h0[2]},
            "h1_garrison": {"unexplained": h1[0], "squad": h1[1], "team": h1[2]},
        }

        def decision(verdict: str, reason: str) -> list[Decision]:
            return [self._decision(gain, ts, verdict, reason, details)]

        def reported(reason: str) -> list[Decision]:
            return decision(NONE, reason) if gain.points >= REPORTED_GAIN else []

        if not analysis["can_build"]:
            self._credit(ctx, sample, analysis, False, learn=h0[0] == 0)
            return reported("dead during the whole interval: cannot build")
        if h0[0] == 0 and h1[0] > 0:
            self._credit(ctx, sample, analysis, False, learn=True)
            return reported("spawn credits / outpost / marking explain the gain")
        if h0[0] == 0 and analysis["h0_located"][0] == 0:
            # Both fit, but the spawns appeared on this player's known spawn point.
            _, squad_used, team_used = analysis["h0_located"]
            ctx.spawns.credit(
                sample, analysis["located"], squad_used, team_used, ts, learn=True
            )
            return decision(
                NONE, "spawned teammates appeared on this player's known spawn point"
            )
        if h0[0] > 0 and not (
            h1[0] == 0
            or (
                h1[0] <= RESIDUAL_TOLERANCE
                and h0[0] - h1[0] >= self.table.node_blueprint
            )
        ):
            # Unknown source involved (bandage, score not in the table…): a garrison is a
            # candidate only if it explains at least 40 points that nothing else can.
            return reported(f"{h0[0]} unexplained points, no construction pattern")

        x, y = analysis["position"]
        # A candidate. The spawns explaining the rest of the gain are claimed; when a spawn wave
        # could explain the whole gain, they are left to the confirmation.
        if h0[0] > 0:
            self._credit(ctx, sample, analysis, True, learn=True)
        self.candidates.append(
            {
                "player_id": sample.player_id,
                "ts": ts,
                "x": x,
                "y": y,
                "team": sample.team,
                "deadline": ts + CONFIRMATION_SECONDS,
                "gain": gain.points,
            }
        )
        reason = (
            "a garrison explains the gain"
            if h0[0] > 0
            else "a garrison or a spawn wave of unknown origin explain the gain"
        )
        return decision(
            PENDING,
            f"{reason}; counted once teammates spawn on it and credit the builder",
        )

    # ------------------------------------------------------------------ confirmation

    def _confirm_candidates(self, ctx: PollContext) -> list[Decision]:
        """Confirm, for each builder, the candidate a credited spawn wave landed on.

        A builder can have several candidates close to each other (his supplies consumed by someone
        else, then his own garrison): the wave confirms the one closest to it. The others cannot be
        confirmed by the same spawns any more (they now belong to his confirmed garrison).
        """
        decisions = []
        builders = {c["player_id"] for c in self.candidates}
        for player_id in sorted(builders):
            waves = []
            for candidate in self.candidates:
                if candidate["player_id"] == player_id:
                    wave = self._wave_at(ctx, candidate)
                    if wave:
                        spread = sum(
                            distance(s["x"], s["y"], candidate["x"], candidate["y"])
                            for s in wave
                        ) / len(wave)
                        waves.append((spread, candidate["ts"], candidate, wave))
            if not waves:
                continue
            _, _, candidate, wave = min(waves, key=lambda w: (w[0], w[1]))
            for spawn in wave:
                spawn["credited_to"] = player_id
            decisions += self._confirm(ctx, candidate)
        return decisions

    def _wave_at(self, ctx: PollContext, candidate: dict) -> list[dict]:
        """The spawns on the candidate, if they credited the builder; empty otherwise.

        Only the owner of a spawn point gets credits: spawns at the HQ, next to a commander, give
        nothing to anybody. So the builder's gain must be explained by those credits plus his other
        observed spawn credits, exactly, and not without them. Spawns at least as close to a spawn
        point the builder already had (his outpost, his garrisons) do not count.
        """
        player_id = candidate["player_id"]
        gain = ctx.gains.get(player_id)
        builder = ctx.current.get(player_id)
        if (
            gain is None
            or gain.is_gap
            or builder is None
            or builder.team != candidate["team"]
            or ctx.ts <= candidate["ts"]
        ):
            return []
        if self._uncredited_spawn_point(ctx, candidate):
            return []
        # Spawns as close to one of his existing spawn points (outpost, garrisons) belong to it.
        known_before = [
            p for p in ctx.spawns.of(player_id) if p["since"] < candidate["ts"]
        ] + self.garrisons.get(player_id, [])
        pool = [
            s
            for s in ctx.spawns.spawns
            if s["team"] == candidate["team"]
            and s["player_id"] != player_id
            and s["credited_to"] in (None, player_id)
            and gain.prev["ts"] - MAX_POLL_INTERVAL / 4 < s["ts"] <= ctx.ts
        ]

        def on_candidate(spawn: dict) -> bool:
            here = distance(spawn["x"], spawn["y"], candidate["x"], candidate["y"])
            return here <= SPAWN_POINT_RADIUS and all(
                here + CONFIRMATION_MARGIN
                < distance(spawn["x"], spawn["y"], p["x"], p["y"])
                for p in known_before
            )

        wave = [s for s in pool if on_candidate(s)]
        if not wave:
            return []
        # His other credits can only come from his other known spawn points (outpost, garrisons).
        own_points = ctx.spawns.of(player_id)
        others = [s for s in pool if s not in wave and ctx.spawns.near(s, own_points)]
        if self._credits_explain(gain.points, others, [], builder, 0):
            # His other observed credits explain the gain: those teammates spawned at a point that
            # credits nobody (the HQ next to him), not on his garrison.
            return []
        fives = self._five_point_sources(gain.prev["role"], gain.interval)
        if self._credits_explain(gain.points, others, wave, builder, fives):
            return wave
        return []

    def _uncredited_spawn_point(self, ctx: PollContext, candidate: dict) -> bool:
        """Teammates already spawned here, crediting nobody, before the candidate: the HQ."""
        before = [
            s
            for s in ctx.spawns.spawns
            if s["team"] == candidate["team"]
            and s["player_id"] != candidate["player_id"]
            and s["ts"] < candidate["ts"]
            and s["credited_to"] is None
            and distance(s["x"], s["y"], candidate["x"], candidate["y"])
            <= SPAWN_POINT_RADIUS
        ]
        return len(before) >= UNCREDITED_SPAWNS_HQ

    def _credits_explain(
        self,
        points: int,
        others: list[dict],
        wave: list[dict],
        builder: PlayerSample,
        fives: int,
    ) -> bool:
        """Whether ``points`` are exactly the credits of some ``others`` spawns plus, when given,
        at least one ``wave`` spawn and up to ``fives`` 5-point sources (outpost tick, marking).

        Only some of the wave may have credited the builder yet: the credit of a spawn can come with
        the next poll.
        """
        table = self.table

        def split(spawns):
            squad = sum(1 for s in spawns if s["unit_id"] == builder.unit_id)
            return squad, len(spawns) - squad

        other_squad, other_team = split(others)
        if not wave:
            return (
                points > 0
                and min_residual(points, 0, other_squad, other_team, fives, table)[0]
                == 0
            )
        wave_squad, wave_team = split(wave)
        for squad_used in range(wave_squad + 1):
            for team_used in range(wave_team + 1):
                if squad_used + team_used == 0:
                    continue
                rest = (
                    points
                    - squad_used * table.spawn_unit
                    - team_used * table.spawn_team
                )
                if rest < 0:
                    continue
                if (
                    rest == 0
                    or min_residual(rest, 0, other_squad, other_team, fives, table)[0]
                    == 0
                ):
                    return True
        return False

    def _confirm(self, ctx: PollContext, candidate: dict) -> list[Decision]:
        """Count the garrison."""
        self.candidates.remove(candidate)
        player_id = candidate["player_id"]
        x, y = candidate["x"], candidate["y"]
        self.garrisons.setdefault(player_id, []).append(
            {"ts": candidate["ts"], "x": x, "y": y, "team": candidate["team"]}
        )
        ctx.spawns.learn(player_id, x, y, ctx.ts, "garrison")
        return [
            Decision(
                ctx.ts,
                player_id,
                ctx.name(player_id),
                self.action,
                CONFIRMED,
                "teammates spawned on it and credited the builder",
                candidate["gain"],
                details={"built_at": candidate["ts"]},
            )
        ]

    def _expire_candidates(self, ctx: PollContext) -> list[Decision]:
        return [
            self._reject(
                candidate,
                ctx.ts,
                f"nobody spawned on it within {CONFIRMATION_SECONDS:.0f}s",
                ctx.names,
            )
            for candidate in list(self.candidates)
            if ctx.ts >= candidate["deadline"]
        ]

    def _reject(
        self, candidate: dict, ts: float, reason: str, names: dict[str, str]
    ) -> Decision:
        self.candidates.remove(candidate)
        player_id = candidate["player_id"]
        return Decision(
            ts,
            player_id,
            names.get(player_id, player_id),
            self.action,
            REJECTED,
            reason,
            candidate["gain"],
            details={"built_at": candidate["ts"]},
        )
