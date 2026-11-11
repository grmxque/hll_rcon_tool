"""Small DSL to write action detection scenarios poll by poll.

Positions are in metres here and converted to game centimetres. A dead player (or one on the
deployment screen) is at (0, 0, 0), like in the RCON player info.
"""

from dataclasses import dataclass

from rcon.actions_tracking.engine import ActionsEngine
from rcon.actions_tracking.model import PlayerSample, PollSnapshot

HQ = (50.0, 50.0)


@dataclass
class P:
    player_id: str
    team: str = "allies"
    unit_id: int = 0
    role: str = "rifleman"
    support: int = 0
    deaths: int = 0
    pos: tuple[float, float] | None = (500.0, 500.0)
    # In the menus: the server reports a fixed position. Others move a little at every poll, as
    # players in the game do (the tracking only takes a player as deployed once he moved).
    still: bool = False

    def sample(self) -> PlayerSample:
        x, y = self.pos if self.pos else (0.0, 0.0)
        return PlayerSample(
            player_id=self.player_id,
            name=self.player_id.title(),
            team=self.team,
            unit_id=self.unit_id,
            role=self.role,
            support=self.support,
            deaths=self.deaths,
            x=x * 100,
            y=y * 100,
            z=0.0 if self.pos is None else 100.0,
        )


class Match:
    def __init__(self, start: float = 1_000.0, interval: float = 3.0, **engine_kwargs):
        self.ts = start
        self.interval = interval
        self.players: dict[str, P] = {}
        self.engine = ActionsEngine(**engine_kwargs)
        self.decisions = []
        self.snapshots: list[PollSnapshot] = []

    def add(self, player_id: str, **values) -> P:
        player = P(player_id, **values)
        self.players[player_id] = player
        return player

    def remove(self, player_id: str) -> None:
        del self.players[player_id]

    def gain(self, player_id: str, points: int) -> None:
        self.players[player_id].support += points

    def die(self, *player_ids: str) -> None:
        for player_id in player_ids:
            player = self.players[player_id]
            player.deaths += 1
            player.pos = None

    def spawn(self, player_ids, at: tuple[float, float]) -> None:
        for index, player_id in enumerate(player_ids):
            # Spawned players spread a few metres around the spawn point.
            self.players[player_id].pos = (
                at[0] + index % 5 * 2,
                at[1] + index // 5 * 2,
            )

    def move(self, player_id: str, to: tuple[float, float]) -> None:
        self.players[player_id].pos = to

    def poll(self, after: float | None = None):
        self.ts += self.interval if after is None else after
        for player in self.players.values():
            if player.pos is not None and not player.still:
                player.pos = (player.pos[0] + 0.001, player.pos[1])
        snapshot = PollSnapshot(
            ts=self.ts, players=tuple(p.sample() for p in self.players.values())
        )
        self.snapshots.append(snapshot)
        decisions = self.engine.observe(snapshot)
        self.decisions += decisions
        return decisions

    def idle(self, seconds: float) -> None:
        """Polls during ``seconds`` with no change."""
        for _ in range(int(seconds // self.interval)):
            self.poll()

    def finalize(self):
        decisions = self.engine.finalize(self.ts)
        self.decisions += decisions
        return decisions

    def count(self, player_id: str, action: str) -> int:
        actions = self.engine.summary().get(player_id)
        return actions.count(action) if actions else 0

    def garrisons(self, player_id: str) -> int:
        return self.count(player_id, "garrison")

    def nodes(self, player_id: str) -> int:
        return self.count(player_id, "node")

    def squad(self, prefix: str, count: int, **values) -> list[str]:
        ids = [f"{prefix}{i}" for i in range(count)]
        for player_id in ids:
            self.add(player_id, **values)
        return ids
