import json
from contextlib import contextmanager
from datetime import timedelta
from unittest.mock import Mock

import hllrcon
import pytest
from fakeredis import FakeRedis
from pydantic import ValidationError

from rcon.api_commands import RconAPI
from rcon.automods import antisteamroll
from rcon.automods.antisteamroll import (
    AntiSteamrollAutomod,
    final_sector,
    inside_protected_sector,
)
from rcon.automods.automod import (
    _do_punitions_for_moderators,
    get_punitions_to_apply,
)
from rcon.commands import HLLCommandFailedError
from rcon.maps import GameMode
from rcon.types import GameEnum, ServerInfo
from rcon.user_config.auto_mod_antisteamroll import (
    AutoModAntiSteamrollUserConfig as Config,
)
from rcon.user_config.utils import InvalidKeysConfigurationError


@pytest.fixture
def scenario(monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(antisteamroll.time, "time", lambda: clock[0])
    server = ServerInfo(game=GameEnum.HLL_WW2, number=1)
    red = FakeRedis()
    mod = AntiSteamrollAutomod(Config(enabled=True), red, server)
    layer = hllrcon.HLLLayer.CARENTAN_WARFARE
    sector = final_sector(layer.id, "axis", server.game)
    (x1, y1), (x2, y2) = sector.area
    player = {
        "player_id": "attacker",
        "name": "Attacker",
        "team": "axis",
        "world_position": {"x": (x1 + x2) / 2, "y": (y1 + y2) / 2, "z": 100},
    }
    defender = dict(player, player_id="defender", team="allies")
    teams = {
        "axis": {
            "count": 1,
            "commander": None,
            "squads": {"Able": {"players": [player]}},
        },
        "allies": {
            "count": 1,
            "commander": None,
            "squads": {"Baker": {"players": [defender]}},
        },
    }
    game = {
        "current_map": {"id": layer.id},
        "game_mode": GameMode.WARFARE,
        "axis_score": 4,
        "allied_score": 1,
        "time_remaining": timedelta(minutes=60),
    }
    return mod, teams, game, clock, player


def confirmed(scenario):
    mod, teams, game, clock, _ = scenario
    assert not mod.server_punitions_to_apply(teams, game)
    clock[0] += 5
    assert not mod.server_punitions_to_apply(teams, game)
    clock[0] += 5
    return mod.server_punitions_to_apply(teams, game)


@pytest.mark.parametrize(
    "changes",
    [
        {"enabled": False},
        {"min_players": 3},
        {"allow_final_attack_remaining_minutes": 60},
    ],
)
def test_inactive_configuration(scenario, changes):
    mod = scenario[0]
    mod.config = (
        Config(enabled=True, **{k: v for k, v in changes.items() if k != "enabled"})
        if "enabled" not in changes
        else Config(**changes)
    )
    assert not confirmed(scenario)
    assert not mod.red.exists(mod.key)


@pytest.mark.parametrize(
    "mode", [GameMode.OFFENSIVE, GameMode.SKIRMISH, GameMode.CONQUEST]
)
def test_only_warfare(scenario, mode):
    scenario[2]["game_mode"] = mode
    assert not confirmed(scenario)


@pytest.mark.parametrize("scores", [(3, 2), (2, 3), (5, 0), (0, 5), (4, 0)])
def test_normal_ended_and_transitional_scores(scenario, scores):
    scenario[2].update(axis_score=scores[0], allied_score=scores[1])
    assert not confirmed(scenario)


def test_four_caps_detects_only_dominant_team(scenario):
    result = confirmed(scenario)
    assert [p.player_id for p in result.warning] == ["attacker"]
    assert not result.punish and not result.kick


def test_allied_leader_and_commander_are_checked(scenario):
    mod, teams, game, _, player = scenario
    game.update(axis_score=1, allied_score=4)
    sector = final_sector(game["current_map"]["id"], "allies", mod.server_info.game)
    (x1, y1), (x2, y2) = sector.area
    player.update(
        team="allies", world_position={"x": (x1 + x2) / 2, "y": (y1 + y2) / 2, "z": 100}
    )
    teams["axis"]["squads"] = {}
    teams["allies"]["squads"] = {}
    teams["allies"]["commander"] = player
    assert confirmed(scenario).warning[0].squad == "Commander"


@pytest.mark.parametrize("minutes", [30, 29, 0])
def test_remaining_time_releases_and_cleans_state(scenario, minutes):
    assert confirmed(scenario)
    mod, teams, game, _, _ = scenario
    game["time_remaining"] = timedelta(minutes=minutes)
    assert not mod.server_punitions_to_apply(teams, game)
    assert not mod.red.exists(mod.key)


def test_duration_stays_released(scenario):
    mod, teams, game, clock, _ = scenario
    mod.config.protection_duration_minutes = 1
    assert confirmed(scenario)
    for _ in range(13):
        clock[0] += 5
        game["time_remaining"] -= timedelta(seconds=5)
        result = mod.server_punitions_to_apply(teams, game)
    assert not result
    assert json.loads(mod.red.get(mod.key))["players"] == {}


def test_score_changes_reset_escalation(scenario):
    assert confirmed(scenario)
    mod, teams, game, _, _ = scenario
    game.update(axis_score=3, allied_score=2)
    assert not mod.server_punitions_to_apply(teams, game)
    assert not mod.red.exists(mod.key)
    game.update(axis_score=4, allied_score=1)
    assert confirmed(scenario).warning


@pytest.mark.parametrize("transition", ["layer", "timer", "config", "gap"])
def test_context_changes_reset_confirmation(scenario, transition):
    assert confirmed(scenario)
    mod, teams, game, clock, _ = scenario
    if transition == "layer":
        game["current_map"]["id"] = hllrcon.HLLLayer.CARENTAN_WARFARE_NIGHT.id
    elif transition == "timer":
        game["time_remaining"] += timedelta(minutes=1)
    elif transition == "config":
        mod.config.warning_message = "New message {player_name}"
    else:
        clock[0] += 20
    assert not mod.server_punitions_to_apply(teams, game)
    history = json.loads(mod.red.get(mod.key))["players"]["attacker"]
    assert history["warnings"] == []


def test_match_lifecycle_clears_same_map_and_blocks_post_match(scenario, monkeypatch):
    mod, teams, game, _, _ = scenario
    monkeypatch.setattr(antisteamroll, "get_redis_client", lambda: mod.red)
    rcon = Mock(server_info=mod.server_info)
    rcon.get_gamestate.return_value = game
    assert confirmed(scenario)
    antisteamroll.match_ended(rcon, {})
    assert not mod.red.exists(mod.key)
    assert not mod.server_punitions_to_apply(teams, game)
    antisteamroll.match_started(rcon, {})
    assert confirmed(scenario).warning


@pytest.mark.parametrize(
    "position",
    [
        None,
        {},
        {"x": 0, "y": 0, "z": 0},
        {"x": float("nan"), "y": 1, "z": 1},
        {"x": 1, "y": float("inf"), "z": 1},
        {"x": True, "y": 1, "z": 1},
        {"x": "1", "y": 1, "z": 1},
    ],
)
def test_unusable_positions_are_ignored(scenario, position):
    scenario[4]["world_position"] = position
    assert not confirmed(scenario)


@pytest.mark.parametrize(
    "layer",
    [
        hllrcon.HLLLayer.CARENTAN_WARFARE,
        hllrcon.HLLLayer.DRIEL_WARFARE_DAY,
        hllrcon.HLLLayer.FOY_WARFARE_DAY,
        hllrcon.HLLLayer.STMARIEDUMONT_WARFARE_DAY,
    ],
)
@pytest.mark.parametrize("dominant", ["axis", "allies"])
def test_mirrored_horizontal_vertical_and_boundaries(layer, dominant):
    sector = final_sector(layer.id, dominant, GameEnum.HLL_WW2)
    allies_home = 4 if layer.map.is_mirrored else 0
    assert (
        sector is layer.sectors[allies_home if dominant == "axis" else 4 - allies_home]
    )
    (x1, y1), (x2, y2) = sector.area
    mx, my = (x1 + x2) / 2, (y1 + y2) / 2
    margin = 1000

    def inside(x, y):
        return inside_protected_sector({"x": x, "y": y, "z": 100}, sector, margin)

    assert inside(mx, my)
    for x, y in [
        (x1, my),
        (x2, my),
        (mx, y1),
        (mx, y2),
        (x1 + margin, my),
        (x2 - margin, my),
        (mx, y1 + margin),
        (mx, y2 - margin),
    ]:
        assert not inside(x, y)
    assert inside(x1 + margin + 1, my)
    assert inside(x2 - margin - 1, my)
    assert inside(mx, y1 + margin + 1)
    assert inside(mx, y2 - margin - 1)


def test_unknown_layer_does_not_guess(scenario, monkeypatch):
    warning = Mock()
    monkeypatch.setattr(antisteamroll.logger, "warning", warning)
    scenario[2]["current_map"]["id"] = "unknown_warfare"
    assert not confirmed(scenario)
    assert "no verified final-sector geometry" in warning.call_args.args[0]


@pytest.mark.parametrize("event", ["leave", "die", "disconnect", "switch"])
def test_compliance_stops_actions_and_requires_new_confirmation(scenario, event):
    assert confirmed(scenario)
    mod, teams, game, _, player = scenario
    if event in ("leave", "die"):
        player["world_position"] = {"x": 0, "y": 0, "z": 0 if event == "die" else 100}
    elif event == "disconnect":
        teams["axis"]["squads"] = {}
    else:
        player["team"] = "allies"
    assert not mod.server_punitions_to_apply(teams, game)
    histories = json.loads(mod.red.get(mod.key))["players"]
    if event in ("leave", "die"):
        assert histories["attacker"]["entered"] is None
    else:
        assert not histories


def test_warning_punish_kick_sequence(scenario):
    mod, teams, game, clock, _ = scenario
    mod.config = Config(
        enabled=True,
        number_of_warnings=1,
        number_of_punishments=1,
        warning_interval_seconds=5,
        punish_interval_seconds=5,
        kick_grace_period_seconds=5,
    )
    assert confirmed(scenario).warning
    clock[0] += 5
    assert mod.server_punitions_to_apply(teams, game).punish
    clock[0] += 5
    assert mod.server_punitions_to_apply(teams, game).kick


def test_punishment_death_and_reentry_preserve_escalation(scenario):
    mod, teams, game, clock, player = scenario
    mod.config = Config(
        enabled=True,
        number_of_warnings=0,
        number_of_punishments=1,
        punish_interval_seconds=5,
        kick_grace_period_seconds=5,
    )
    assert confirmed(scenario).punish
    position = player["world_position"]
    player["world_position"] = {"x": 0, "y": 0, "z": 0}
    clock[0] += 5
    assert not mod.server_punitions_to_apply(teams, game)
    player["world_position"] = position
    assert confirmed(scenario).kick


def test_escalation_expires_after_compliance(scenario):
    mod, teams, game, clock, player = scenario
    mod.config.violation_reset_seconds = 15
    assert confirmed(scenario).warning
    position = player["world_position"]
    player["world_position"] = {"x": 0, "y": 0, "z": 0}
    for _ in range(3):
        clock[0] += 5
        assert not mod.server_punitions_to_apply(teams, game)
    assert not json.loads(mod.red.get(mod.key))["players"]
    player["world_position"] = position
    result = confirmed(scenario)
    assert result.warning and not result.punish


def test_reentry_at_reset_deadline_gets_fresh_escalation(scenario):
    mod, teams, game, clock, player = scenario
    mod.config.violation_reset_seconds = 15
    assert confirmed(scenario).warning
    position = player["world_position"]
    player["world_position"] = {"x": 0, "y": 0, "z": 0}
    for _ in range(2):
        clock[0] += 5
        assert not mod.server_punitions_to_apply(teams, game)
    clock[0] += 5
    player["world_position"] = position
    assert not mod.server_punitions_to_apply(teams, game)
    assert json.loads(mod.red.get(mod.key))["players"]["attacker"]["warnings"] == []


def test_kicks_can_be_disabled(scenario):
    mod, teams, game, clock, _ = scenario
    mod.config = Config(
        enabled=True,
        number_of_warnings=0,
        number_of_punishments=0,
        kick_after_max_punish=False,
    )
    assert not confirmed(scenario)
    for _ in range(10):
        clock[0] += 5
        assert not mod.server_punitions_to_apply(teams, game)


def test_three_cap_threshold_still_protects_only_final_sector(scenario):
    scenario[0].config.caps_threshold = 3
    scenario[2].update(axis_score=3, allied_score=2)
    assert confirmed(scenario).warning


def test_catalog_non_warfare_layer_is_skipped(scenario):
    scenario[2]["current_map"]["id"] = hllrcon.HLLLayer.CARENTAN_OFFENSIVE_US_DAY.id
    assert not confirmed(scenario)


@pytest.mark.parametrize(
    "field,expected",
    [("number_of_warnings", "warning"), ("number_of_punishments", "punish")],
)
def test_unlimited_stages_never_escalate(scenario, field, expected):
    mod, teams, game, clock, _ = scenario
    mod.config.number_of_warnings = -1 if field == "number_of_warnings" else 0
    mod.config.number_of_punishments = -1 if field == "number_of_punishments" else 2
    assert getattr(confirmed(scenario), expected)
    for _ in range(10):
        clock[0] += 5
        result = mod.server_punitions_to_apply(teams, game)
        assert not result.kick
        if expected == "warning":
            assert not result.punish


def test_punish_failure_retries_and_ignores_other_mods(scenario):
    mod, teams, game, _, _ = scenario
    mod.config.number_of_warnings = 0
    action = confirmed(scenario).punish[0]
    other = action.__class__(
        player_id=action.player_id,
        name=action.name,
        team=action.team,
        squad=action.squad,
        details=action.details.__class__(author="OtherMod"),
    )
    mod.player_punish_failed(other)
    assert json.loads(mod.red.get(mod.key))["players"]["attacker"]["punishes"]
    mod.player_punish_failed(action)
    assert mod.server_punitions_to_apply(teams, game).punish


@pytest.mark.parametrize("method", ["warning", "punish", "kick"])
def test_dry_run_audits_without_any_player_command(scenario, monkeypatch, method):
    mod = scenario[0]
    mod.config.dry_run = True
    if method != "warning":
        mod.config.number_of_warnings = 0
    if method == "kick":
        mod.config.number_of_punishments = 0
        mod.config.kick_grace_period_seconds = 5
    actions = confirmed(scenario)
    assert getattr(actions, method)
    session = Mock()

    @contextmanager
    def fake_session():
        yield session

    monkeypatch.setattr(antisteamroll, "enter_session", fake_session)
    monkeypatch.setattr("rcon.automods.automod.audit", Mock())
    rcon = Mock()
    _do_punitions_for_moderators(rcon, actions, [mod])
    rcon.message_player.assert_not_called()
    rcon.punish.assert_not_called()
    rcon.kick.assert_not_called()
    entry = session.add.call_args.args[0]
    assert entry.command == "antisteamroll_detection"
    assert entry.command_result == "dry_run"
    assert json.loads(entry.command_arguments)["player_id"] == "attacker"


def test_executor_retries_failed_real_punishment(scenario, monkeypatch):
    mod, _, _, _, _ = scenario
    mod.config.number_of_warnings = 0
    actions = confirmed(scenario)
    monkeypatch.setattr(mod, "audit_action", Mock())
    monkeypatch.setattr("rcon.automods.automod.audit", Mock())
    rcon = Mock()
    rcon.punish.side_effect = HLLCommandFailedError("dead")
    _do_punitions_for_moderators(rcon, actions, [mod])
    assert json.loads(mod.red.get(mod.key))["players"]["attacker"]["punishes"] == []


def test_server_dispatch_and_empty_team_cleanup(scenario):
    mod, teams, game, clock, _ = scenario
    rcon = Mock()
    rcon.get_team_view.return_value = teams
    rcon.get_gamestate.return_value = game
    assert not get_punitions_to_apply(rcon, [mod])
    clock[0] += 5
    assert not get_punitions_to_apply(rcon, [mod])
    clock[0] += 5
    assert get_punitions_to_apply(rcon, [mod]).warning
    rcon.get_team_view.return_value = {}
    assert not get_punitions_to_apply(rcon, [mod])
    assert json.loads(mod.red.get(mod.key))["players"] == {}


def test_live_config_api_and_next_cycle(scenario, monkeypatch):
    mod, teams, game, _, _ = scenario
    assert confirmed(scenario)
    persisted = {}
    monkeypatch.setattr(
        "rcon.user_config.auto_mod_antisteamroll.set_user_config",
        lambda name, config: persisted.update(config=config),
    )
    monkeypatch.setattr(
        Config, "load_from_db", lambda **scope: persisted.get("config", mod.config)
    )
    rcon = Mock(server_info=mod.server_info)
    rcon._validate_user_config = lambda **kwargs: RconAPI._validate_user_config(
        rcon, **kwargs
    )
    RconAPI.validate_auto_mod_antisteamroll_config(
        rcon,
        by="admin",
        config={"enabled": True, "warning_message": "Updated {player_name}"},
    )
    assert not persisted
    RconAPI.set_auto_mod_antisteamroll_config(
        rcon,
        by="admin",
        config={"enabled": True, "warning_message": "Updated {player_name}"},
    )
    mod.config = RconAPI.get_auto_mod_antisteamroll_config(rcon)
    assert not mod.server_punitions_to_apply(teams, game)
    assert confirmed(scenario).warning[0].details.message == "Updated Attacker"
    RconAPI.set_auto_mod_antisteamroll_config(
        rcon, by="admin", config={"enabled": False}
    )
    mod.config = RconAPI.get_auto_mod_antisteamroll_config(rcon)
    assert not mod.enabled()
    assert not mod.red.exists(mod.key)


@pytest.mark.parametrize(
    "values",
    [
        {"caps_threshold": 5},
        {"entry_grace_seconds": 0},
        {"min_players": 101},
        {"warning_message": "{unknown}"},
        {"punish_message": "{player_name.__class__}"},
        {"kick_message": "{"},
    ],
)
def test_invalid_settings_are_rejected(values):
    with pytest.raises(ValidationError):
        Config(**values)


def test_state_is_scoped_to_game_and_server(scenario):
    mod = scenario[0]
    assert (
        mod.key
        != AntiSteamrollAutomod(
            mod.config, mod.red, ServerInfo(game=GameEnum.HLL_WW2, number=2)
        ).key
    )

    assert (
        mod.key
        != AntiSteamrollAutomod(
            mod.config, mod.red, ServerInfo(game=GameEnum.HLL_VIETNAM, number=1)
        ).key
    )


def test_webhook_serializes_for_standard_persistence_and_api():
    config = Config(discord_webhook_url="https://discord.com/api/webhooks/example")
    data = json.loads(json.dumps(config.model_dump()))
    assert data["discord_webhook_url"] == "https://discord.com/api/webhooks/example"


def ended_match(scenario, monkeypatch, *, snapshot=True):
    mod, _, game, _, _ = scenario
    monkeypatch.setattr(antisteamroll, "get_redis_client", lambda: mod.red)
    rcon = Mock(server_info=mod.server_info)
    if snapshot:
        rcon.get_gamestate.return_value = game
    else:
        rcon.get_gamestate.side_effect = HLLCommandFailedError("timeout")
    antisteamroll.match_ended(rcon, {})
    return rcon


def test_missed_match_start_on_new_layer_resumes_protection(scenario, monkeypatch):
    mod, _, game, _, _ = scenario
    ended_match(scenario, monkeypatch)
    assert not confirmed(scenario)
    layer = hllrcon.HLLLayer.CARENTAN_WARFARE_NIGHT
    game["current_map"] = {"id": layer.id}
    sector = final_sector(layer.id, "axis", mod.server_info.game)
    (x1, y1), (x2, y2) = sector.area
    scenario[4]["world_position"] = {"x": (x1 + x2) / 2, "y": (y1 + y2) / 2, "z": 1}
    assert confirmed(scenario).warning


def test_missed_match_start_on_same_layer_resumes_for_whole_match(
    scenario, monkeypatch
):
    mod, _, game, _, _ = scenario
    ended_match(scenario, monkeypatch)
    game["time_remaining"] = timedelta(minutes=90)
    assert confirmed(scenario).warning
    # The new match later passes below the remaining time of the ended one.
    game["time_remaining"] = timedelta(minutes=50)
    assert json.loads(mod.red.get(antisteamroll.match_key(mod.server_info)))["active"]


def test_post_match_snapshot_failure_expires(scenario, monkeypatch):
    mod = scenario[0]
    ended_match(scenario, monkeypatch, snapshot=False)
    assert not confirmed(scenario)
    ttl = mod.red.ttl(antisteamroll.match_key(mod.server_info))
    assert 0 < ttl <= antisteamroll.ENDED_MARKER_TTL


def test_live_action_is_audited_once_applied(scenario, monkeypatch):
    mod = scenario[0]
    actions = confirmed(scenario)
    session = Mock()

    @contextmanager
    def fake_session():
        yield session

    monkeypatch.setattr(antisteamroll, "enter_session", fake_session)
    monkeypatch.setattr("rcon.automods.automod.audit", Mock())
    rcon = Mock()
    _do_punitions_for_moderators(rcon, actions, [mod])
    rcon.message_player.assert_called_once()
    assert session.add.call_args.args[0].command_result == "action_applied"


def test_failed_command_is_not_audited(scenario, monkeypatch):
    mod = scenario[0]
    mod.config.number_of_warnings = 0
    actions = confirmed(scenario)
    audit_action = Mock()
    monkeypatch.setattr(mod, "audit_action", audit_action)
    monkeypatch.setattr("rcon.automods.automod.audit", Mock())
    rcon = Mock()
    rcon.punish.side_effect = HLLCommandFailedError("dead")
    _do_punitions_for_moderators(rcon, actions, [mod])
    audit_action.assert_not_called()


def test_audit_failure_does_not_stop_the_executor(scenario, monkeypatch):
    mod = scenario[0]
    actions = confirmed(scenario)

    @contextmanager
    def broken_session():
        raise RuntimeError("database unavailable")
        yield

    monkeypatch.setattr(antisteamroll, "enter_session", broken_session)
    monkeypatch.setattr("rcon.automods.automod.audit", Mock())
    rcon = Mock()
    _do_punitions_for_moderators(rcon, actions, [mod])
    rcon.message_player.assert_called_once()


def test_punish_dropped_for_another_automod_is_rolled_back(scenario):
    mod, teams, game, clock, player = scenario
    mod.config.number_of_warnings = 0

    class SquadMod:
        def punitions_to_apply(self, team_view, squad_name, team, squad, gamestate):
            result = antisteamroll.PunitionsToApply()
            if squad_name == "Able":
                result.punish.append(
                    antisteamroll.PunishPlayer(
                        player_id=player["player_id"],
                        name=player["name"],
                        squad=squad_name,
                        team=team,
                        details=antisteamroll.PunishDetails(author="OtherMod"),
                    )
                )
            return result

    rcon = Mock()
    rcon.get_team_view.return_value = teams
    rcon.get_gamestate.return_value = game
    for _ in range(2):
        get_punitions_to_apply(rcon, [SquadMod(), mod])
        clock[0] += 5
    punitions = get_punitions_to_apply(rcon, [SquadMod(), mod])
    assert [p.details.author for p in punitions.punish] == ["OtherMod"]
    history = json.loads(mod.red.get(mod.key))["players"]["attacker"]
    assert history["punishes"] == []


def test_message_placeholders_distinguish_remaining_time_and_threshold(scenario):
    mod, _, game, _, _ = scenario
    mod.config.warning_message = "{remaining_minutes}/{attack_allowed_minutes}"
    game["time_remaining"] = timedelta(minutes=42, seconds=30)
    assert confirmed(scenario).warning[0].details.message == "42/30"


def test_api_rejects_unknown_keys_but_storage_tolerates_obsolete_ones():
    with pytest.raises(InvalidKeysConfigurationError):
        Config.save_to_db({"enabled": True, "unexpected": True}, dry_run=True)
    stored = Config.model_validate({"enabled": True, "removed_field": 1})
    assert stored.enabled
