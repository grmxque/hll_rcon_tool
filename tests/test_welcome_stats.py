import os
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import Mock

import pytest
from pydantic import ValidationError

from rcon import welcome_stats as ws
from rcon.user_config.welcome_stats import WelcomeStatsUserConfig as Config
from rcon.utils import INDEFINITE_VIP_DATE
from rcon.welcome_stats import (
    LABELS,
    AllTimeStats,
    build_message,
    compact_number,
    readable_duration,
    vip_status,
)

STATS = AllTimeStats(
    games=1520,
    kills=4200,
    deaths=2100,
    playtime_seconds=3 * 86400 + 2 * 3600 + 5 * 60 + 9,
    top_victims=[("Bob", 120), ("Carol", 80)],
    top_nemesis=[("Dave", 1500)],
)


def test_number_and_duration_formats():
    assert compact_number(999) == "999"
    assert compact_number(1500) == "1.5K"
    en = LABELS["en"]
    assert readable_duration(59, en) == "00h00m59s"
    assert readable_duration(STATS.playtime_seconds, en) == "3D 02h05m09s"
    assert readable_duration(400 * 86400, en) == "1Y 1M 5D 00h00m00s"
    assert readable_duration(40 * 86400, LABELS["fr"]) == "1M 10J 00h00m00s"


def test_vip_status():
    en = LABELS["en"]
    assert vip_status(None, en) == "NOT VIP"
    assert vip_status({"vip_expiration": None}, en) == "UNLIMITED VIP"
    assert vip_status({"vip_expiration": INDEFINITE_VIP_DATE}, en) == "UNLIMITED VIP"
    until = datetime(2026, 12, 24, 18, 30, tzinfo=UTC)
    assert vip_status({"vip_expiration": until}, en) == "VIP UNTIL 24/12/2026 18:30 UTC"


def test_message_layout_in_french():
    message = build_message("Alice", STATS, None, LABELS["fr"])
    assert message.splitlines() == [
        "▒ Alice ▒",
        "NON VIP",
        "",
        "PARTIES JOUÉES : 1.5K",
        "TEMPS DE JEU : 3J 02h05m09s",
        "2.0 KD (4.2K KILLS / 2.1K MORTS)",
        "",
        "VICTIMES :",
        "Bob (120 KILLS)",
        "Carol (80 KILLS)",
        "",
        "ENNEMIS JURÉS :",
        "Dave (1.5K MORTS)",
    ]


def test_empty_sections_are_hidden():
    stats = AllTimeStats(10, 5, 0, 60, [], [])
    message = build_message("Alice", stats, None, LABELS["en"])
    assert "VICTIMS" not in message and "NEMESIS" not in message
    assert "5.0 KD" in message  # no division by zero


@pytest.fixture
def rcon():
    rcon = Mock()
    rcon.get_vip_ids.return_value = [
        {"player_id": "alice", "vip_expiration": INDEFINITE_VIP_DATE}
    ]
    return rcon


def test_welcome_message_for_known_and_new_players(rcon, monkeypatch):
    monkeypatch.setattr(ws, "load_all_time_stats", lambda player_id, top: STATS)
    message = ws.welcome_message(rcon, Config(), "Alice", "alice")
    assert "UNLIMITED VIP" in message

    config = Config(min_games=2000, new_player_message="Welcome {player_name}!")
    assert ws.welcome_message(rcon, config, "Alice", "alice") == "Welcome Alice!"
    assert ws.welcome_message(rcon, Config(min_games=2000), "Alice", "alice") is None

    monkeypatch.setattr(ws, "load_all_time_stats", lambda player_id, top: None)
    assert ws.welcome_message(rcon, config, "Zed", "zed") == "Welcome Zed!"


def test_send_never_raises(rcon, monkeypatch):
    monkeypatch.setattr(
        ws, "load_all_time_stats", Mock(side_effect=RuntimeError("db down"))
    )
    ws.send_welcome_stats(rcon, Config(), "Alice", "alice")
    rcon.message_player.assert_not_called()


def test_send_uses_a_private_unsaved_message(rcon, monkeypatch):
    monkeypatch.setattr(ws, "load_all_time_stats", lambda player_id, top: STATS)
    ws.send_welcome_stats(rcon, Config(), "Alice", "alice")
    kwargs = rcon.message_player.call_args.kwargs
    assert kwargs["player_id"] == "alice"
    assert kwargs["save_message"] is False


def test_hook_schedules_the_message_after_the_delay(rcon, monkeypatch):
    timers = []

    class FakeTimer:
        def __init__(self, delay, function, args):
            timers.append((delay, function, args))
            self.daemon = False

        def start(self):
            timers[-1] += (self.daemon,)

    monkeypatch.setattr(ws.threading, "Timer", FakeTimer)
    monkeypatch.setattr(
        Config, "load_from_db", classmethod(lambda cls: Config(enabled=True))
    )
    log = {"player_id_1": "alice", "player_name_1": "Alice"}
    ws.welcome_stats_on_connected(rcon, log)
    delay, function, args, daemon = timers[0]
    assert (delay, function, daemon) == (20, ws.send_welcome_stats, True)
    assert args[2:] == ("Alice", "alice")

    monkeypatch.setattr(Config, "load_from_db", classmethod(lambda cls: Config()))
    ws.welcome_stats_on_connected(rcon, log)
    ws.welcome_stats_on_connected(rcon, {"player_id_1": None, "player_name_1": None})
    assert len(timers) == 1


@pytest.mark.parametrize(
    "values",
    [
        {"delay_seconds": 121},
        {"min_games": 0},
        {"top_count": 6},
        {"new_player_message": "{player_id}"},
        {"language": "de"},
    ],
)
def test_invalid_settings_are_rejected(values):
    with pytest.raises(ValidationError):
        Config(**values)


@pytest.mark.skipif(not os.getenv("HLL_DB_URL"), reason="needs the test database")
def test_all_time_stats_query():
    from rcon.models import (
        LogLine,
        Maps,
        PlayerID,
        PlayerName,
        PlayerSession,
        PlayerStats,
        enter_session,
    )

    suffix = uuid.uuid4().hex[:12]
    ids = {name: f"ws-{name}-{suffix}" for name in ("alice", "bob", "carol")}
    start = datetime(2020, 1, 1, tzinfo=UTC)
    stat_defaults = {
        column: 0
        for column in [
            "kills_streak",
            "deaths_without_kill_streak",
            "teamkills",
            "teamkills_streak",
            "deaths_by_tk",
            "deaths_by_tk_streak",
            "nb_vote_started",
            "nb_voted_yes",
            "nb_voted_no",
            "time_seconds",
            "kills_per_minute",
            "deaths_per_minute",
            "kill_death_ratio",
            "longest_life_secs",
            "shortest_life_secs",
            "combat",
            "offense",
            "defense",
            "support",
            "vehicle_kills",
            "vehicles_destroyed",
            "level",
            "kills_and_assists",
            "deaths_and_redeploys",
        ]
    } | {"most_killed": {}, "death_by": {}, "weapons": {}, "death_by_weapons": {}}

    created = {}
    try:
        with enter_session() as session:
            players = {k: PlayerID(player_id=v) for k, v in ids.items()}
            session.add_all(players.values())
            session.flush()
            created["players"] = [p.id for p in players.values()]
            session.add_all(
                [
                    PlayerName(
                        player_id_id=players["bob"].id, name="OldBob", last_seen=start
                    ),
                    PlayerName(
                        player_id_id=players["bob"].id,
                        name="Bob",
                        last_seen=start + timedelta(days=1),
                    ),
                    PlayerName(
                        player_id_id=players["carol"].id, name="Carol", last_seen=start
                    ),
                    PlayerSession(
                        player_id_id=players["alice"].id,
                        start=start,
                        end=start + timedelta(hours=2),
                    ),
                ]
            )
            maps = [
                Maps(
                    start=start + timedelta(days=i),
                    end=start + timedelta(days=i, hours=1),
                    server_number=99,
                    map_name=f"ws-test-{suffix}-{i}",
                )
                for i in range(2)
            ]
            session.add_all(maps)
            session.flush()
            created["maps"] = [m.id for m in maps]
            session.add_all(
                PlayerStats(
                    player_id_id=players["alice"].id,
                    map_id=m.id,
                    name="Alice",
                    kills=k,
                    deaths=d,
                    **stat_defaults,
                )
                for m, (k, d) in zip(maps, [(10, 5), (20, 5)], strict=True)
            )

            def kill(killer, victim):
                raw = f"ws-test {suffix} {uuid.uuid4().hex}"  # unique (event_time, raw)
                return LogLine(
                    event_time=start,
                    type="KILL",
                    raw=raw,
                    content=raw,
                    weapon="M1",
                    server="99",
                    player1_player_id=players[killer].id,
                    player2_player_id=players[victim].id,
                )

            session.add_all(
                [kill("alice", "bob") for _ in range(3)]
                + [kill("alice", "carol")]
                + [kill("carol", "alice") for _ in range(2)]
            )

        stats = ws.load_all_time_stats(ids["alice"], top_count=3)
        assert (stats.games, stats.kills, stats.deaths) == (2, 30, 10)
        assert stats.playtime_seconds == 2 * 3600
        assert stats.top_victims == [("Bob", 3), ("Carol", 1)]
        assert stats.top_nemesis == [("Carol", 2)]
        assert ws.load_all_time_stats(f"ws-unknown-{suffix}", top_count=3) is None
        assert ws.load_all_time_stats(ids["alice"], top_count=0).top_victims == []
    finally:
        with enter_session() as session:
            player_ids = created.get("players", [])
            session.query(LogLine).filter(
                LogLine.player1_player_id.in_(player_ids)
            ).delete(synchronize_session=False)
            session.query(PlayerStats).filter(
                PlayerStats.player_id_id.in_(player_ids)
            ).delete(synchronize_session=False)
            session.query(Maps).filter(Maps.id.in_(created.get("maps", []))).delete(
                synchronize_session=False
            )
            session.query(PlayerName).filter(
                PlayerName.player_id_id.in_(player_ids)
            ).delete(synchronize_session=False)
            session.query(PlayerSession).filter(
                PlayerSession.player_id_id.in_(player_ids)
            ).delete(synchronize_session=False)
            session.query(PlayerID).filter(PlayerID.id.in_(player_ids)).delete(
                synchronize_session=False
            )
