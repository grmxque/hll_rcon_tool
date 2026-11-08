from datetime import timedelta
from unittest.mock import Mock

import pytest

from rcon import live_map
from rcon.live_map import (
    build_layer,
    build_live_map,
    build_player,
    build_score,
    sector_owners,
)
from rcon.types import GameEnum


def _player(player_id, name, *, team="allies", unit="able", x=0.0, y=0.0, z=0.0):
    return {
        "player_id": player_id,
        "name": name,
        "clan_tag": "",
        "team": team,
        "faction": "us" if team == "allies" else "ger",
        "unit_name": unit,
        "role": "rifleman",
        "loadout": "standard issue",
        "level": 42,
        "kills": 3,
        "deaths": 1,
        "world_position": {"x": x, "y": y, "z": z},
        "profile": {"sessions": ["not sent to the map"]},
    }


def test_large_layer_image_matches_its_grid():
    layer = build_layer("carentan_warfare", GameEnum.HLL_WW2)

    assert layer is not None
    assert layer["map_id"] == "carentan"
    assert layer["game_mode"] == "warfare"
    # Carentan's large grid is 10 x 201.6 m cells centred on the origin.
    extent = {
        "x_min": -100800.0,
        "y_min": -100800.0,
        "x_max": 100800.0,
        "y_max": 100800.0,
    }
    assert layer["image_extent"] == extent
    assert layer["grid"] == {**extent, "columns": 10, "rows": 10}


def test_strongpoints_are_in_world_coordinates():
    layer = build_layer("carentan_warfare", GameEnum.HLL_WW2)

    assert len(layer["strongpoints"]) == 15
    blactot = next(sp for sp in layer["strongpoints"] if sp["name"] == "Blactot")
    assert blactot["x"] == pytest.approx(-65543.41)
    assert blactot["y"] == pytest.approx(-39731.965)
    assert blactot["radius"] == 5000.0
    image = layer["image_extent"]
    for sp in layer["strongpoints"]:
        assert image["x_min"] < sp["x"] < image["x_max"]
        assert image["y_min"] < sp["y"] < image["y_max"]


def test_skirmish_uses_the_large_image_and_its_own_grid():
    skirmish = build_layer("CAR_S_1944_Day_P_Skirmish", GameEnum.HLL_WW2)
    warfare = build_layer("carentan_warfare", GameEnum.HLL_WW2)

    assert skirmish["image_extent"] == warfare["image_extent"]
    grid = skirmish["grid"]
    assert (grid["columns"], grid["rows"]) == (10, 10)
    # Small grid: 139.264 m cells, shifted by the layer offset.
    assert grid["x_min"] == pytest.approx(-5 * 13926.4 + 150.0)
    assert grid["y_max"] == pytest.approx(5 * 13926.4 - 110.0)


def test_vietnam_rectangular_grid_inside_square_image():
    layer = build_layer("wdeve_warfare_day", GameEnum.HLL_VIETNAM)

    image, grid = layer["image_extent"], layer["grid"]
    assert (grid["columns"], grid["rows"]) == (10, 6)
    assert image["x_max"] - image["x_min"] == image["y_max"] - image["y_min"]
    assert grid["x_min"] >= image["x_min"] and grid["x_max"] <= image["x_max"]
    assert grid["y_min"] >= image["y_min"] and grid["y_max"] <= image["y_max"]


@pytest.mark.parametrize("layer_id", ["unknown", "not a layer"])
def test_unknown_layer_has_no_geometry(layer_id):
    assert build_layer(layer_id, GameEnum.HLL_WW2) is None


def test_player_alive_flag_and_fields():
    alive = build_player(_player("1", "Alice", x=1200.5, y=-30.0, z=150.0))
    dead = build_player(_player("2", "Bob"))

    assert alive["alive"] is True
    assert (alive["x"], alive["y"]) == (1200.5, -30.0)
    assert "profile" not in alive and "world_position" not in alive
    assert dead["alive"] is False


def test_player_without_position_is_not_alive():
    raw = _player("1", "Alice")
    del raw["world_position"]

    assert build_player(raw)["alive"] is False


def test_live_map_flattens_squads_and_commanders():
    team_view = {
        "fail_count": 0,
        "allies": {
            "squads": {
                "able": {"players": [_player("1", "Alice", x=10.0)]},
                "baker": {"players": [_player("2", "Bob", unit="baker", x=20.0)]},
            },
            "commander": _player("3", "Carol", unit="command", x=30.0),
        },
        "axis": {
            "squads": {"able": {"players": [_player("4", "Dave", team="axis")]}},
            "commander": None,
        },
    }

    result = build_live_map(team_view, "carentan_warfare", GameEnum.HLL_WW2)

    assert result["layer"]["map_id"] == "carentan"
    assert sorted(p["name"] for p in result["players"]) == [
        "Alice",
        "Bob",
        "Carol",
        "Dave",
    ]


def test_live_map_with_empty_server():
    result = build_live_map({"fail_count": 0}, "unknown", GameEnum.HLL_WW2)

    assert result == {"layer": None, "score": None, "players": []}


CARENTAN_LAYOUT = ["Blactot", "Ruins", "Town Center", "Customs", "Rail Causeway"]


@pytest.mark.parametrize(
    "allies_first, allied, axis, expected",
    [
        (True, 2, 3, ["allies", "allies", "axis", "axis", "axis"]),
        (False, 2, 3, ["axis", "axis", "axis", "allies", "allies"]),
        (True, 5, 0, ["allies"] * 5),
        (True, 2, 2, ["allies", "allies", None, "axis", "axis"]),
        (True, 4, 4, [None] * 5),  # inconsistent score
    ],
)
def test_sector_owners(allies_first, allied, axis, expected):
    assert sector_owners(5, allies_first, allied, axis) == expected


def test_sectors_follow_map_direction_and_score():
    # Carentan: Allies attack left to right, Utah Beach: right to left.
    carentan = build_layer("carentan_warfare", GameEnum.HLL_WW2, score=(1, 4))
    utah = build_layer("utahbeach_warfare", GameEnum.HLL_WW2, score=(1, 4))

    assert [s["owner"] for s in carentan["sectors"]] == ["allies"] + ["axis"] * 4
    assert [s["owner"] for s in utah["sectors"]] == ["axis"] * 4 + ["allies"]
    first = carentan["sectors"][0]
    assert first["x_min"] < first["x_max"] < carentan["sectors"][1]["x_max"]


def test_sectors_without_score_or_front_line_have_no_owner():
    assert {
        s["owner"] for s in build_layer("carentan_warfare", GameEnum.HLL_WW2)["sectors"]
    } == {None}
    skirmish = build_layer("CAR_S_1944_Day_P_Skirmish", GameEnum.HLL_WW2, score=(1, 0))
    assert {s["owner"] for s in skirmish["sectors"]} == {None}


def test_active_strongpoints_from_game_layout():
    layer = build_layer(
        "carentan_warfare", GameEnum.HLL_WW2, game_layout=CARENTAN_LAYOUT
    )

    assert layer["objectives_known"] is True
    active = [sp["name"] for sp in layer["strongpoints"] if sp["active"]]
    assert active == CARENTAN_LAYOUT
    assert [sp["sector"] for sp in layer["strongpoints"][:4]] == [0, 0, 0, 1]


@pytest.mark.parametrize(
    "game_layout",
    [
        None,
        [],
        ["Blactot", "Ruins"],
        ["Foy", "Ruins", "Town Center", "Customs", "Rail Causeway"],
    ],
)
def test_unknown_or_foreign_game_layout(game_layout):
    layer = build_layer("carentan_warfare", GameEnum.HLL_WW2, game_layout=game_layout)

    assert layer["objectives_known"] is False
    assert {sp["active"] for sp in layer["strongpoints"]} == {None}


def test_build_score():
    gamestate = {
        "allied_score": 2,
        "axis_score": 3,
        "num_allied_players": 48,
        "num_axis_players": 49,
        "time_remaining": timedelta(minutes=42, seconds=5),
    }

    assert build_score(gamestate, "us", "ger") == {
        "allied": 2,
        "axis": 3,
        "allied_faction": "us",
        "axis_faction": "ger",
        "allied_players": 48,
        "axis_players": 49,
        "time_remaining_seconds": 2525,
    }
    assert build_score(None) is None


def test_current_game_layout_only_for_the_current_layer(monkeypatch):
    history = [
        {
            "name": "carentan_warfare",
            "game_layout": {"requested": [], "set": CARENTAN_LAYOUT},
        }
    ]
    monkeypatch.setattr(live_map, "MapsHistory", lambda: history)

    assert live_map.current_game_layout("carentan_warfare") == CARENTAN_LAYOUT
    assert live_map.current_game_layout("foy_warfare") is None
    history[0]["game_layout"]["set"] = []
    assert live_map.current_game_layout("carentan_warfare") is None
    monkeypatch.setattr(live_map, "MapsHistory", list)
    assert live_map.current_game_layout("carentan_warfare") is None


def test_live_map_for_survives_a_gamestate_failure(monkeypatch):
    monkeypatch.setattr(live_map, "current_game_layout", lambda layer_id: None)
    rcon = Mock()
    rcon.get_map.return_value.id = "carentan_warfare"
    rcon.get_map.return_value.map.allies.name = "us"
    rcon.get_map.return_value.map.axis.name = "ger"
    rcon.get_gamestate.side_effect = RuntimeError("RCON down")
    rcon.get_team_view.return_value = {"fail_count": 0}
    rcon.game_profile.game = GameEnum.HLL_WW2

    result = live_map.live_map_for(rcon)

    assert result["score"] is None
    assert result["layer"]["map_id"] == "carentan"
