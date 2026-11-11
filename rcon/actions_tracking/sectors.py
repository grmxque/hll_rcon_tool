"""Sectors of the match and their owners, as derived from the score by the live map
(``rcon/live_map.py::build_layer``): Warfare and Offensive only, ``None`` when unknown or neutral.

A sector is a world rectangle ``{x_min, y_min, x_max, y_max, owner}`` (game centimetres), in hllrcon
order (left to right or top to bottom), so consecutive sectors are neighbours.

Game rule: when a team captures a sector, every garrison and outpost of the other team in it is
destroyed. The capture is the one destruction of a spawn point the polls can date.
"""

from __future__ import annotations

FRONT = "front"
REAR = "rear"


def other_team(team: str | None) -> str | None:
    return {"allies": "axis", "axis": "allies"}.get(team or "")


def sector_at(sectors: list[dict], x: float, y: float) -> int | None:
    """Index of the sector containing (x, y)."""
    for index, sector in enumerate(sectors):
        if (
            sector["x_min"] <= x <= sector["x_max"]
            and sector["y_min"] <= y <= sector["y_max"]
        ):
            return index
    return None


def inside(sector: dict, x: float, y: float) -> bool:
    return sector_at([sector], x, y) == 0


def track_owners(
    known: list[dict], polled: list[dict] | tuple[dict, ...]
) -> tuple[list[dict], list[dict]]:
    """New sector state and the captures since the previous one.

    Each sector of the state keeps its ``last_owner``: the last team known to hold it. An unknown or
    neutral owner (``None``) does not replace it, so a sector going from one team to neutral then to
    the other team is a capture. A new geometry (first poll with sectors, another layer) starts over
    without captures. A capture: ``{"team": team that lost it, "index", "sector"}``.
    """
    polled = list(polled)
    same_layer = len(known) == len(polled) and all(
        all(a[key] == b[key] for key in ("x_min", "y_min", "x_max", "y_max"))
        for a, b in zip(known, polled)
    )
    state, captures = [], []
    for index, sector in enumerate(polled):
        owner = sector.get("owner")
        last = known[index].get("last_owner") if same_layer else owner
        if owner is not None and last is not None and owner != last:
            captures.append({"team": last, "index": index, "sector": dict(sector)})
        state.append(dict(sector) | {"last_owner": owner or last})
    return state, captures


def front_zone(sectors: list[dict], team: str | None, x: float, y: float) -> str | None:
    """Where (x, y) is for ``team``: ``front`` (a sector it does not hold, or one of its sectors next
    to one it does not hold), ``rear`` (behind its front sector), ``None`` (outside the sectors or
    owners unknown)."""
    index = sector_at(sectors, x, y)
    if index is None or all(s.get("owner") is None for s in sectors):
        return None
    if sectors[index].get("owner") != team:
        return FRONT
    neighbours = [sectors[i] for i in (index - 1, index + 1) if 0 <= i < len(sectors)]
    if any(s.get("owner") != team for s in neighbours):
        return FRONT
    return REAR
