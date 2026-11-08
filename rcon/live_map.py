"""Live map: current layer geometry, score and player positions for "Views > Map".

Everything here is a pure transformation of data CRCON already fetches:

* player positions come from ``Rcon.get_team_view`` (``world_position``, in game
  centimetres), which is cached for ~2 s and polled by the automods anyway, so the
  live map adds no RCON traffic;
* score and remaining time come from ``Rcon.get_gamestate`` (also cached ~2 s);
* map geometry (grid, sectors, strongpoints) comes from ``hllrcon``'s static layer data;
* the active strongpoint of each sector comes from the game layout CRCON recorded
  for the current match (``MapsHistory()[0]["game_layout"]``). The game server has
  no command to read the current objectives, so they are only known when the layout
  was set through CRCON (Maps → Objectives) before the match started.

World coordinates: ``x`` grows towards the east and ``y`` towards the south, so
they map directly onto image pixels (left→right, top→bottom). Tactical map images
(``/tac-maps/<map_id>.webp``) are square and cover ten cells of the map's large
(Warfare) grid on each axis, centred on that grid.
"""

import logging
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any, Literal, NotRequired, TypedDict

import hllrcon
from hllrcon.data.maps import CardinalDirection

from rcon.game.registry import game_switch
from rcon.types import GameEnum
from rcon.utils import MapsHistory

if TYPE_CHECKING:
    from rcon.rcon import Rcon

logger = logging.getLogger(__name__)

#: Number of grid cells covered by a tactical map image on each axis.
TAC_MAP_CELLS = 10

Owner = Literal["allies", "axis"]


class Extent(TypedDict):
    """Axis-aligned rectangle in world coordinates (centimetres)."""

    x_min: float
    y_min: float
    x_max: float
    y_max: float


class GridGeometry(Extent):
    columns: int
    rows: int


class StrongpointGeometry(TypedDict):
    name: str
    x: float
    y: float
    radius: float
    sector: int
    #: ``None`` when the current match layout is unknown.
    active: bool | None


class SectorGeometry(Extent):
    #: ``None`` when ownership cannot be derived from the score (game mode
    #: without a 5-sector front line, unknown score, or sector being contested).
    owner: Owner | None


class LiveMapLayer(TypedDict):
    id: str
    map_id: str
    game_mode: str
    image_extent: Extent
    grid: GridGeometry
    sectors: list[SectorGeometry]
    strongpoints: list[StrongpointGeometry]
    objectives_known: bool


class LiveMapScore(TypedDict):
    allied: int
    axis: int
    allied_faction: str | None
    axis_faction: str | None
    allied_players: int
    axis_players: int
    time_remaining_seconds: int


class LiveMapPlayer(TypedDict):
    player_id: str
    name: str
    team: str | None
    faction: str | None
    unit_name: str | None
    role: str | None
    loadout: str | None
    level: int
    kills: int
    deaths: int
    x: float
    y: float
    alive: bool
    clan_tag: NotRequired[str]


class LiveMap(TypedDict):
    layer: LiveMapLayer | None
    score: LiveMapScore | None
    players: list[LiveMapPlayer]


def _grid_extent(grid: hllrcon.data.layers.Grid) -> GridGeometry:
    (col_from, row_from), (col_to, row_to) = grid.size
    x_min, y_min = grid.grid_to_world_from((col_from, row_from))
    x_max, y_max = grid.grid_to_world_to((col_to, row_to))
    return {
        "x_min": x_min,
        "y_min": y_min,
        "x_max": x_max,
        "y_max": y_max,
        "columns": col_to - col_from + 1,
        "rows": row_to - row_from + 1,
    }


def _image_extent(large_grid: hllrcon.data.layers.Grid) -> Extent:
    """World rectangle covered by the square tactical map image of a map."""
    grid = _grid_extent(large_grid)
    center_x = (grid["x_min"] + grid["x_max"]) / 2
    center_y = (grid["y_min"] + grid["y_max"]) / 2
    half_side = TAC_MAP_CELLS * large_grid.scale / 2
    return {
        "x_min": center_x - half_side,
        "y_min": center_y - half_side,
        "x_max": center_x + half_side,
        "y_max": center_y + half_side,
    }


def _layer_classes(game: GameEnum):
    return game_switch(game, hllrcon.HLLLayer, hllrcon.HLLVLayer)


def _has_front_line(layer) -> bool:
    """Warfare/Offensive: 5 sectors in a row, each with 3 strongpoints."""
    return len(layer.sectors) == 5 and all(
        len(sector.capture_zones) == 3 for sector in layer.sectors
    )


def sector_owners(
    sector_count: int, allies_first: bool, allied_score: int, axis_score: int
) -> list[Owner | None]:
    """Owner of each sector, in hllrcon order (left→right or top→bottom).

    In Warfare and Offensive the score is the number of sectors held, and each
    team holds the sectors closest to its spawn: the first ``allied_score``
    sectors from the Allied side, the first ``axis_score`` from the Axis side.
    Sectors held by neither (score below the sector count) are ``None``.
    """
    if allied_score < 0 or axis_score < 0 or allied_score + axis_score > sector_count:
        return [None] * sector_count
    owners: list[Owner | None] = (
        ["allies"] * allied_score
        + [None] * (sector_count - allied_score - axis_score)
        + ["axis"] * axis_score
    )
    return owners if allies_first else owners[::-1]


def _allies_first(layer) -> bool:
    """Whether the Allies spawn on the side of the first sector (left or top)."""
    return layer.map.allies_direction in (
        CardinalDirection.LEFT_TO_RIGHT,
        CardinalDirection.TOP_TO_BOTTOM,
    )


def _active_objectives(layer, game_layout: Sequence[Any] | None) -> set[str] | None:
    """Names of the active strongpoints, if ``game_layout`` matches this layer."""
    if not game_layout or len(game_layout) != len(layer.sectors):
        return None
    names = {
        capture_zone.strongpoint.name
        for sector in layer.sectors
        for capture_zone in sector.capture_zones
    }
    selected = {str(name) for name in game_layout}
    if not selected <= names:
        return None
    return selected


def build_layer(
    layer_id: str,
    game: GameEnum,
    *,
    score: tuple[int, int] | None = None,
    game_layout: Sequence[Any] | None = None,
) -> LiveMapLayer | None:
    """Geometry of ``layer_id``, or ``None`` when hllrcon does not know the layer.

    ``score`` is (allied, axis) and colours the sectors; ``game_layout`` is the list of
    the 5 strongpoint names CRCON set for the current match (``GameLayout["set"]``).

    Skirmish layers play on a smaller grid than the tactical image, so the image
    extent is always taken from a large-mode layer of the same map, while the grid
    and strongpoints shown are those of the current layer.
    """
    layer_cls = _layer_classes(game)
    try:
        layer = layer_cls.by_id(layer_id)
    except ValueError:
        return None

    large_grid = next(
        (
            candidate.grid
            for candidate in layer_cls.all()
            if candidate.map.id.casefold() == layer.map.id.casefold()
            and candidate.game_mode.is_large()
        ),
        layer.grid,
    )

    front_line = _has_front_line(layer)
    if front_line and score is not None:
        owners = sector_owners(len(layer.sectors), _allies_first(layer), *score)
    else:
        owners = [None] * len(layer.sectors)
    active = _active_objectives(layer, game_layout) if front_line else None

    sectors: list[SectorGeometry] = []
    for sector, owner in zip(layer.sectors, owners):
        (x_min, y_min), (x_max, y_max) = sector.area
        sectors.append(
            {
                "x_min": x_min,
                "y_min": y_min,
                "x_max": x_max,
                "y_max": y_max,
                "owner": owner,
            }
        )

    return {
        "id": layer.id,
        "map_id": layer.map.id.lower(),
        "game_mode": layer.game_mode.id,
        "image_extent": _image_extent(large_grid),
        "grid": _grid_extent(layer.grid),
        "sectors": sectors,
        "strongpoints": [
            {
                "name": capture_zone.strongpoint.name,
                "x": capture_zone.strongpoint.center[0],
                "y": capture_zone.strongpoint.center[1],
                "radius": capture_zone.strongpoint.radius,
                "sector": index,
                "active": None
                if active is None
                else capture_zone.strongpoint.name in active,
            }
            for index, sector in enumerate(layer.sectors)
            for capture_zone in sector.capture_zones
        ],
        "objectives_known": active is not None,
    }


def build_score(
    gamestate: dict[str, Any] | None,
    allied_faction: str | None = None,
    axis_faction: str | None = None,
) -> LiveMapScore | None:
    """Score banner data from a ``get_gamestate`` result."""
    if not gamestate:
        return None
    time_remaining = gamestate.get("time_remaining")
    return {
        "allied": int(gamestate.get("allied_score", 0)),
        "axis": int(gamestate.get("axis_score", 0)),
        "allied_faction": allied_faction,
        "axis_faction": axis_faction,
        "allied_players": int(gamestate.get("num_allied_players", 0)),
        "axis_players": int(gamestate.get("num_axis_players", 0)),
        "time_remaining_seconds": int(time_remaining.total_seconds())
        if time_remaining is not None
        else 0,
    }


def _team_view_players(team_view: dict[str, Any]) -> list[dict[str, Any]]:
    """All players of a ``get_team_view`` result, commanders included."""
    players: list[dict[str, Any]] = []
    for team in team_view.values():
        if not isinstance(team, dict):
            continue  # e.g. "fail_count"
        for squad in team.get("squads", {}).values():
            players.extend(squad.get("players", []))
        if team.get("commander"):
            players.append(team["commander"])
    return players


def build_player(player: dict[str, Any]) -> LiveMapPlayer:
    """Subset of a team view player needed by the map.

    The game reports a position of exactly (0, 0, 0) for players who are dead or
    on the deployment screen; they are flagged ``alive=False`` so the map can hide
    them instead of stacking them on the map centre.
    """
    position = player.get("world_position") or {}
    x = float(position.get("x", 0.0))
    y = float(position.get("y", 0.0))
    z = float(position.get("z", 0.0))
    return {
        "player_id": player["player_id"],
        "name": player["name"],
        "clan_tag": player.get("clan_tag") or "",
        "team": player.get("team"),
        "faction": player.get("faction"),
        "unit_name": player.get("unit_name"),
        "role": player.get("role"),
        "loadout": player.get("loadout"),
        "level": player.get("level", 0),
        "kills": player.get("kills", 0),
        "deaths": player.get("deaths", 0),
        "x": x,
        "y": y,
        "alive": (x, y, z) != (0.0, 0.0, 0.0),
    }


def build_live_map(
    team_view: dict[str, Any],
    layer_id: str,
    game: GameEnum,
    *,
    gamestate: dict[str, Any] | None = None,
    game_layout: Sequence[Any] | None = None,
    allied_faction: str | None = None,
    axis_faction: str | None = None,
) -> LiveMap:
    score = build_score(gamestate, allied_faction, axis_faction)
    return {
        "layer": build_layer(
            layer_id,
            game,
            score=(score["allied"], score["axis"]) if score else None,
            game_layout=game_layout,
        ),
        "score": score,
        "players": [build_player(p) for p in _team_view_players(team_view)],
    }


def current_game_layout(layer_id: str) -> list[str | int] | None:
    """Objectives CRCON set for the match in progress, if it was set through CRCON.

    ``MapsHistory()[0]`` is the current match; its layout is recorded at match start
    from the last ``set_game_layout`` call (see ``rcon.hooks``).
    """
    try:
        current = MapsHistory()[0]
    except IndexError:
        return None
    if str(current.get("name", "")).casefold() != layer_id.casefold():
        return None
    return list(current.get("game_layout", {}).get("set") or []) or None


def live_map_for(rcon: "Rcon") -> LiveMap:
    """Live map for the server ``rcon`` is connected to (cached RCON reads only)."""
    layer = rcon.get_map()
    try:
        gamestate = rcon.get_gamestate()
    except Exception:
        logger.exception("Live map: could not read the game state")
        gamestate = None
    return build_live_map(
        rcon.get_team_view(),
        layer.id,
        rcon.game_profile.game,
        gamestate=gamestate,
        game_layout=current_game_layout(layer.id),
        allied_faction=layer.map.allies.name,
        axis_faction=layer.map.axis.name,
    )
