"""Seeded simulation of a match to measure the detection error rates against a known ground truth.

The simulation applies the score table of ``ScoreTable`` to random but plausible gameplay: deaths and
synchronised respawn waves (HQ, the squad outpost, any garrison of the team), garrisons built by
officers, nodes and repair stations built by engineers, passive income of nodes and outposts,
commander abilities, marking and blowtorch repairs. Polls happen every 2.5 to 4.5 seconds, with an
optional outage. It is a stress test of the decision logic, not a model of the real game: the real
values and timings must still be validated in game (see docs/actions-tracking.md).
"""

import math
import random
from dataclasses import dataclass, field

from rcon.actions_tracking.engine import ActionsEngine
from rcon.actions_tracking.model import (
    PlayerSample,
    PollSnapshot,
)

MAP_SIZE = 2_000.0  # metres
RESPAWN_WAVE = 20  # seconds
CONFIRMATION_WINDOW = 300  # seconds, as CONFIRMATION_SECONDS in the garrison detector


@dataclass
class SimPlayer:
    player_id: str
    team: str
    unit_id: int
    role: str
    x: float
    y: float
    alive: bool = True
    support: float = 0
    deaths: int = 0
    respawn_at: int | None = None
    still_until: int = 0
    nodes: list[int] = field(default_factory=list)  # build second of each node
    last_garrison: int = -1_000
    target: tuple[float, float] | None = None


@dataclass
class Truth:
    garrisons: dict[str, int] = field(default_factory=dict)
    # Garrisons teammates spawned on within the confirmation window, per builder: the others can
    # never be counted (nothing confirms them), by design.
    used_garrisons: dict[str, set[int]] = field(default_factory=dict)
    nodes: dict[str, int] = field(default_factory=dict)


class Simulation:
    def __init__(
        self, seed: int, duration: int = 30 * 60, outage: tuple[int, int] | None = None
    ):
        self.rng = random.Random(seed)
        self.duration = duration
        self.outage = outage
        self.players: dict[str, SimPlayer] = {}
        self.outposts: dict[str, tuple[float, float]] = {}
        self.garrisons: list[tuple[str, str, float, float]] = []  # builder, team, x, y
        self.truth = Truth()
        self.hq = {"allies": (100.0, 100.0), "axis": (1900.0, 1900.0)}
        for team in ("allies", "axis"):
            for unit in range(5):
                for index, role in enumerate(
                    ["officer", "rifleman", "medic", "engineer", "support", "assault"]
                ):
                    player_id = f"{team}-{unit}-{index}"
                    self.players[player_id] = SimPlayer(
                        player_id, team, unit, role, *self._random_pos()
                    )
                self.outposts[f"{team}-{unit}-0"] = self._random_pos()
            self.players[f"{team}-cmd"] = SimPlayer(
                f"{team}-cmd", team, -1, "armycommander", *self.hq[team]
            )

    def _random_pos(self) -> tuple[float, float]:
        return self.rng.uniform(0, MAP_SIZE), self.rng.uniform(0, MAP_SIZE)

    def run(self, engine: ActionsEngine) -> list:
        decisions = []
        next_poll = 3.0
        for second in range(self.duration):
            self._step(second)
            if second >= next_poll:
                in_outage = self.outage and self.outage[0] <= second < self.outage[1]
                if not in_outage:
                    decisions += engine.observe(self._snapshot(second))
                next_poll = second + self.rng.uniform(2.5, 4.5)
        decisions += engine.finalize(float(self.duration))
        return decisions

    def _snapshot(self, second: int) -> PollSnapshot:
        return PollSnapshot(
            ts=float(second),
            players=tuple(
                PlayerSample(
                    player_id=p.player_id,
                    name=p.player_id,
                    team=p.team,
                    unit_id=p.unit_id,
                    role=p.role,
                    support=int(p.support),
                    deaths=p.deaths,
                    x=p.x * 100 if p.alive else 0.0,
                    y=p.y * 100 if p.alive else 0.0,
                    z=100.0 if p.alive else 0.0,
                )
                for p in self.players.values()
            ),
        )

    def _step(self, second: int) -> None:
        rng = self.rng
        for player in self.players.values():
            if not player.alive:
                if player.respawn_at is not None and second >= player.respawn_at:
                    self._respawn(player, second)
                continue
            if player.role != "armycommander" and rng.random() < 1 / 150:
                player.alive = False
                player.deaths += 1
                # Synchronised waves: everybody dead long enough spawns at the next wave.
                player.respawn_at = (
                    second // RESPAWN_WAVE + 1
                ) * RESPAWN_WAVE + RESPAWN_WAVE
                continue
            if second >= player.still_until:
                self._walk(player)
            self._act(player, second)
        # Garrisons get destroyed (about 7 min on average), so others are rebuilt, sometimes in place.
        for garrison in list(self.garrisons):
            if rng.random() < 1 / 400:
                self.garrisons.remove(garrison)
        # Passive income: outposts (to their officer) and nodes (to their builder).
        for officer_id in self.outposts:
            if second % 60 == 17:
                self.players[officer_id].support += 10
        for player in self.players.values():
            for built in player.nodes:
                if second > built and (second - built) % 60 == 0:
                    player.support += 10

    def _walk(self, player: SimPlayer) -> None:
        """Walk at 3 m/s towards a destination, then pick another one."""
        if (
            player.target is None
            or math.hypot(player.target[0] - player.x, player.target[1] - player.y) < 5
        ):
            player.target = self._random_pos()
        dx, dy = player.target[0] - player.x, player.target[1] - player.y
        length = math.hypot(dx, dy)
        player.x += 3 * dx / length
        player.y += 3 * dy / length

    def _act(self, player: SimPlayer, second: int) -> None:
        rng = self.rng
        if player.role == "officer":
            # Game rules: 200 m from any friendly garrison, 50 supplies to bring between two.
            if (
                rng.random() < 1 / 300
                and second - player.last_garrison >= 30
                and self._garrison_spot_free(player)
            ):
                self._build_garrison(player, second)
            elif rng.random() < 1 / 120:
                player.support += 5  # marking
        elif player.role == "engineer":
            # Game rules: one node of each type per player, 9 per team.
            if (
                len(player.nodes) < 3
                and self._team_nodes(player.team) < 9
                and rng.random() < 1 / 500
            ):
                player.still_until = second + 8
                player.support += 90
                self._consume_supplies(player, own=0.2, officer=0.2, support=0.4)
                player.nodes.append(second)
                self.truth.nodes[player.player_id] = len(player.nodes)
            elif rng.random() < 1 / 1500:
                player.support += 90  # repair station: no passive production
            elif rng.random() < 1 / 200:
                player.support += 5  # blowtorch
        elif player.role == "armycommander":
            if rng.random() < 1 / 120:
                player.support += rng.choice([50, 50, 75, 100])  # abilities
            elif rng.random() < 1 / 600 and second - player.last_garrison >= 30:
                # Drives somewhere on the map and builds a garrison there.
                player.x, player.y = self._random_pos()
                if self._garrison_spot_free(player):
                    self._build_garrison(player, second)

    def _build_garrison(self, player: SimPlayer, second: int) -> None:
        player.last_garrison = second
        player.still_until = second + 4
        player.support += 50
        self._consume_supplies(player, own=0.3, officer=0.2, support=0.3)
        self.garrisons.append(
            (player.player_id, player.team, player.x, player.y, second)
        )
        self.truth.garrisons[player.player_id] = (
            self.truth.garrisons.get(player.player_id, 0) + 1
        )
        if len([g for g in self.garrisons if g[1] == player.team]) > 6:
            oldest = next(g for g in self.garrisons if g[1] == player.team)
            self.garrisons.remove(oldest)  # destroyed

    def _consume_supplies(
        self, builder: SimPlayer, own: float, officer: float, support: float
    ) -> None:
        """The 50 supplies consumed credit whoever placed them: the builder's own truck crate,
        another squad leader's crate, a support box, or a source nobody is credited for."""
        draw = self.rng.random()
        team = [p for p in self.players.values() if p.team == builder.team]
        if draw < own:
            owner = builder
        elif draw < own + officer:
            owner = self.rng.choice(
                [p for p in team if p.role == "officer" and p is not builder]
            )
        elif draw < own + officer + support:
            owner = self.rng.choice([p for p in team if p.role == "support"])
        else:
            return
        owner.support += 50

    def _garrison_spot_free(self, player: SimPlayer) -> bool:
        """200 m from the team's garrisons, and away from the HQ (a garrison there is useless)."""
        hq_x, hq_y = self.hq[player.team]
        return math.hypot(player.x - hq_x, player.y - hq_y) >= 100 and all(
            math.hypot(player.x - x, player.y - y) >= 200
            for _, team, x, y, _built in self.garrisons
            if team == player.team
        )

    def _team_nodes(self, team: str) -> int:
        return sum(len(p.nodes) for p in self.players.values() if p.team == team)

    def _respawn(self, player: SimPlayer, second: int) -> None:
        rng = self.rng
        options = [("hq", None, self.hq[player.team])]
        officer_id = f"{player.team}-{player.unit_id}-0"
        if officer_id in self.outposts and officer_id != player.player_id:
            options.append(("outpost", officer_id, self.outposts[officer_id]))
        for builder, team, x, y, built in self.garrisons:
            if team == player.team:
                options.append(("garrison", (builder, built), (x, y)))
        kind, owner, (x, y) = rng.choice(options)
        builder = owner
        if kind == "garrison":
            builder, built = owner
            if second - built <= CONFIRMATION_WINDOW and builder != player.player_id:
                self.truth.used_garrisons.setdefault(builder, set()).add(built)
        player.x, player.y = x + rng.uniform(-10, 10), y + rng.uniform(-10, 10)
        player.alive = True
        player.respawn_at = None
        if builder and builder != player.player_id:
            same_unit = self.players[builder].unit_id == player.unit_id
            self.players[builder].support += 5 if same_unit else 3


def score(engine: ActionsEngine, truth: Truth) -> dict:
    """Garrison counts per builder (true/false positives, misses) and node eligibility."""
    summary = engine.summary()
    tp = fp = fn = fn_used = 0
    builders = set(truth.garrisons) | {
        pid for pid, actions in summary.items() if actions.count("garrison")
    }
    for pid in builders:
        detected = summary[pid].count("garrison") if pid in summary else 0
        real = truth.garrisons.get(pid, 0)
        used = len(truth.used_garrisons.get(pid, ()))
        tp += min(detected, real)
        fp += max(0, detected - real)
        fn += max(0, real - detected)
        fn_used += max(0, used - detected)
    node_detected = {pid for pid, actions in summary.items() if actions.count("node")}
    node_real = set(truth.nodes)
    return {
        "garrison_tp": tp,
        "garrison_fp": fp,
        "garrison_fn": fn,
        # Misses among the garrisons someone spawned on: the others are not countable.
        "garrison_used": sum(len(v) for v in truth.used_garrisons.values()),
        "garrison_fn_used": fn_used,
        "node_tp": len(node_detected & node_real),
        "node_fp": len(node_detected - node_real),
        "node_fn": len(node_real - node_detected),
    }
