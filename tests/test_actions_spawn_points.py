"""Spawn points: spawns grouped by position and classified (rcon/actions_tracking/spawns.py)."""

import pytest

from rcon.actions_tracking.spawns import (
    AIRHEAD,
    GARRISON,
    HQ,
    OUTPOST,
    SETTLE_SECONDS,
    UNKNOWN,
    classify,
)
from tests.actions_scenarios import Match
from tests.test_actions_detection import build, confirm, wave

START = 1_000.0
HQ_POS = (50.0, 50.0)


@pytest.fixture
def match():
    m = Match(start=START)
    m.add("off", role="officer", unit_id=0, pos=(900.0, 900.0))
    m.squad("a", 10, unit_id=0)
    m.add("off2", role="officer", unit_id=1, pos=(300.0, 300.0))
    m.squad("b", 15, unit_id=1)
    m.add("cmd", role="armycommander", unit_id=-1, pos=(100.0, 100.0))
    m.add("solo", unit_id=None, pos=(400.0, 400.0))
    m.poll()
    m.poll()
    return m


def settle(m):
    m.idle(SETTLE_SECONDS + 3)


def point_at(m, at):
    return m.engine.points.near("allies", at[0] * 100, at[1] * 100)


def point(**values):
    base = {
        "first_ts": START + 600,
        "last_ts": START + 660,
        "spawns": 0,
        "squads": {},
        "commander": 0,
        "no_squad": 0,
        "credited": {},
        "uncredited": 0,
        "garrison": None,
    }
    return base | values


@pytest.mark.parametrize(
    "values, kind",
    [
        ({"garrison": "off"}, GARRISON),
        (
            {"spawns": 2, "squads": {"0": 1, "1": 1}, "credited": {"off": 2}},
            GARRISON,
        ),
        (
            {
                "spawns": 2,
                "squads": {"none": 1, "0": 1},
                "no_squad": 1,
                "credited": {"off": 2},
            },
            GARRISON,
        ),
        ({"spawns": 3, "squads": {"0": 3}, "credited": {"off": 3}}, OUTPOST),
        ({"spawns": 1, "squads": {"0": 1}, "credited": {"off": 1}}, UNKNOWN),
        (
            {
                "spawns": 3,
                "squads": {"0": 2, "1": 1},
                "uncredited": 3,
                "first_ts": START + 30,
            },
            HQ,
        ),
        ({"spawns": 3, "squads": {"0": 2, "1": 1}, "uncredited": 3}, AIRHEAD),
        ({"spawns": 3, "squads": {"0": 3}, "uncredited": 3}, UNKNOWN),
        (
            {
                "spawns": 4,
                "squads": {"0": 3, "-1": 1},
                "commander": 1,
                "uncredited": 4,
            },
            UNKNOWN,  # the commander also spawns on garrisons: no HQ evidence
        ),
        (
            {
                "spawns": 3,
                "squads": {"0": 2, "1": 1},
                "uncredited": 3,
                "last_ts": START + 1_300,
            },
            HQ,
        ),
        ({"spawns": 2, "squads": {"-1": 2}, "commander": 2, "uncredited": 2}, UNKNOWN),
    ],
    ids=[
        "confirmed garrison",
        "credited, two squads",
        "credited, no squad",
        "credited, one squad",
        "credited once",
        "uncredited from the start",
        "uncredited, placed during the match",
        "uncredited, one squad",
        "uncredited, commander: not an HQ",
        "uncredited, used for 10 min",
        "commander only",
    ],
)
def test_classify(values, kind):
    assert classify(point(**values), START)[0] == kind


def test_hq_used_from_the_start(match):
    wave(match, HQ_POS, squad=("a0", "a1"), team=("b0", "b1"))
    settle(match)
    hq = point_at(match, HQ_POS)
    assert hq["kind"] == HQ
    assert hq["spawns"] == 4 and hq["uncredited"] == 4
    assert hq["squads"] == {"0": 2, "1": 2}


def test_spawns_join_their_point_once_settled(match):
    wave(match, HQ_POS, team=("b0", "b1", "b2"))
    assert match.engine.points.points == []  # credits may still come
    settle(match)
    assert len(match.engine.points.points) == 1


def test_outpost_of_an_officer(match):
    outpost = (950.0, 950.0)
    match.idle(200)  # not the start of the match any more
    wave(match, outpost, squad=("a0", "a1"), credited="off")
    wave(match, outpost, squad=("a2", "a3"), credited="off")
    settle(match)
    found = point_at(match, outpost)
    assert (found["kind"], found["credited"]) == (OUTPOST, {"off": 4})


def test_confirmed_garrison_point(match):
    garrison = (800.0, 800.0)
    build(match, "off", garrison)
    confirm(match, "off", garrison)
    settle(match)
    found = point_at(match, garrison)
    assert (found["kind"], found["garrison"]) == (GARRISON, "off")
    assert match.engine.points.of_kind(GARRISON) == [found]


def test_airhead_placed_during_the_match(match):
    airhead = (1200.0, 600.0)
    match.idle(200)
    wave(match, airhead, squad=("a0", "a1"), team=("b0", "b1"))
    settle(match)
    assert point_at(match, airhead)["kind"] == AIRHEAD


def test_points_are_forgotten_when_unused(match):
    wave(match, HQ_POS, team=("b0", "b1", "b2"))
    settle(match)
    match.idle(20 * 60 + 10)
    assert match.engine.points.points == []


def test_points_survive_the_state_round_trip(match):
    wave(match, HQ_POS, team=("b0", "b1", "b2"))
    settle(match)
    from rcon.actions_tracking.engine import ActionsEngine

    copy = ActionsEngine(match.engine.to_state())
    assert copy.points.points == match.engine.points.points
    assert copy.first_ts == match.engine.first_ts


def test_replay_prints_the_spawn_points(match, tmp_path, capsys):
    import json

    from rcon.actions_tracking import __main__ as cli

    wave(match, HQ_POS, squad=("a0", "a1"), team=("b0", "b1"))
    settle(match)
    path = tmp_path / "polls.jsonl"
    path.write_text(
        "\n".join(json.dumps(s.to_dict()) for s in match.snapshots), encoding="utf-8"
    )
    assert cli.main(["replay", str(path), "--spawns"]) == 0
    lines = capsys.readouterr().out.strip().splitlines()
    assert len(lines) == 1
    assert "allies hq" in lines[0] and "spawns=4" in lines[0]
    assert "used since the start of the match" in lines[0]


def test_live_points(match):
    wave(match, HQ_POS, squad=("a0", "a1"), team=("b0", "b1"))  # HQ
    outpost = (950.0, 950.0)
    match.idle(200)
    wave(match, outpost, squad=("a2", "a3"), credited="off")
    wave(match, outpost, squad=("a4", "a5"), credited="off")
    wave(match, (1500.0, 1500.0), squad=("a6",))  # one spawn: unknown, not drawn
    settle(match)
    engine = match.engine
    assert point_at(match, HQ_POS)["kind"] == "hq"
    live = {p["kind"]: p for p in engine.points.live(engine.names, engine.last_ts)}
    assert set(live) == {"outpost"}  # HQs are not drawn
    assert (
        live["outpost"] | {"owner_id": "off", "owner_name": "Off", "spawns": 4}
        == live["outpost"]
    )
    # Next to a garrison marker: drawn as the garrison only.
    hidden = [{"team": "allies", "x": 95_000.0, "y": 95_000.0}]
    assert [
        p["kind"] for p in engine.points.live(engine.names, engine.last_ts, hidden)
    ] == []
    # An outpost nobody spawned on for 5 min is no longer drawn.
    later = engine.last_ts + 6 * 60
    assert engine.points.live(engine.names, later) == []
