"""Sector captures and front line (rcon/actions_tracking/sectors.py) in the actions tracking."""

from unittest.mock import Mock

import pytest

from rcon.actions_tracking import tracker
from rcon.actions_tracking.sectors import (
    FRONT,
    REAR,
    front_zone,
    sector_at,
    track_owners,
)
from rcon.actions_tracking.spawns import SETTLE_SECONDS
from rcon.types import GameEnum
from tests.actions_scenarios import SECTOR_WIDTH, Match
from tests.test_actions_detection import build, confirm, wave


def band(index, owner):
    width = SECTOR_WIDTH * 100
    return {
        "x_min": index * width,
        "y_min": 0.0,
        "x_max": (index + 1) * width,
        "y_max": 200_000.0,
        "owner": owner,
    }


def bands(*owners):
    return [band(index, owner) for index, owner in enumerate(owners)]


def x_in(index):
    """A position in the middle of sector ``index``, in game centimetres."""
    return (index + 0.5) * SECTOR_WIDTH * 100


# ------------------------------------------------------------------ pure functions


def test_sector_at():
    sectors = bands("allies", "allies", "axis")
    assert sector_at(sectors, x_in(1), 50_000) == 1
    assert sector_at(sectors, x_in(7), 50_000) is None
    assert sector_at(sectors, x_in(0), 300_000) is None


def test_no_capture_on_the_first_poll_with_sectors():
    state, captures = track_owners([], bands("allies", "axis"))
    assert captures == []
    assert [s["last_owner"] for s in state] == ["allies", "axis"]


def test_capture_when_the_owner_changes_team():
    state, _ = track_owners([], bands("allies", "allies", "axis"))
    state, captures = track_owners(state, bands("allies", "axis", "axis"))
    assert [(c["team"], c["index"]) for c in captures] == [("allies", 1)]
    _, captures = track_owners(state, bands("allies", "axis", "axis"))
    assert captures == []


def test_capture_through_a_neutral_sector_and_unknown_owners():
    state, _ = track_owners([], bands("allies", "allies", "axis"))
    state, captures = track_owners(state, bands("allies", None, "axis"))
    assert captures == []
    state, captures = track_owners(state, bands(None, None, None))  # score unknown
    assert captures == []
    _, captures = track_owners(state, bands("allies", "axis", "axis"))
    assert [(c["team"], c["index"]) for c in captures] == [("allies", 1)]


def test_another_layer_starts_over():
    state, _ = track_owners([], bands("allies", "axis"))
    other = [s | {"y_max": 1.0} for s in bands("axis", "allies")]
    _, captures = track_owners(state, other)
    assert captures == []


@pytest.mark.parametrize(
    "owners, team, index, zone",
    [
        (("allies", "allies", "allies", "axis", "axis"), "allies", 0, REAR),
        (("allies", "allies", "allies", "axis", "axis"), "allies", 1, REAR),
        (("allies", "allies", "allies", "axis", "axis"), "allies", 2, FRONT),
        (("allies", "allies", "allies", "axis", "axis"), "allies", 3, FRONT),
        (("allies", "allies", "allies", "axis", "axis"), "axis", 4, REAR),
        (("allies", "allies", None, "axis", "axis"), "allies", 1, FRONT),
        (("allies", "allies", None, "axis", "axis"), "axis", 2, FRONT),
        ((None, None, None, None, None), "allies", 1, None),
    ],
)
def test_front_zone(owners, team, index, zone):
    assert front_zone(bands(*owners), team, x_in(index), 50_000) == zone


def test_front_zone_outside_the_sectors():
    assert front_zone(bands("allies", "axis"), "allies", x_in(5), 50_000) is None


# ------------------------------------------------------------------ engine

SECTOR_2 = (1000.0, 900.0)  # metres, in the middle sector


@pytest.fixture
def match():
    m = Match()
    m.add("off", role="officer", unit_id=0, pos=(900.0, 900.0))
    m.squad("a", 10, unit_id=0)
    m.add("off2", role="officer", unit_id=1, pos=(300.0, 300.0))
    m.squad("b", 15, unit_id=1)
    m.sectors("allies", "allies", "allies", "axis", "axis")
    m.poll()
    m.poll()
    return m


def lose_sector_2(m):
    m.sectors("allies", "allies", "axis", "axis", "axis")
    return m.poll()


def test_candidate_in_a_captured_sector_is_rejected(match):
    build(match, "off", SECTOR_2)
    decisions = lose_sector_2(match)
    [rejected] = [d for d in decisions if d.player_id == "off"]
    assert rejected.verdict == "rejected"
    assert "sector captured" in rejected.reason
    confirm(match, "off", SECTOR_2)  # too late: destroyed
    assert match.garrisons("off") == 0
    assert (
        match.engine.captures[0] | {"team": "allies", "index": 2}
        == match.engine.captures[0]
    )


def test_confirmed_garrison_in_a_captured_sector_stays_counted(match):
    build(match, "off", SECTOR_2)
    confirm(match, "off", SECTOR_2)
    assert match.garrisons("off") == 1
    lose_sector_2(match)
    assert match.garrisons("off") == 1
    engine = match.engine
    detector = engine.detectors["garrison"]
    assert detector.live(engine.spawns, engine.names, engine.last_ts) == []
    assert all(
        sector_at([band(2, None)], p["x"], p["y"]) is None
        for p in engine.spawns.of("off")
    )


def test_garrisons_elsewhere_are_intact(match):
    garrison = (300.0, 900.0)  # sector 0
    build(match, "off2", garrison)
    confirm(match, "off2", garrison, team=("a0", "a1"))
    lose_sector_2(match)
    engine = match.engine
    live = engine.detectors["garrison"].live(
        engine.spawns, engine.names, engine.last_ts
    )
    assert [g["builder_id"] for g in live] == ["off2"]


def test_outpost_in_a_captured_sector_is_destroyed(match):
    outpost = (1050.0, 950.0)
    match.idle(200)
    wave(match, outpost, squad=("a0", "a1"), credited="off")
    wave(match, outpost, squad=("a2", "a3"), credited="off")
    match.idle(SETTLE_SECONDS + 3)
    point = match.engine.points.near("allies", outpost[0] * 100, outpost[1] * 100)
    assert point["kind"] == "outpost"
    # Spawns there just before the capture do not bring it back once they settle.
    wave(match, outpost, squad=("a4",), credited="off")
    lose_sector_2(match)
    match.idle(SETTLE_SECONDS + 3)
    assert (
        match.engine.points.near("allies", outpost[0] * 100, outpost[1] * 100) is None
    )
    # Spawns after the capture make a new point.
    wave(match, outpost, squad=("a5", "a6"), team=("b0",))
    match.idle(SETTLE_SECONDS + 3)
    assert match.engine.points.near("allies", outpost[0] * 100, outpost[1] * 100)


# ------------------------------------------------------------------ tracker


def rcon_with_score(allied, axis):
    rcon = Mock()
    rcon.get_map.return_value.id = "carentan_warfare"
    rcon.get_gamestate.return_value = {"allied_score": allied, "axis_score": axis}
    rcon.game_profile.game = GameEnum.HLL_WW2
    return rcon


def test_sectors_of_the_running_layer():
    sectors = tracker.sectors_of(rcon_with_score(3, 2))
    assert len(sectors) == 5
    assert set(sectors[0]) == {"x_min", "y_min", "x_max", "y_max", "owner"}
    assert sorted(s["owner"] for s in sectors) == ["allies"] * 3 + ["axis"] * 2


def test_sectors_unknown_when_the_game_state_fails():
    rcon = rcon_with_score(3, 2)
    rcon.get_gamestate.side_effect = ConnectionError("rcon down")
    assert tracker.sectors_of(rcon) == ()


def test_sectors_unknown_without_a_front_line():
    rcon = rcon_with_score(1, 1)
    rcon.get_map.return_value.id = "unknown_layer"
    assert tracker.sectors_of(rcon) == ()


def test_live_map_hides_the_garrisons_nobody_uses_on_the_front(match, monkeypatch):
    from tests.test_actions_tracking import settings

    front, rear = SECTOR_2, (300.0, 900.0)  # sector 2 is next to the axis sectors
    build(match, "off", front)
    confirm(match, "off", front)
    build(match, "off2", rear)
    confirm(match, "off2", rear, team=("a0", "a1"))
    monkeypatch.setattr(tracker, "load_config", lambda: settings())
    monkeypatch.setattr(tracker, "get_redis_client", Mock)
    monkeypatch.setattr(
        tracker, "_load_state", lambda red, start: match.engine.to_state()
    )

    def drawn():
        return sorted(g["builder_id"] for g in tracker.live_tracking(1)["garrisons"])

    assert drawn() == ["off", "off2"]
    match.idle(tracker.FRONT_STALE_SECONDS + 10)  # nobody spawns on them
    assert drawn() == ["off2"]


def test_replay_shows_the_captures(match, tmp_path, capsys):
    import json

    from rcon.actions_tracking import __main__ as cli

    lose_sector_2(match)
    path = tmp_path / "polls.jsonl"
    path.write_text(
        "\n".join(json.dumps(s.to_dict()) for s in match.snapshots), encoding="utf-8"
    )
    assert cli.main(["replay", str(path), "--spawns"]) == 0
    assert "sector 3 captured from allies" in capsys.readouterr().out
