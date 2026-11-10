"""Redis persistence, banner, recording and replay of the actions tracking."""

import json
from unittest.mock import Mock

import pytest
from fakeredis import FakeRedis

from rcon.actions_tracking import __main__ as cli
from rcon.actions_tracking import banner, tracker
from rcon.actions_tracking.model import PlayerActions
from rcon.user_config.end_of_game_rewards import EndOfGameRewardsUserConfig as Config

MATCH = {"start": 10_000, "end": None}


def settings(**values):
    return Config(
        **(
            {
                "enabled": True,
                "garrison_rewards_enabled": True,
                "node_rewards_enabled": True,
            }
            | values
        )
    )


@pytest.fixture
def red(monkeypatch):
    red = FakeRedis()
    for module in (tracker, banner):
        monkeypatch.setattr(module, "get_redis_client", lambda: red)
        monkeypatch.setattr(module, "get_server_number", lambda: "1")
    return red


@pytest.fixture
def config(monkeypatch):
    current = {"value": settings()}
    monkeypatch.setattr(tracker, "load_config", lambda: current["value"])
    return current


def players(**support):
    return {
        "players": {
            player_id: {
                "name": player_id.title(),
                "team": "allies",
                "unit_id": 0,
                "role": "officer" if player_id.startswith("off") else "engineer",
                "support": points,
                "deaths": 0,
                # 300 m apart: each one can build a garrison.
                "world_position": {
                    "x": 1000.0 + 30_000 * index,
                    "y": 1000.0,
                    "z": 10.0,
                },
            }
            for index, (player_id, points) in enumerate(support.items())
        },
        "fail_count": 0,
    }


def summary(red, config, end_ts=10_100):
    return tracker.final_actions(config["value"], MATCH["start"], end_ts)


def test_detection_survives_a_restart_and_replayed_polls(red, config):
    rcon = Mock()
    tracker.observe_poll(rcon, MATCH, players(off=0), now=10_003)
    # Each call reloads the state from Redis: the same as a log loop restart.
    tracker.observe_poll(rcon, MATCH, players(off=50), now=10_006)
    tracker.observe_poll(rcon, MATCH, players(off=50), now=10_006)  # replayed poll
    tracker.observe_poll(rcon, MATCH, players(off=50), now=10_004)  # older poll
    assert summary(red, config)["off"].count("garrison") == 1
    decisions = tracker.stored_decisions(MATCH["start"])
    assert [(d["kind"], d["verdict"]) for d in decisions] == [("garrison", "counted")]
    assert decisions[0]["reason"] == "only a construction explains the gain"


def test_each_match_has_its_own_state(red, config):
    rcon = Mock()
    tracker.observe_poll(rcon, MATCH, players(off=0), now=10_003)
    tracker.observe_poll(rcon, MATCH, players(off=50), now=10_006)
    next_match = {"start": 20_000, "end": None}
    tracker.observe_poll(rcon, next_match, players(off=0), now=20_003)
    assert tracker.final_actions(config["value"], 20_000, 20_100) == {}
    assert summary(red, config)["off"].count("garrison") == 1


def test_finalize_is_idempotent(red, config):
    rcon = Mock()
    tracker.observe_poll(rcon, MATCH, players(eng=0), now=10_003)
    tracker.observe_poll(rcon, MATCH, players(eng=90), now=10_006)
    first = summary(red, config)
    assert (
        first["eng"].count("node") == 1
    )  # pending node, the match ended before confirmation
    assert summary(red, config) == first
    tracker.observe_poll(rcon, MATCH, players(eng=190), now=10_009)  # after the end
    assert summary(red, config) == first


def test_nothing_happens_when_disabled_or_outside_a_running_match(red, config):
    rcon = Mock()
    config["value"] = settings(
        garrison_rewards_enabled=False, node_rewards_enabled=False
    )
    tracker.observe_poll(rcon, MATCH, players(off=0), now=10_003)
    config["value"] = settings(enabled=False)
    tracker.observe_poll(rcon, MATCH, players(off=0), now=10_004)
    config["value"] = settings()
    tracker.observe_poll(
        rcon, {"start": 10_000, "end": 10_002}, players(off=0), now=10_005
    )
    tracker.observe_poll(rcon, {"start": None, "end": None}, players(off=0), now=10_006)
    assert red.keys() == []


def test_invalid_data_never_breaks_the_log_loop(red, config):
    tracker.observe_poll(Mock(), MATCH, {"players": {"x": {"support": "abc"}}}, now=1)
    tracker.observe_poll(Mock(), MATCH, None, now=2)


def test_banner_after_a_new_construction(red, config, monkeypatch):
    queue = Mock()
    monkeypatch.setattr("rcon.workers.get_queue", lambda: queue)
    config["value"] = settings(thanks_banner_enabled=True, language="fr")
    rcon = Mock()
    rcon.set_broadcast.return_value = "Bienvenue"
    tracker.observe_poll(rcon, MATCH, players(off=0, off2=0), now=10_003)
    tracker.observe_poll(rcon, MATCH, players(off=50, off2=0), now=10_006)
    rcon.set_broadcast.assert_called_once_with(
        "Merci pour la construction de garnisons : Off (1)"
    )
    # No node builder: straight back to the previous broadcast.
    [restore] = queue.enqueue_in.call_args_list
    assert restore.args[0].total_seconds() == 30
    assert restore.args[2] == "Bienvenue"
    # Another construction within 5 minutes: no new banner.
    tracker.observe_poll(rcon, MATCH, players(off=50, off2=50), now=10_009)
    assert summary(red, config)["off2"].count("garrison") == 1
    assert rcon.set_broadcast.call_count == 1


def test_banner_is_not_sent_in_dry_run(red, config, monkeypatch):
    config["value"] = settings(thanks_banner_enabled=True, dry_run=True)
    rcon = Mock()
    tracker.observe_poll(rcon, MATCH, players(off=0), now=10_003)
    tracker.observe_poll(rcon, MATCH, players(off=50), now=10_006)
    rcon.set_broadcast.assert_not_called()


def test_banner_messages_and_chaining(red, monkeypatch):
    queue = Mock()
    monkeypatch.setattr("rcon.workers.get_queue", lambda: queue)
    builders = {
        f"p{i}": PlayerActions(
            f"p{i}",
            f"Player{i}",
            counts={"garrison": i % 3, "node": 1},
            reached={"garrison": i, "node": i},
        )
        for i in range(12)
    }
    cfg = settings(thanks_banner_enabled=True, thanks_banner_seconds=20)
    garrisons, nodes = banner.build_thanks_messages(cfg, builders)
    assert garrisons.startswith(
        "Thanks for building garrisons: Player2 (2), Player5 (2)"
    )
    assert garrisons.endswith("Player7 (1), Player10 (1)")  # 8 builders: all shown
    assert nodes.endswith(", +4 others")
    rcon = Mock()
    rcon.set_broadcast.return_value = "previous"
    assert banner.send_thanks_banner(rcon, cfg, builders)
    rcon.set_broadcast.assert_called_once_with(garrisons)
    calls = [
        (c.args[0].total_seconds(), c.args[2]) for c in queue.enqueue_in.call_args_list
    ]
    assert calls == [(20, nodes), (40, "previous")]
    assert not banner.send_thanks_banner(rcon, cfg, builders)  # throttled


def test_banner_skips_disabled_categories():
    builders = {"p": PlayerActions("p", "P", counts={"garrison": 1, "node": 1})}
    cfg = settings(node_rewards_enabled=False)
    assert banner.build_thanks_messages(cfg, builders) == [
        "Thanks for building garrisons: P (1)"
    ]
    assert banner.build_thanks_messages(cfg, {}) == []


def test_recorded_snapshots_can_be_replayed(red, config, monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("LOGGING_PATH", str(tmp_path))
    config["value"] = settings(record_actions_snapshots=True)
    rcon = Mock()
    tracker.observe_poll(rcon, MATCH, players(off=0, eng=0), now=10_003)
    tracker.observe_poll(rcon, MATCH, players(off=50, eng=0), now=10_006)
    tracker.observe_poll(rcon, MATCH, players(off=50, eng=90), now=10_009)
    path = tmp_path / "actions_snapshots" / "1_10000.jsonl"
    assert len(path.read_text().splitlines()) == 3
    assert json.loads(path.read_text().splitlines()[0])["ts"] == 10_003

    assert cli.main(["replay", str(path)]) == 0
    output = capsys.readouterr().out
    assert (
        "garrison counted   Off (off) +50: only a construction explains the gain"
        in output
    )
    assert "Off (off): garrison: 1" in output
    assert "TOP 3 garrisons: Off (1)" in output


def test_the_log_loop_feeds_its_player_poll_to_the_detector(monkeypatch):
    from unittest.mock import patch

    from tests.test_log_loop_maps_history import (
        make_gamestate,
        make_loop,
        make_map_info,
    )

    current_map = make_map_info()
    observe = Mock()
    monkeypatch.setattr("rcon.logs.loop.observe_poll", observe)
    with patch("rcon.logs.loop.MapsHistory") as maps_history_cls:
        maps_history_cls.return_value.get_current_map.return_value = current_map
        loop = make_loop(make_gamestate())
        loop.update_maps_history(prev_map_time_elapsed=1)
    dp = loop.get_detailed_players.return_value
    observe.assert_called_once_with(loop.rcon, current_map, dp)
