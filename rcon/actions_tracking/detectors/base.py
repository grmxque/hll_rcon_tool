"""Contract of an action detector."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterable
from typing import ClassVar

from rcon.actions_tracking.context import PollContext
from rcon.actions_tracking.model import Decision
from rcon.actions_tracking.rules import GameRules, ScoreTable


class ActionDetector(ABC):
    """Decides, poll after poll, which players performed one kind of action.

    A detector keeps its own JSON-serialisable state (persisted between polls) and only reads the
    shared ``PollContext``, except the spawns it claims in ``ctx.spawns``.
    """

    action: ClassVar[str]
    # Roles analysed when the settings do not say otherwise.
    default_roles: ClassVar[tuple[str, ...]] = ()

    def __init__(
        self,
        state: dict | None,
        *,
        roles: Iterable[str] | None,
        table: ScoreTable,
        rules: GameRules,
    ):
        self.roles = frozenset(self.default_roles if roles is None else roles)
        self.table = table
        self.rules = rules
        self.load(state or {})

    @abstractmethod
    def load(self, state: dict) -> None: ...

    @abstractmethod
    def to_state(self) -> dict: ...

    @abstractmethod
    def observe(self, ctx: PollContext) -> list[Decision]:
        """Decisions for the interval between the previous poll and ``ctx.ts``."""

    def sector_captured(self, capture: dict, ctx: PollContext) -> list[Decision]:
        """``capture["team"]`` lost ``capture["sector"]`` in this poll: its garrisons and outposts
        there are destroyed (``rcon/actions_tracking/sectors.py``). Called before ``observe``."""
        return []

    def finalize(self, end_ts: float, names: dict[str, str]) -> list[Decision]:
        """Settle what is still pending at the end of the match."""
        return []

    @abstractmethod
    def results(self) -> dict[str, tuple[int, float | None]]:
        """Per player: number of actions, and when that number was reached."""
