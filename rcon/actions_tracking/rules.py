"""What the game gives for each action, and what it makes physically impossible.

Both come from the community score table and players' knowledge; they must be validated in game
(docs/actions-tracking.md, "Validation before enabling the rewards").
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class ScoreTable:
    """Support points given to the player, from the community score table.

    https://docs.google.com/spreadsheets/d/1eN4f7J_4jXreYCdaU082kOdaYTFAsPp7tZvMY7vgKrM
    """

    garrison_built: int = 50
    node_built: int = 50
    node_blueprint: int = 40
    node_passive: int = 10
    node_passive_period: float = 60.0
    spawn_unit: int = 5  # a squad member spawned on the player's spawn point
    spawn_team: int = 3  # another teammate spawned on the player's garrison
    outpost_passive: int = 10
    outpost_passive_period: float = 60.0
    mark: int = 5
    mark_period: float = 20.0
    repair: int = 5  # blowtorch, every 5 s
    repair_period: float = 5.0
    # +1 per supply consumed, to whoever placed the supplies (truck crate, support box, drop): a
    # garrison or a node consumes 50, at the moment it is built. Observed in game: an officer
    # building a garrison on his own truck crate gets +100.
    supplies_consumed: int = 50
    # Largest support gain one player can plausibly get per second (big spawn wave on a garrison
    # plus a construction). Above that, the counter is not trusted (stats leaking from the previous
    # match).
    max_gain_per_second: float = 60.0
    min_implausible_gain: int = 400


@dataclass(frozen=True)
class GameRules:
    """Physical constraints: what cannot happen, whatever the score says."""

    # One node of each type (munitions, manpower, fuel) per player, 9 per team. The limit of 3 nodes
    # of the same type per team cannot be checked: the node type is not observable.
    max_nodes_per_player: int = 3
    max_nodes_per_team: int = 9


DEFAULT_SCORE_TABLE = ScoreTable()
DEFAULT_GAME_RULES = GameRules()
