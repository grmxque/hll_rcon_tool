from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from unittest.mock import Mock

import pytest
from fakeredis import FakeRedis
from pydantic import ValidationError

from rcon import end_of_game_rewards as eog
from rcon.end_of_game_rewards import (
    build_player_message,
    compute_rewards,
    new_expiration,
    process_end_of_game,
)
from rcon.user_config.end_of_game_rewards import EndOfGameRewardsUserConfig as Config
from rcon.utils import INDEFINITE_VIP_DATE

NOW = datetime(2026, 11, 7, 20, 0, tzinfo=UTC)


def player(player_id, **stats):
    base = {
        "player_id": player_id,
        "player": player_id.title(),
        "kills": 0,
        "kills_streak": 0,
        "kills_per_minute": 0.0,
        "kill_death_ratio": 0.0,
        "combat": 0,
        "offense": 0,
        "defense": 0,
        "support": 0,
    }
    return base | stats


@pytest.fixture
def stats():
    return [
        player("alice", offense=900, combat=300),
        player("bob", defense=800, support=50, combat=400),
        player("carol", support=600, kills=10),
    ]


def config(**values):
    return Config(**({"enabled": True, "min_players_for_rewards": 0} | values))


def test_best_player_of_each_category_is_rewarded(stats):
    rewards = compute_rewards(stats, config(), {}, online_count=60)
    assert [(c, p["player_id"]) for c, p in rewards.top_players] == [
        ("offense", "alice"),
        ("defense", "bob"),
        ("support", "carol"),
        ("combat", "bob"),
    ]
    assert rewards.days == {"alice": 4, "bob": 8, "carol": 4}


def test_ties_reward_the_first_player(stats):
    stats[1]["offense"] = 900
    rewards = compute_rewards(stats, config(top_categories=["offense"]), {}, 60)
    assert rewards.days == {"alice": 4}


@pytest.mark.parametrize(
    "stats_values,expected_hits",
    [
        ({"kills": 70}, {"kills"}),
        ({"kills_streak": 10}, {"kills_streak"}),
        ({"kills_per_minute": 0.75}, {"kills_per_minute"}),
        ({"kills": 30, "kill_death_ratio": 1.5}, {"kill_death_ratio"}),
        ({"kills": 29, "kill_death_ratio": 3.0}, set()),
        ({"kills": 69, "kills_streak": 9, "kills_per_minute": 0.74}, set()),
    ],
)
def test_individual_thresholds(stats_values, expected_hits):
    rewards = compute_rewards(
        [player("dave", **stats_values)], config(top_categories=[]), {}, 60
    )
    assert rewards.hits["dave"] == expected_hits
    assert rewards.days.get("dave", 0) == 2 * len(expected_hits)


def test_individual_rewards_add_up_with_best_scores():
    dave = player("dave", kills=80, kills_streak=12, offense=10)
    rewards = compute_rewards([dave], config(top_categories=["offense"]), {}, 60)
    assert rewards.days == {"dave": 4 + 2 + 2}


def test_zero_threshold_disables_the_rule():
    rewards = compute_rewards(
        [player("dave", kills=0)],
        config(top_categories=[], kills_threshold=0),
        {},
        60,
    )
    assert rewards.hits["dave"] == set()


def test_fidelity_only_for_limited_vips(stats):
    vips = {
        "alice": {"player_id": "alice", "vip_expiration": NOW + timedelta(days=1)},
        "carol": {"player_id": "carol", "vip_expiration": INDEFINITE_VIP_DATE},
    }
    rewards = compute_rewards(
        stats, config(top_categories=[], fidelity_reward_days=1), vips, 60
    )
    assert rewards.days == {"alice": 1}


def test_unlimited_vip_and_vip_without_expiration_are_never_rewarded(stats):
    vips = {
        "alice": {"player_id": "alice", "vip_expiration": None},
        "bob": {"player_id": "bob", "vip_expiration": INDEFINITE_VIP_DATE},
    }
    assert compute_rewards(stats, config(), vips, 60).days == {"carol": 4}


@pytest.mark.parametrize(
    "changes,online",
    [({"min_players_for_rewards": 50}, 49), ({"vip_rewards_enabled": False}, 60)],
)
def test_rewards_require_population_and_switch(stats, changes, online):
    rewards = compute_rewards(stats, config(**changes), {}, online)
    assert not rewards.rewards_allowed
    assert rewards.days == {}
    assert rewards.top_players  # still announced


def test_expiration_rules():
    assert new_expiration(None, 2, NOW) == NOW + timedelta(days=2)
    future = {"vip_expiration": NOW + timedelta(days=5)}
    assert new_expiration(future, 2, NOW) == NOW + timedelta(days=7)
    expired = {"vip_expiration": NOW - timedelta(days=5)}
    assert new_expiration(expired, 2, NOW) == NOW + timedelta(days=2)
    assert new_expiration({"vip_expiration": INDEFINITE_VIP_DATE}, 2, NOW) is None


@pytest.mark.parametrize(
    "language,expected",
    [
        (
            "en",
            [
                "█ END OF GAME █ +4 d/TOP",
                "Offense : Dave (5)",
                "▒ Dave ▒",
                "KILLS 80 (+2 d) · STREAK 0",
                "K/MIN 0.71 · K/D 0",
                "YOU WON 6 DAYS OF VIP!",
            ],
        ),
        (
            "fr",
            [
                "█ FIN DE PARTIE █ +4 j/TOP",
                "Attaque : Dave (5)",
                "▒ Dave ▒",
                "KILLS 80 (+2 j) · SÉRIE 0",
                "K/MIN 0.71 · K/D 0",
                "TU AS GAGNÉ 6 JOURS DE VIP!",
            ],
        ),
    ],
)
def test_player_message_layout(language, expected):
    dave = player("dave", kills=80, kills_per_minute=0.71234, offense=5)
    cfg = config(language=language, top_categories=["offense"])
    rewards = compute_rewards([dave], cfg, {}, 60)
    assert build_player_message(dave, rewards, cfg).splitlines() == expected


def test_message_without_rewards_shows_no_days():
    dave = player("dave", kills=80)
    cfg = config(min_players_for_rewards=50)
    rewards = compute_rewards([dave], cfg, {}, 10)
    message = build_player_message(dave, rewards, cfg)
    assert "VIP" not in message


@pytest.fixture
def rcon(stats):
    rcon = Mock()
    rcon.get_player_ids.return_value = {"Alice": "alice", "Bob": "bob"}
    rcon.get_vip_ids.return_value = [
        {"player_id": "bob", "name": "Bob", "vip_expiration": NOW + timedelta(days=1)}
    ]
    return rcon


@pytest.fixture
def audit(monkeypatch):
    session = Mock()

    @contextmanager
    def fake_session():
        yield session

    monkeypatch.setattr(eog, "enter_session", fake_session)
    discord = Mock()
    monkeypatch.setattr(eog, "send_to_discord_audit", discord)
    return session, discord


def test_process_messages_connected_players_and_grants_vip(stats, rcon, audit):
    session, discord = audit
    cfg = config(discord_webhook_url="https://discord.com/api/webhooks/x")
    process_end_of_game(rcon, cfg, stats, now=NOW)

    calls = rcon.message_player.call_args_list
    assert [c.kwargs["player_id"] for c in calls] == [
        "alice",
        "bob",
    ]  # carol is offline
    assert "YOU WON 4 DAYS OF VIP!" in calls[0].kwargs["message"]

    grants = {c.args[0]: c.args for c in rcon.add_vip.call_args_list}
    assert grants["alice"] == (
        "alice",
        "Alice - Top stats VIP",
        (NOW + timedelta(days=4)).isoformat(),
    )
    # bob is VIP until NOW + 1 day: extended from there.
    assert grants["bob"][2] == (NOW + timedelta(days=9)).isoformat()
    assert "carol" in grants  # rewarded even though disconnected
    assert session.add.call_count == 3
    assert session.add.call_args.args[0].command_result == "vip_granted"
    assert "Alice +4d" in discord.call_args.kwargs["message"]


def test_dry_run_sends_nothing(stats, rcon, audit):
    session, _ = audit
    process_end_of_game(rcon, config(dry_run=True), stats, now=NOW)
    rcon.message_player.assert_not_called()
    rcon.add_vip.assert_not_called()
    assert session.add.call_args.args[0].command_result == "dry_run"


def test_stats_message_can_be_disabled(stats, rcon, audit):
    process_end_of_game(rcon, config(send_stats_message=False), stats, now=NOW)
    rcon.message_player.assert_not_called()
    assert rcon.add_vip.called


def test_one_failed_message_does_not_stop_the_others(stats, rcon, audit):
    rcon.message_player.side_effect = [Exception("connection closed"), True]
    process_end_of_game(rcon, config(), stats, now=NOW)
    assert [c.kwargs["player_id"] for c in rcon.message_player.call_args_list] == [
        "alice",
        "bob",
    ]
    assert rcon.add_vip.call_count == 3


def test_one_failed_vip_grant_does_not_stop_the_others(stats, rcon, audit):
    rcon.add_vip.side_effect = [Exception("rcon down"), True, True]
    process_end_of_game(rcon, config(), stats, now=NOW)
    assert rcon.add_vip.call_count == 3
    assert audit[0].add.call_count == 2


def test_match_end_is_processed_once(stats, rcon, monkeypatch):
    red = FakeRedis()
    monkeypatch.setattr(eog, "get_redis_client", lambda: red)
    monkeypatch.setattr(eog, "get_server_number", lambda: "1")
    monkeypatch.setattr(eog, "get_cached_live_game_stats", lambda: {"stats": stats})
    monkeypatch.setattr(Config, "load_from_db", classmethod(lambda cls: config()))
    process = Mock()
    monkeypatch.setattr(eog, "process_end_of_game", process)
    log = {"timestamp_ms": 123}
    eog.end_of_game_rewards_on_match_end(rcon, log)
    eog.end_of_game_rewards_on_match_end(rcon, log)
    assert process.call_count == 1
    eog.end_of_game_rewards_on_match_end(rcon, {"timestamp_ms": 456})
    assert process.call_count == 2


def test_disabled_or_failing_processing_never_raises(rcon, monkeypatch):
    monkeypatch.setattr(
        Config, "load_from_db", classmethod(lambda cls: Config(enabled=False))
    )
    eog.end_of_game_rewards_on_match_end(rcon, {"timestamp_ms": 1})
    monkeypatch.setattr(
        Config, "load_from_db", classmethod(lambda cls: (_ for _ in ()).throw(OSError))
    )
    eog.end_of_game_rewards_on_match_end(rcon, {"timestamp_ms": 2})


@pytest.mark.parametrize(
    "values",
    [
        {"top_categories": ["kills"]},
        {"min_players_for_rewards": 101},
        {"kills_per_minute_threshold": -1},
        {"vip_description": ""},
        {"language": "de"},
    ],
)
def test_invalid_settings_are_rejected(values):
    with pytest.raises(ValidationError):
        Config(**values)


def test_builder_roles_default_to_squad_leaders_and_commander():
    cfg = Config()
    assert cfg.garrison_builder_roles == ["officer", "armycommander"]
    assert cfg.node_builder_roles == ["engineer"]
    assert Config(garrison_builder_roles=["spotter"]).garrison_builder_roles == [
        "spotter"
    ]


@pytest.mark.parametrize("role", ["squadleader", "foo"])
def test_unknown_builder_roles_are_rejected(role):
    with pytest.raises(ValidationError):
        Config(garrison_builder_roles=["officer", role])


def test_defaults_match_the_original_plugin():
    cfg = Config()
    assert not cfg.enabled
    assert cfg.min_players_for_rewards == 50
    assert cfg.top_categories == ["offense", "defense", "support", "combat"]
    assert (cfg.top_category_reward_days, cfg.individual_reward_days) == (4, 2)
    assert (cfg.kills_threshold, cfg.kills_streak_threshold) == (70, 10)
    assert cfg.kills_per_minute_threshold == 0.75
    assert (cfg.kill_death_ratio_threshold, cfg.kill_death_ratio_min_kills) == (1.5, 30)
    assert cfg.fidelity_reward_days == 0


# Construction categories (TOP 3 garrison builders, node builders)
from rcon.actions_tracking.model import PlayerActions


def built(player_id, garrisons=0, nodes=0, reached=0.0, name=None):
    counts = {"garrison": garrisons, "node": nodes}
    counts = {action: count for action, count in counts.items() if count}
    return PlayerActions(
        player_id=player_id,
        name=name or player_id.title(),
        counts=counts,
        reached={action: reached for action in counts},
    )


def actions(*builders):
    return {b.player_id: b for b in builders}


def construction_config(**values):
    return config(
        **(
            {
                "top_categories": [],
                "individual_reward_days": 0,
                "garrison_rewards_enabled": True,
                "node_rewards_enabled": True,
            }
            | values
        )
    )


def test_top_3_garrison_builders_get_one_day_each():
    builders = actions(
        built("alice", garrisons=5, reached=300),
        built("bob", garrisons=2, reached=100),
        built("carol", garrisons=4, reached=200),
        built("dave", garrisons=1, reached=50),
    )
    rewards = compute_rewards([], construction_config(), {}, 60, builders)
    assert [b.player_id for b in rewards.top_garrison_builders] == [
        "alice",
        "carol",
        "bob",
    ]
    assert rewards.days == {"alice": 1, "carol": 1, "bob": 1}
    assert rewards.reasons["alice"] == ["top_garrison"]


def test_garrison_ties_go_to_the_first_to_reach_the_count():
    builders = actions(
        built("alice", garrisons=3, reached=900),
        built("bob", garrisons=3, reached=400),
        built("carol", garrisons=3, reached=400),  # same time as bob: player ID decides
        built("dave", garrisons=3, reached=100),
    )
    rewards = compute_rewards([], construction_config(), {}, 60, builders)
    assert [b.player_id for b in rewards.top_garrison_builders] == [
        "dave",
        "bob",
        "carol",
    ]
    assert "alice" not in rewards.days


def test_fewer_than_three_builders_and_zero_constructions():
    builders = actions(built("alice", garrisons=1), built("bob", nodes=2))
    rewards = compute_rewards([], construction_config(), {}, 60, builders)
    assert [b.player_id for b in rewards.top_garrison_builders] == ["alice"]
    assert rewards.days == {"alice": 1, "bob": 1}
    assert compute_rewards([], construction_config(), {}, 60, {}).days == {}
    assert compute_rewards([], construction_config(), {}, 60, None).days == {}


def test_every_node_builder_gets_one_day_whatever_the_count():
    builders = actions(built("alice", nodes=1), built("bob", nodes=3))
    rewards = compute_rewards([], construction_config(), {}, 60, builders)
    assert rewards.days == {"alice": 1, "bob": 1}
    assert {b.player_id for b in rewards.node_builders} == {"alice", "bob"}


def test_construction_categories_can_be_disabled_or_configured():
    builders = actions(built("alice", garrisons=2, nodes=1))
    cfg = construction_config(garrison_rewards_enabled=False, node_reward_days=3)
    rewards = compute_rewards([], cfg, {}, 60, builders)
    assert rewards.days == {"alice": 3}
    assert rewards.top_garrison_builders == []
    rewards = compute_rewards(
        [], construction_config(node_rewards_enabled=False), {}, 60, builders
    )
    assert rewards.days == {"alice": 1}


def test_construction_rewards_follow_the_existing_eligibility_rules():
    builders = actions(built("alice", garrisons=2, nodes=1), built("bob", nodes=1))
    vips = {"bob": {"player_id": "bob", "vip_expiration": None}}  # unlimited VIP
    cfg = construction_config(min_players_for_rewards=50)
    assert compute_rewards([], cfg, vips, 10, builders).days == {}
    cfg = construction_config(min_players_for_rewards=0, vip_rewards_enabled=False)
    assert compute_rewards([], cfg, vips, 60, builders).days == {}
    assert compute_rewards([], construction_config(), vips, 60, builders).days == {
        "alice": 2
    }


def test_rewards_add_up_across_categories(stats):
    builders = actions(built("carol", garrisons=4, nodes=2))
    cfg = config(garrison_rewards_enabled=True, node_rewards_enabled=True)
    rewards = compute_rewards(stats, cfg, {}, 60, builders)
    # carol: best support (4) + garrison top 3 (1) + node builder (1)
    assert rewards.days["carol"] == 6
    assert rewards.reasons["carol"] == ["top_support", "top_garrison", "node_builder"]
    # The historic rewards are unchanged.
    assert rewards.days["alice"] == 4 and rewards.days["bob"] == 8


def test_builder_who_left_before_the_end_is_rewarded(rcon, audit):
    builders = actions(built("erin", garrisons=2, name="Erin"))
    cfg = construction_config(discord_webhook_url="https://discord.com/api/webhooks/x")
    process_end_of_game(rcon, cfg, [], now=NOW, actions=builders)
    grants = {c.args[0]: c.args for c in rcon.add_vip.call_args_list}
    assert grants["erin"][1] == "Erin - Top stats VIP"
    row = audit[0].add.call_args.args[0]
    assert '"reasons": ["top_garrison"]' in row.command_arguments
    assert "Erin +1d [top_garrison]" in audit[1].call_args.kwargs["message"]


def test_construction_rewards_in_dry_run(rcon, audit):
    builders = actions(built("alice", garrisons=1))
    process_end_of_game(
        rcon, construction_config(dry_run=True), [], now=NOW, actions=builders
    )
    rcon.add_vip.assert_not_called()
    assert audit[0].add.call_args.args[0].command_result == "dry_run"


def worst_case_rewards(language):
    long = "X" * 32
    stats = [
        player(
            f"p{i}",
            player=f"{long}{i}",
            kills=100,
            kills_streak=20,
            kills_per_minute=1.5,
            kill_death_ratio=3.2,
            offense=1000 + i,
            defense=1000 + i,
            support=1000 + i,
            combat=1000 + i,
        )
        for i in range(5)
    ]
    builders = actions(
        *(
            built(f"p{i}", garrisons=10 - i, nodes=3, name=f"{long}{i}")
            for i in range(5)
        )
    )
    cfg = config(
        language=language, garrison_rewards_enabled=True, node_rewards_enabled=True
    )
    vips = {"p0": {"player_id": "p0", "vip_expiration": NOW + timedelta(days=3)}}
    cfg = cfg.model_copy(update={"fidelity_reward_days": 1})
    return stats, cfg, compute_rewards(stats, cfg, vips, 60, builders)


@pytest.mark.parametrize("language", ["en", "fr"])
def test_message_fits_a_console_in_the_worst_case(language):
    stats, cfg, rewards = worst_case_rewards(language)
    message = build_player_message(stats[0], rewards, cfg)
    lines = message.splitlines()
    assert len(lines) <= 16
    assert all(len(line) <= 40 for line in lines), lines
    assert lines[-1].startswith(("YOU WON", "TU AS GAGNÉ"))
    assert any(
        line.startswith(("GARRISONS 10 (+1", "GARNISONS 10 (+1")) for line in lines
    )


def test_message_with_constructions():
    builders = actions(
        built("alice", garrisons=5, reached=1),
        built("bob", garrisons=4, reached=2),
        built("carol", garrisons=3, reached=3, nodes=2),
        built("dave", garrisons=1, reached=4, nodes=1),
    )
    cfg = construction_config(language="fr")
    carol = player("carol")
    rewards = compute_rewards([carol], cfg, {}, 60, builders)
    assert build_player_message(carol, rewards, cfg).splitlines() == [
        "█ FIN DE PARTIE █",
        "▒ GARNISONS +1 j ▒",
        "1. Alice (5)",
        "2. Bob (4)",
        "3. Carol (3)",
        "Nodes +1 j : 2 constructeurs",
        "▒ Carol ▒",
        "KILLS 0 · SÉRIE 0",
        "K/MIN 0 · K/D 0",
        "GARNISONS 3 (+1 j) · NODES 2 (+1 j)",
        "TU AS GAGNÉ 2 JOURS DE VIP!",
    ]
    dave = player("dave")
    message = build_player_message(dave, rewards, cfg)
    assert "GARNISONS 1 · NODES 1 (+1 j)" in message  # not in the top 3


def test_message_sections_are_omitted_when_disabled_or_empty():
    cfg = construction_config()
    rewards = compute_rewards([player("alice")], cfg, {}, 60, {})
    message = build_player_message(player("alice"), rewards, cfg)
    assert "GARRISONS" not in message and "Nodes" not in message


def test_match_end_passes_the_actions_of_the_ending_match(stats, rcon, monkeypatch):
    red = FakeRedis()
    monkeypatch.setattr(eog, "get_redis_client", lambda: red)
    monkeypatch.setattr(eog, "get_server_number", lambda: "1")
    monkeypatch.setattr(eog, "get_cached_live_game_stats", lambda: {"stats": stats})
    monkeypatch.setattr(
        Config, "load_from_db", classmethod(lambda cls: construction_config())
    )
    history = [{"start": 5_000, "end": 9_000}, {"start": 1_000, "end": 4_000}]
    monkeypatch.setattr(eog, "MapsHistory", lambda: history)
    final = Mock(return_value={"alice": built("alice", garrisons=1)})
    monkeypatch.setattr(eog, "final_actions", final)
    process = Mock()
    monkeypatch.setattr(eog, "process_end_of_game", process)

    eog.end_of_game_rewards_on_match_end(rcon, {"timestamp_ms": 4_000_000})
    assert final.call_args.args[1:] == (1_000, 4_000)  # an older MATCH ENDED line
    assert process.call_args.kwargs["actions"] == final.return_value

    final.side_effect = RuntimeError("redis down")
    eog.end_of_game_rewards_on_match_end(rcon, {"timestamp_ms": 9_000_000})
    assert process.call_args.kwargs["actions"] == {}  # the other rewards still run
