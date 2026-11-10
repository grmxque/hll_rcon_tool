"""Garrisons, detected from the support gain of their builder.

Between two polls, the gain of a player whose role can build a garrison is explained in two ways:

- H0, no construction: spawn credits (+5 per squad member, +3 per other teammate spawning on the
  player's spawn point), outpost and marking income, bounded by what the polls observed;
- H1, a garrison: the same plus +50.

A garrison is counted when it is needed to explain the gain, never because the gain equals 50: a
spawn wave can give exactly 50 too. When both fit, the context decides (player dead, where the spawned
teammates appeared, which builder needs those spawns), or later observations do (pending case).
The physical rules of the game (``GameRules``) exclude what cannot happen: one garrison per poll, no
garrison within 200 m of a friendly one.
"""

from __future__ import annotations

import math

from rcon.actions_tracking.context import (
    MAX_POLL_INTERVAL,
    SPAWN_POINT_RADIUS,
    SPAWN_POINT_TTL,
    Gain,
    PollContext,
    SpawnLedger,
    distance,
)
from rcon.actions_tracking.detectors.base import ActionDetector
from rcon.actions_tracking.model import (
    CONFIRMED,
    COUNTED,
    NONE,
    PENDING,
    REJECTED,
    Decision,
    PlayerSample,
)
from rcon.actions_tracking.rules import ScoreTable

STATIONARY_DISTANCE = 2_000  # moved less than 20 m during the poll interval
# Several commander abilities give +50 support, like a garrison: the commander's garrisons only count
# once teammates spawn on them.
EVIDENCE_REQUIRED_ROLES = frozenset({"armycommander"})
EVIDENCE_REQUIRED = "confirmation required (commander abilities give +50 too)"
# Other income tolerated besides the spawn credits that confirm a garrison (outpost, marking).
CONFIRMATION_TOLERANCE = 15
PENDING_SECONDS = 180.0
INCOME_WINDOW_SECONDS = 600.0
MIN_INCOME_OBSERVATION_SECONDS = 120.0
# Unexplained points tolerated besides a construction (bandages, sources missing from the table).
RESIDUAL_TOLERANCE = 20
# Decisions with a smaller gain and no construction are not reported (noise).
REPORTED_GAIN = 40


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
        # Per builder: {ts, x, y, team, standing, last_seen, gap?}
        self.garrisons: dict[str, list[dict]] = state.get("garrisons", {})
        self.pending: list[dict] = state.get("pending", [])
        self.income: dict[str, dict] = state.get("income", {})

    def to_state(self) -> dict:
        return {
            "garrisons": self.garrisons,
            "pending": self.pending,
            "income": self.income,
        }

    def results(self) -> dict[str, tuple[int, float | None]]:
        return {
            player_id: (len(built), max(g["ts"] for g in built))
            for player_id, built in self.garrisons.items()
            if built
        }

    # ------------------------------------------------------------------ poll

    def observe(self, ctx: PollContext) -> list[Decision]:
        self._refresh_standing(ctx)
        for player_id, sample in ctx.current.items():
            if sample.role in self.roles and not ctx.global_gap:
                self.income.setdefault(player_id, {"since": ctx.ts, "events": []})
        candidates = {
            pid: gain
            for pid, gain in ctx.gains.items()
            if gain.prev["role"] in self.roles
        }
        decisions = []
        normal = {}
        for player_id, gain in candidates.items():
            if ctx.global_gap or gain.is_gap:
                decisions.append(self._over_gap(ctx, gain))
            else:
                normal[player_id] = gain

        # Least ambiguous builders first: they claim the spawn credits they need, which settles
        # some ambiguous cases of the others (a spawn credits only one builder).
        ranks = {pid: self._analyse(ctx, gain)["rank"] for pid, gain in normal.items()}
        for player_id in sorted(normal, key=lambda pid: (ranks[pid], pid)):
            gain = normal[player_id]
            before = len(self.garrisons.get(player_id, []))
            decisions += self._decide(ctx, gain, self._analyse(ctx, gain))
            built = len(self.garrisons.get(player_id, [])) - before
            if any(
                p["player_id"] == player_id and p["ts"] == ctx.ts for p in self.pending
            ):
                built = 1  # either a garrison or the same value in spawn credits
            self._record_income(
                player_id, ctx.ts, gain.points - built * self.table.garrison_built
            )
        decisions += self._settle_pending(ctx)
        return decisions

    def finalize(self, end_ts: float, names: dict[str, str]) -> list[Decision]:
        return [
            self._resolve_default(pending, end_ts, "match ended", names)
            for pending in list(self.pending)
        ]

    # ------------------------------------------------------------------ physical rules

    def _refresh_standing(self, ctx: PollContext) -> None:
        """A garrison where teammates spawn is still standing."""
        for spawn in ctx.spawns.at(ctx.ts):
            for garrison in self._standing(spawn["team"], ctx.ts):
                if (
                    distance(spawn["x"], spawn["y"], garrison["x"], garrison["y"])
                    <= SPAWN_POINT_RADIUS
                ):
                    garrison["last_seen"] = ctx.ts

    def _standing(self, team: str | None, ts: float) -> list[dict]:
        """Known friendly garrisons presumed standing (not replaced, used or built recently)."""
        return [
            garrison
            for built in self.garrisons.values()
            for garrison in built
            if garrison.get("standing")
            and garrison.get("team") == team
            and ts - garrison.get("last_seen", garrison["ts"]) <= SPAWN_POINT_TTL
        ]

    def _neighbours(self, sample: PlayerSample, x: float, y: float, ts: float):
        return [
            garrison
            for garrison in self._standing(sample.team, ts)
            if distance(x, y, garrison["x"], garrison["y"])
            < self.rules.garrison_min_spacing
        ]

    # ------------------------------------------------------------------ normal intervals

    def _five_point_sources(self, interval: float) -> int:
        """Bound of the 5-point units from outpost ticks (10 = 2 units) and marking (5)."""
        table = self.table
        outpost = math.ceil(interval / table.outpost_passive_period) * (
            table.outpost_passive // table.spawn_unit
        )
        marks = math.ceil(interval / table.mark_period) * (
            table.mark // table.spawn_unit
        )
        return outpost + marks

    def _analyse(self, ctx: PollContext, gain: Gain) -> dict:
        sample, prev = gain.sample, gain.prev
        ledger = ctx.spawns
        pool = ledger.pool(sample, prev["ts"], ctx.ts)
        own = ledger.of(sample.player_id)
        foreign = ledger.of_teammates(sample.player_id, sample.team, ctx.players)
        located = [s for s in pool if ledger.near(s, own)]
        claimable = [s for s in pool if s in located or not ledger.near(s, foreign)]
        fives = self._five_point_sources(gain.interval)

        def residual(spawns, fixed):
            squad = sum(1 for s in spawns if s["unit_id"] == sample.unit_id)
            return min_residual(
                gain.points, fixed, squad, len(spawns) - squad, fives, self.table
            )

        # One garrison at most between two polls: a second one needs 50 more supplies and a spot
        # 200 m away.
        built = self.table.garrison_built
        x, y = (sample.x, sample.y) if sample.alive else (prev["x"], prev["y"])
        result = {
            "pool": pool,
            "located": located,
            "claimable": claimable,
            "h0_all": residual(pool, 0),
            "h0_claimable": residual(claimable, 0),
            "h0_located": residual(located, 0),
            "h1": residual(pool, built),
            "h1_claimable": residual(claimable, built),
            "can_build": sample.alive or prev["alive"],
            "position": (x, y),
            "neighbours": self._neighbours(sample, x, y, ctx.ts),
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
        neighbours = analysis["neighbours"]
        too_recent = [
            g
            for g in neighbours
            if ts - g["ts"] < self.rules.garrison_rebuild_min_seconds
        ]
        details = {
            "role": prev["role"],
            "alive": [prev["alive"], sample.alive],
            "spawns_available": len(analysis["pool"]),
            "spawns_near_own_points": len(analysis["located"]),
            "spawns_claimable": len(analysis["claimable"]),
            "friendly_garrisons_within_200m": len(neighbours),
            "h0_no_construction": {"unexplained": h0[0], "squad": h0[1], "team": h0[2]},
            "h1_garrison": {"unexplained": h1[0], "squad": h1[1], "team": h1[2]},
        }

        def decision(verdict: str, reason: str) -> list[Decision]:
            return [
                Decision(
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
            ]

        def reported(reason: str) -> list[Decision]:
            return decision(NONE, reason) if gain.points >= REPORTED_GAIN else []

        if not analysis["can_build"]:
            self._credit(ctx, sample, analysis, False, learn=h0[0] == 0)
            return reported("dead during the whole interval: cannot build")
        if too_recent:
            self._credit(ctx, sample, analysis, False, learn=h0[0] == 0)
            if h1[0] == 0 or gain.points >= REPORTED_GAIN:
                newest = max(too_recent, key=lambda g: g["ts"])
                return decision(
                    NONE,
                    "physically impossible: a friendly garrison was built "
                    f"{ts - newest['ts']:.0f}s ago within 200 m",
                )
            return []

        if neighbours and prev["role"] in EVIDENCE_REQUIRED_ROLES:
            # The spawns that would confirm it are those of the garrison already standing there.
            self._credit(ctx, sample, analysis, False, learn=h0[0] == 0)
            return reported(
                "a friendly garrison already stands within 200 m; "
                f"{EVIDENCE_REQUIRED}, impossible here"
            )
        if h1[0] == 0 and h0[0] > 0:
            self._credit(ctx, sample, analysis, True, learn=True)
            reason = "only a construction explains the gain"
            if neighbours:
                reason += (
                    "; it replaces a friendly garrison within 200 m, presumed destroyed"
                )
            return self._add(ctx, gain, analysis, decision, reason)
        if h1[0] > 0 and h0[0] == 0:
            self._credit(ctx, sample, analysis, False, learn=True)
            return reported("spawn credits / outpost / marking explain the gain")
        if h1[0] > 0 and h0[0] > 0:
            # Unknown source involved (bandage, score not in the table…): a construction is
            # retained only if it explains at least 40 points that nothing else can, and where a
            # garrison can stand.
            if (
                not neighbours
                and h1[0] <= RESIDUAL_TOLERANCE
                and h0[0] - h1[0] >= self.table.node_blueprint
            ):
                self._credit(ctx, sample, analysis, True, learn=False)
                return self._add(
                    ctx,
                    gain,
                    analysis,
                    decision,
                    f"{h1[0]} unexplained points with a construction, {h0[0]} without",
                )
            return reported(f"{h0[0]} unexplained points, no construction pattern")

        # Both hypotheses fit exactly: spawns on this player's spawn point give the same gain.
        if analysis["h0_located"][0] == 0:
            _, squad_used, team_used = analysis["h0_located"]
            ctx.spawns.credit(
                sample, analysis["located"], squad_used, team_used, ts, learn=True
            )
            return decision(
                NONE, "spawned teammates appeared on this player's known spawn point"
            )
        if neighbours:
            self._credit(ctx, sample, analysis, False, learn=True)
            return decision(
                NONE,
                "a friendly garrison already stands within 200 m: spawn credits explain the gain",
            )
        if analysis["h0_claimable"][0] > 0:
            self._credit(ctx, sample, analysis, True, learn=False)
            return self._add(
                ctx,
                gain,
                analysis,
                decision,
                "the spawns that could explain the gain came from other builders' spawn points",
            )
        claimable = analysis["claimable"]
        repeated = [
            pending
            for pending in self.pending
            if pending["player_id"] == sample.player_id
            and any(
                ctx.spawns.near(spawn, _points(pending["h0_spawns"]))
                for spawn in claimable
            )
        ]
        if repeated:
            # The same spot credited this player again with a construction-like gain: a garrison
            # is not rebuilt where spawns keep appearing, it is this player's spawn point.
            settled = []
            for pending in repeated:
                for x, y in pending["h0_spawns"]:
                    ctx.spawns.learn(sample.player_id, x, y, ts, "spawn")
                settled.append(
                    self._resolve(
                        pending,
                        ts,
                        NONE,
                        "the same spawn point credited the player again",
                        ctx.names,
                        ctx.spawns,
                    )
                )
            self._credit(ctx, sample, analysis, False, learn=True)
            return settled + decision(
                NONE, "spawns on a point that already credited this player"
            )
        stationary = (
            prev["alive"]
            and sample.alive
            and distance(sample.x, sample.y, prev["x"], prev["y"])
            <= STATIONARY_DISTANCE
        )
        if prev["role"] in EVIDENCE_REQUIRED_ROLES:
            default, default_reason = NONE, EVIDENCE_REQUIRED
        elif stationary:
            default, default_reason = (
                COUNTED,
                "default for an ambiguous gain (player stationary)",
            )
        else:
            default, default_reason = (
                NONE,
                "default for an ambiguous gain (player moving)",
            )
        self._open_pending(ctx, gain, analysis, claimable, default, default_reason)
        return decision(
            PENDING,
            "spawns of unknown origin give the same gain; waiting for later evidence "
            f"({default_reason})",
        )

    def _open_pending(
        self,
        ctx: PollContext,
        gain: Gain,
        analysis: dict,
        h0_spawns: list[dict],
        default: str,
        default_reason: str,
    ) -> None:
        x, y = analysis["position"]
        self.pending.append(
            {
                "player_id": gain.sample.player_id,
                "ts": ctx.ts,
                "x": x,
                "y": y,
                "team": gain.sample.team,
                "deadline": ctx.ts + PENDING_SECONDS,
                "default": default,
                "default_reason": default_reason,
                "h0_spawns": [[s["x"], s["y"]] for s in h0_spawns],
                "gain": gain.points,
            }
        )

    def _add(self, ctx, gain: Gain, analysis: dict, decision, reason: str):
        """Count the garrison, or wait for teammates to spawn on it (commander)."""
        sample = gain.sample
        if gain.prev["role"] in EVIDENCE_REQUIRED_ROLES:
            x, y = analysis["position"]
            if any(
                p["team"] == sample.team
                and distance(x, y, p["x"], p["y"]) < self.rules.garrison_min_spacing
                for p in self.pending
            ):
                # Garrisons are 200 m apart: the gain is not a second one (ability used while
                # standing on a garrison still awaiting confirmation).
                return decision(
                    NONE,
                    "another garrison awaiting confirmation stands within 200 m",
                )
            self._open_pending(ctx, gain, analysis, [], NONE, EVIDENCE_REQUIRED)
            return decision(PENDING, f"{reason}; {EVIDENCE_REQUIRED}")
        x, y = analysis["position"]
        for replaced in analysis["neighbours"]:
            replaced["standing"] = False
        self._store(sample.player_id, sample.team, x, y, ctx.ts, ctx.spawns)
        return decision(COUNTED, reason)

    def _store(
        self,
        player_id: str,
        team: str | None,
        x: float,
        y: float,
        ts: float,
        ledger: SpawnLedger | None,
        gap: bool = False,
    ) -> None:
        self.garrisons.setdefault(player_id, []).append(
            {
                "ts": ts,
                "x": x,
                "y": y,
                "team": team,
                "standing": not gap,  # position unknown after a gap
                "last_seen": ts,
                "gap": gap,
            }
        )
        if ledger is not None and not gap:
            ledger.learn(player_id, x, y, ts, "garrison")

    # ------------------------------------------------------------------ data gaps

    def _over_gap(self, ctx: PollContext, gain: Gain) -> Decision:
        """Data gap: credit the garrisons needed beyond the income the player probably had.

        The other income during the gap is estimated from the player's own non-construction income
        before the gap (spawn credits, outpost, marking), with a 50 % margin, and capped by the
        largest income the observed deaths allow. The worst case alone (every teammate death
        spawning on this player's spawn points) would hide nearly every garrison built during a gap.
        The builder needs at least ``garrison_min_seconds_between`` between two garrisons.
        """
        sample, prev, table = gain.sample, gain.prev, self.table
        squad_bound, team_bound = ctx.spawns.bounds(
            sample, prev, ctx.ts, ctx.current, ctx.players
        )
        fives = self._five_point_sources(gain.interval)
        worst_case = (
            squad_bound + fives
        ) * table.spawn_unit + team_bound * table.spawn_team
        rate = self._income_rate(sample.player_id, prev["ts"])
        if rate is None:
            allowance = worst_case
            basis = "worst case (no income history)"
        else:
            allowance = min(worst_case, 1.5 * rate * gain.interval + RESIDUAL_TOLERANCE)
            basis = f"{rate * 60:.0f} pts/min observed before the gap"
        most = 1 + int(gain.interval // self.rules.garrison_min_seconds_between)
        if prev["role"] in EVIDENCE_REQUIRED_ROLES:
            most = (
                0  # the position of a garrison built during a gap cannot be confirmed
            )
        count = 0
        while (
            count < most
            and gain.points - count * table.garrison_built > allowance
            and gain.points >= (count + 1) * table.garrison_built
        ):
            count += 1
        for _ in range(count):
            self._store(
                sample.player_id,
                sample.team,
                sample.x,
                sample.y,
                ctx.ts,
                None,
                gap=True,
            )
        self._record_income(
            sample.player_id, ctx.ts, gain.points - count * table.garrison_built
        )
        reason = (
            f"data gap of {gain.interval:.0f}s: {count} garrison(s) needed beyond the "
            "probable other income"
            if count
            else f"data gap of {gain.interval:.0f}s: the probable other income explains the gain"
        )
        return Decision(
            ctx.ts,
            sample.player_id,
            sample.name,
            self.action,
            COUNTED if count else NONE,
            reason,
            gain.points,
            gain.interval,
            {
                "role": prev["role"],
                "squad_spawns_bound": squad_bound,
                "team_spawns_bound": team_bound,
                "other_income_allowance": round(allowance),
                "allowance_basis": basis,
                "most_garrisons_possible": most,
                "garrisons": count,
            },
        )

    def _record_income(self, player_id: str, ts: float, points: int) -> None:
        """Non-construction income of a builder, used to estimate it over data gaps."""
        income = self.income.setdefault(player_id, {"since": ts, "events": []})
        if points > 0:
            income["events"].append([ts, points])
        income["events"] = [
            e for e in income["events"] if ts - e[0] <= INCOME_WINDOW_SECONDS
        ]

    def _income_rate(self, player_id: str, until: float) -> float | None:
        income = self.income.get(player_id)
        if income is None:
            return None
        observed = min(INCOME_WINDOW_SECONDS, until - income["since"])
        if observed < MIN_INCOME_OBSERVATION_SECONDS:
            return None
        return (
            sum(points for ts, points in income["events"] if until - ts <= observed)
            / observed
        )

    # ------------------------------------------------------------------ pending cases

    def _settle_pending(self, ctx: PollContext) -> list[Decision]:
        decisions = []
        for pending in list(self.pending):
            own = ctx.spawns.of(pending["player_id"])
            learned_after = [p for p in own if p["since"] > pending["ts"]]
            if self._credited_spawns_at(ctx, pending):
                decisions.append(
                    self._resolve(
                        pending,
                        ctx.ts,
                        COUNTED,
                        "teammates later spawned at the build position and credited the builder",
                        ctx.names,
                        ctx.spawns,
                    )
                )
            elif any(
                distance(x, y, p["x"], p["y"]) <= SPAWN_POINT_RADIUS
                for x, y in pending["h0_spawns"]
                for p in learned_after
            ):
                decisions.append(
                    self._resolve(
                        pending,
                        ctx.ts,
                        NONE,
                        "those spawns later proved to be on this player's spawn point",
                        ctx.names,
                        ctx.spawns,
                    )
                )
            elif ctx.ts >= pending["deadline"]:
                decisions.append(
                    self._resolve_default(
                        pending, ctx.ts, "no later evidence", ctx.names, ctx.spawns
                    )
                )
        return decisions

    def _credited_spawns_at(self, ctx: PollContext, pending: dict) -> bool:
        """Teammates spawned at the build position and the builder's gain is their spawn credits.

        Only the builder of a spawn point gets credits: spawns at the HQ, next to a commander, give
        nothing to anybody, so the gain must match the credits, not merely exceed them.
        """
        player_id = pending["player_id"]
        gain = ctx.gains.get(player_id)
        builder = ctx.current.get(player_id)
        if gain is None or builder is None or ctx.ts <= pending["ts"]:
            return False
        known_before = [
            p for p in ctx.spawns.of(player_id) if p["since"] < pending["ts"]
        ]
        if ctx.spawns.near(pending, known_before):
            return False  # spawns there are those of a spawn point that already existed
        after = gain.prev["ts"] - MAX_POLL_INTERVAL / 4
        nearby = [
            s
            for s in ctx.spawns.spawns
            if s["team"] == pending["team"]
            and s["player_id"] != player_id
            and s["credited_to"] in (None, player_id)
            and after < s["ts"] <= ctx.ts
            and distance(s["x"], s["y"], pending["x"], pending["y"])
            <= SPAWN_POINT_RADIUS
        ]
        credits = sum(
            self.table.spawn_unit
            if s["unit_id"] == builder.unit_id
            else self.table.spawn_team
            for s in nearby
        )
        return (
            bool(nearby) and credits <= gain.points <= credits + CONFIRMATION_TOLERANCE
        )

    def _resolve_default(
        self,
        pending: dict,
        ts: float,
        why: str,
        names: dict[str, str],
        ledger: SpawnLedger | None = None,
    ) -> Decision:
        return self._resolve(
            pending,
            ts,
            pending["default"],
            f"{why}: {pending.get('default_reason', 'default for an ambiguous gain')}",
            names,
            ledger,
        )

    def _resolve(
        self,
        pending: dict,
        ts: float,
        verdict: str,
        reason: str,
        names: dict[str, str],
        ledger: SpawnLedger | None,
    ) -> Decision:
        self.pending.remove(pending)
        player_id = pending["player_id"]
        if verdict == COUNTED:
            self._store(
                player_id,
                pending["team"],
                pending["x"],
                pending["y"],
                pending["ts"],
                ledger,
            )
        return Decision(
            ts,
            player_id,
            names.get(player_id, player_id),
            self.action,
            CONFIRMED if verdict == COUNTED else REJECTED,
            reason,
            pending["gain"],
            details={"built_at": pending["ts"]},
        )


def _points(positions) -> list[dict]:
    return [{"x": x, "y": y} for x, y in positions]
