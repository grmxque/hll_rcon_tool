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
    # Largest support gain one player can plausibly get per second (big spawn wave on a garrison
    # plus a construction). Above that, the counter is not trusted (stats leaking from the previous
    # match).
    max_gain_per_second: float = 60.0
    min_implausible_gain: int = 400


@dataclass(frozen=True)
class GameRules:
    """Physical constraints: what cannot happen, whatever the score says."""

    # A garrison cannot be built within 200 m of a friendly garrison (game centimetres).
    garrison_min_spacing: float = 20_000.0
    # Replacing a destroyed garrison at the same place (destroyed, 50 supplies brought, rebuilt)
    # takes at least this long after it was built.
    garrison_rebuild_min_seconds: float = 30.0
    # Two garrisons of the same builder: walk 200 m away and get 50 supplies again.
    garrison_min_seconds_between: float = 30.0
    # One node of each type (munitions, manpower, fuel) per player, 9 per team. The limit of 3 nodes
    # of the same type per team cannot be checked: the node type is not observable.
    max_nodes_per_player: int = 3
    max_nodes_per_team: int = 9


DEFAULT_SCORE_TABLE = ScoreTable()
DEFAULT_GAME_RULES = GameRules()
