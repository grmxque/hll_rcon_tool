"""Live map: current layer geometry, score and player positions for "Views > Map".

Everything here is a pure transformation of data CRCON already fetches:

* player positions come from ``Rcon.get_team_view`` (``world_position``, in game
  centimetres), which is cached for ~2 s and polled by the automods anyway, so the
  live map adds no RCON traffic;
* score and remaining time come from ``Rcon.get_gamestate`` (also cached ~2 s);
* map geometry (grid, sectors) comes from ``hllrcon``'s static layer data.
* garrisons and spawn points (HQs, outposts, airheads) come from the actions
  tracking state of the match in Redis (``rcon.actions_tracking``), when it is
  enabled.

Strongpoints are not shown: the game server has no command to read the active
objectives of the match in progress.

World coordinates: ``x`` grows towards the east and ``y`` towards the south, so
they map directly onto image pixels (left→right, top→bottom). Tactical map images
(``/tac-maps/<map_id>.webp``) are square and cover ten cells of the map's large
(Warfare) grid on each axis, centred on that grid.
"""

import logging
from typing import TYPE_CHECKING, Any, Literal, NotRequired, TypedDict

import hllrcon
from hllrcon.data.maps import CardinalDirection

from rcon.actions_tracking.tracker import live_tracking
from rcon.game.registry import game_switch
from rcon.types import GameEnum
from rcon.utils import MapsHistory

if TYPE_CHECKING:
    from rcon.rcon import Rcon
    from rcon.types import MapInfo

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


class LiveMapGarrison(TypedDict):
    """A garrison detected by the actions tracking (``rcon.actions_tracking``)."""

    x: float
    y: float
    team: str | None
    #: "candidate": detected from the builder's score, not confirmed yet (abilities and supplies
    #: consumed give the same points); "confirmed": teammates spawned on it.
    status: Literal["candidate", "confirmed"]
    builder_id: str
    builder_name: str
    #: Epoch seconds.
    built_at: float
    #: Last spawn on it (epoch seconds), confirmed garrisons only.
    last_seen: float | None


class LiveMapSpawnPoint(TypedDict):
    """A spawn point seen from the spawns of the match (``rcon.actions_tracking.spawns``)."""

    x: float
    y: float
    team: str | None
    #: "garrison" here is only seen from the spawns (not counted for its builder): the counted
    #: ones are in ``garrisons``. "airhead" is a possible one.
    kind: Literal["garrison", "outpost", "airhead"]
    #: Player credited for the spawns there (garrisons and outposts).
    owner_id: str | None
    owner_name: str | None
    #: Epoch seconds.
    first_seen: float
    last_seen: float
    spawns: int
    #: Seen from a single spawn, or an airhead (drawn see-through).
    possible: bool


class LiveMap(TypedDict):
    layer: LiveMapLayer | None
    score: LiveMapScore | None
    players: list[LiveMapPlayer]
    garrisons: list[LiveMapGarrison]
    spawn_points: list[LiveMapSpawnPoint]
    #: Start of the match in progress (epoch ms), used to keep only its log lines.
    match_start_ms: int | None


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
    """Warfare/Offensive: 5 sectors in a row, each with 3 capture zones."""
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


def build_layer(
    layer_id: str,
    game: GameEnum,
    *,
    score: tuple[int, int] | None = None,
) -> LiveMapLayer | None:
    """Geometry of ``layer_id``, or ``None`` when hllrcon does not know the layer.

    ``score`` is (allied, axis) and colours the sectors.

    Skirmish layers play on a smaller grid than the tactical image, so the image
    extent is always taken from a large-mode layer of the same map, while the grid
    and sectors shown are those of the current layer.
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

    if _has_front_line(layer) and score is not None:
        owners = sector_owners(len(layer.sectors), _allies_first(layer), *score)
    else:
        owners = [None] * len(layer.sectors)

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
    allied_faction: str | None = None,
    axis_faction: str | None = None,
    match_start: int | None = None,
    garrisons: list[LiveMapGarrison] | None = None,
    spawn_points: list[LiveMapSpawnPoint] | None = None,
) -> LiveMap:
    """Assemble the live map. ``match_start`` is the match start in epoch seconds."""
    score = build_score(gamestate, allied_faction, axis_faction)
    return {
        "layer": build_layer(
            layer_id,
            game,
            score=(score["allied"], score["axis"]) if score else None,
        ),
        "score": score,
        "players": [build_player(p) for p in _team_view_players(team_view)],
        "garrisons": garrisons or [],
        "spawn_points": spawn_points or [],
        "match_start_ms": match_start * 1000 if match_start else None,
    }


def current_match() -> "MapInfo | None":
    """The match in progress: ``MapsHistory()[0]`` while it has no end time.

    The entry is created by the MATCH START hook (``rcon.hooks``) and closed at the
    next match start.
    """
    try:
        current = MapsHistory()[0]
    except IndexError:
        return None
    return current if current.get("end") is None else None


def live_map_for(rcon: "Rcon") -> LiveMap:
    """Live map for the server ``rcon`` is connected to (cached RCON reads only)."""
    layer = rcon.get_map()
    try:
        gamestate = rcon.get_gamestate()
    except Exception:
        logger.exception("Live map: could not read the game state")
        gamestate = None
    match = current_match()
    match_start = match.get("start") if match else None
    try:
        tracking = live_tracking(match_start)
    except Exception:
        logger.exception("Live map: could not read the actions tracking")
        tracking = {"garrisons": [], "spawn_points": []}
    return build_live_map(
        rcon.get_team_view(),
        layer.id,
        rcon.game_profile.game,
        gamestate=gamestate,
        allied_faction=layer.map.allies.name,
        axis_faction=layer.map.axis.name,
        match_start=match_start,
        garrisons=tracking["garrisons"],
        spawn_points=tracking["spawn_points"],
    )
