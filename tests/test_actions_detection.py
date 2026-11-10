"""Garrison / node detection scenarios: each one checks false positives and false negatives."""

import json

import pytest

from rcon.actions_tracking.engine import ActionsEngine
from rcon.actions_tracking.model import PollSnapshot
from tests.actions_scenarios import HQ, Match

GARRISON = (800.0, 800.0)


@pytest.fixture
def match():
    m = Match()
    m.add("off", role="officer", unit_id=0, pos=(900.0, 900.0))
    m.squad("a", 10, unit_id=0)
    m.add("off2", role="officer", unit_id=1, pos=(300.0, 300.0))
    m.squad("b", 15, unit_id=1)
    m.add("eng", role="engineer", unit_id=2, pos=(600.0, 600.0))
    m.add("cmd", role="armycommander", unit_id=-1, pos=(100.0, 100.0))
    m.add("axis_off", team="axis", role="officer", unit_id=0, pos=(1500.0, 1500.0))
    m.poll()
    return m


def verdicts(decisions, player_id):
    return [(d.kind, d.verdict) for d in decisions if d.player_id == player_id]


def wave(m, at, squad=(), team=(), credited=None, extra=0):
    """Teammates die then spawn at ``at``; ``credited`` gets +5 per squad member and +3 per other
    teammate (the owner of the spawn point), plus ``extra`` points."""
    players = list(squad) + list(team)
    m.die(*players)
    m.idle(15)
    m.spawn(players, at=at)
    if credited:
        m.gain(credited, 5 * len(squad) + 3 * len(team) + extra)
    return m.poll()


def confirm(m, builder, at, squad=(), team=("b0", "b1")):
    """A spawn wave on the builder's garrison at ``at``."""
    return wave(m, at, squad=squad, team=team, credited=builder)


def build(m, builder, at, points=50):
    m.move(builder, at)
    m.poll()
    m.gain(builder, points)
    return m.poll()


def build_known_garrison(m, officer="off", at=GARRISON):
    """The officer builds a garrison at ``at``, teammates spawn on it, he walks away."""
    build(m, officer, at)
    confirm(m, officer, at)
    m.move(officer, (at[0] + 200, at[1] + 200))
    m.poll()


# 1
def test_garrison_with_exactly_the_construction_bonus(match):
    decisions = build(match, "off", GARRISON)
    assert verdicts(decisions, "off") == [("garrison", "pending")]
    assert match.garrisons("off") == 0  # not before a spawn on it
    confirm(match, "off", GARRISON)
    assert match.garrisons("off") == 1
    assert ("garrison", "confirmed") in verdicts(match.decisions, "off")


def test_garrison_nobody_spawns_on_is_not_counted(match):
    build(match, "off", GARRISON)
    match.idle(310)
    assert match.garrisons("off") == 0
    assert ("garrison", "rejected") in verdicts(match.decisions, "off")


def test_garrison_built_on_own_supplies(match):
    # Observed in game: an officer building on his own truck crate gets +50 for the garrison and
    # +50 for the 50 supplies consumed.
    decisions = build(match, "off", GARRISON, points=100)
    assert verdicts(decisions, "off") == [("garrison", "pending")]
    confirm(match, "off", GARRISON)
    assert match.garrisons("off") == 1


def test_supplies_consumed_by_someone_else_are_not_a_garrison(match):
    # off2 builds a garrison on off's truck crate: both get +50 in the same poll.
    match.move("off2", (1300.0, 1300.0))
    match.poll()
    match.gain("off2", 50)
    match.gain("off", 50)
    match.poll()
    confirm(match, "off2", (1300.0, 1300.0), team=("a0", "a1"))
    match.idle(310)
    assert match.garrisons("off2") == 1
    assert match.garrisons("off") == 0


def test_small_spawn_credits_are_not_a_garrison(match):
    wave(match, (950.0, 950.0), squad=("a0", "a1", "a2"), credited="off")
    match.idle(310)
    assert match.garrisons("off") == 0


def test_a_single_spawn_confirms(match):
    build(match, "off", GARRISON)
    confirm(match, "off", GARRISON, team=("b0",))
    assert match.garrisons("off") == 1


def test_credits_split_over_two_polls_confirm(match):
    build(match, "off", GARRISON)
    match.die("b0", "b1", "b2")
    match.idle(15)
    match.spawn(["b0", "b1", "b2"], at=GARRISON)
    match.gain("off", 6)  # the third credit comes with the next poll
    match.poll()
    assert match.garrisons("off") == 1


def test_credit_one_poll_after_the_spawn_confirms(match):
    build(match, "off", GARRISON)
    match.die("b0", "b1")
    match.idle(15)
    match.spawn(["b0", "b1"], at=GARRISON)
    match.poll()  # the credits come with the next poll
    match.gain("off", 6)
    match.poll()
    assert match.garrisons("off") == 1


def test_wave_on_a_garrison_while_building_the_next_one(match):
    build(match, "off", GARRISON)
    match.move("off", (1200.0, 800.0))
    match.die("b0", "b1")
    match.idle(15)
    match.spawn(["b0", "b1"], at=GARRISON)
    match.gain("off", 50 + 6)  # second garrison + the wave on the first one
    match.poll()
    confirm(match, "off", (1200.0, 800.0), team=("b2",))
    assert match.garrisons("off") == 2


def test_two_candidates_next_to_each_other_count_once(match):
    match.gain("off", 50)  # someone consumed his supplies
    match.poll()
    match.move("off", (910.0, 900.0))
    match.poll()
    match.gain("off", 50)  # his garrison, 10 m away
    match.poll()
    confirm(match, "off", (905.0, 900.0))
    confirm(match, "off", (905.0, 900.0), team=("b2", "b3"))
    match.idle(310)
    assert match.garrisons("off") == 1


def test_commander_ability_at_the_hq_at_match_start(match):
    match.move("cmd", HQ)
    match.poll()
    match.gain("cmd", 50)  # ability before anybody spawned at the HQ
    match.poll()
    squad_b = [f"b{i}" for i in range(6)]
    wave(match, HQ, team=squad_b)  # HQ spawns credit nobody
    wave(match, HQ, team=squad_b)
    # His real garrison elsewhere (not yet known) credits him as much as a part of an HQ wave.
    build(match, "cmd", (1500.0, 300.0))
    wave(match, HQ, team=squad_b, credited="cmd", extra=-12)
    match.idle(310)
    assert match.garrisons("cmd") == 0


# 2
def test_garrison_with_simultaneous_spawn_credits(match):
    match.die("a0", "b0")
    match.poll()
    match.spawn(["a0"], at=(950.0, 950.0))
    match.spawn(["b0"], at=(950.0, 950.0))
    match.gain("off", 58)  # 50 + 5 (squad member) + 3 (teammate)
    decisions = match.poll()
    assert verdicts(decisions, "off") == [("garrison", "pending")]
    confirm(match, "off", (900.0, 900.0), team=("b1", "b2"))
    assert match.garrisons("off") == 1


# 3
def test_big_spawn_wave_on_a_known_garrison_is_not_a_new_garrison(match):
    build_known_garrison(match)
    squad = [f"a{i}" for i in range(10)]
    decisions = wave(match, GARRISON, squad=squad, credited="off")
    assert verdicts(decisions, "off") == [("garrison", "none")]
    match.idle(310)
    assert match.garrisons("off") == 1  # only the first one


def test_spawn_wave_while_the_officer_is_dead(match):
    squad = [f"a{i}" for i in range(10)]
    match.die("off")
    wave(match, (2000.0, 2000.0), squad=squad, credited="off")
    match.idle(310)
    assert match.garrisons("off") == 0


def test_ambiguous_wave_of_unknown_origin_is_not_confirmed_by_itself(match):
    squad = [f"a{i}" for i in range(10)]
    decisions = wave(match, (2000.0, 2000.0), squad=squad, credited="off")
    assert verdicts(decisions, "off") == [("garrison", "pending")]
    match.idle(310)
    assert match.garrisons("off") == 0


def test_ambiguous_wave_confirmed_by_later_spawns_at_the_build_position(match):
    squad = [f"a{i}" for i in range(10)]
    wave(match, (2000.0, 2000.0), squad=squad, credited="off")
    confirm(match, "off", (900.0, 900.0))
    assert match.garrisons("off") == 1


# 4
def test_garrison_built_during_a_spawn_wave(match):
    build_known_garrison(match)
    build_known_garrison(match, officer="off2", at=(300.0, 600.0))
    match.move("off", (1200.0, 1200.0))
    match.poll()
    squad_b = [f"b{i}" for i in range(15)]
    match.die(*squad_b, "a0", "a1", "a2", "a3")
    match.idle(15)
    match.spawn(squad_b, at=(300.0, 600.0))  # on off2's garrison
    match.spawn(["a0", "a1", "a2", "a3"], at=GARRISON)  # on off's first garrison
    match.gain("off2", 75)
    match.gain("off", 50 + 20)  # new garrison + 4 squad members on the first one
    decisions = match.poll()
    assert verdicts(decisions, "off") == [("garrison", "pending")]
    confirm(match, "off", (1200.0, 1200.0), team=("b0", "b1"))
    assert match.garrisons("off") == 2
    assert (
        match.garrisons("off2") == 1
    )  # the wave on its garrison is not a construction


def test_wave_on_another_builders_spawn_point_does_not_hide_a_garrison(match):
    build_known_garrison(match, officer="off2", at=(300.0, 600.0))
    squad_b = [f"b{i}" for i in range(15)]
    match.die(*squad_b)
    match.idle(15)
    match.spawn(squad_b, at=(300.0, 600.0))
    match.gain("off2", 75)
    match.gain("off", 50)  # 15 teammates x 3 + 5 would also give 50
    match.poll()
    confirm(match, "off", (900.0, 900.0), squad=("a0", "a1"), team=())
    assert match.garrisons("off") == 1
    assert match.garrisons("off2") == 1


# 5
def test_several_garrisons_by_the_same_officer(match):
    for position in [(900.0, 900.0), (1100.0, 900.0), (1100.0, 1100.0)]:
        build(match, "off", position)
        confirm(match, "off", position)
        match.idle(30)
    assert match.garrisons("off") == 3
    assert match.engine.summary()["off"].reached["garrison"] is not None


def test_one_garrison_at_most_between_two_polls(match):
    match.gain("off", 150)  # garrison + own supplies + 50 unexplained
    match.poll()
    assert all(d.verdict != "pending" for d in match.decisions if d.player_id == "off")


def test_rebuilt_next_to_the_old_one_needs_spawns_on_the_new_one(match):
    build_known_garrison(match)
    build(match, "off", (GARRISON[0] + 5, GARRISON[1] + 5))  # same spot
    confirm(match, "off", GARRISON)  # spawns on the old one's spot
    match.idle(310)
    assert match.garrisons("off") == 1  # nothing tells the new one from the old one


def test_quick_rebuild_on_a_truck(match):
    build(match, "off", GARRISON, points=100)
    confirm(match, "off", GARRISON)
    build(match, "off", (1100.0, 800.0), points=100)  # 30 s later, 300 m away
    confirm(match, "off", (1100.0, 800.0))
    assert match.garrisons("off") == 2


# 6
def test_commander_abilities_are_not_constructions(match):
    for points in (50, 100, 75, 50, 50):
        match.gain("cmd", points)
        match.poll()
        match.idle(30)
    match.idle(310)
    assert match.garrisons("cmd") == 0
    assert match.nodes("cmd") == 0


def test_commander_garrison_confirmed_by_teammates_spawning_on_it(match):
    build(match, "cmd", (1300.0, 1300.0))
    confirm(match, "cmd", (1302.0, 1302.0), team=("b0", "b1", "b2", "b3"))
    assert match.garrisons("cmd") == 1


def test_commander_at_the_hq_is_not_confirmed_by_hq_spawns(match):
    squad_b = [f"b{i}" for i in range(10)]
    wave(match, HQ, team=squad_b)  # HQ spawns credit nobody
    build(match, "cmd", HQ)  # ability
    wave(match, HQ, team=squad_b)
    wave(match, HQ, team=squad_b, credited="cmd", extra=-30 + 50)  # another ability
    match.idle(310)
    assert match.garrisons("cmd") == 0


def test_officer_next_to_the_hq_is_not_confirmed_by_hq_spawns(match):
    squad_b = [f"b{i}" for i in range(6)]
    wave(match, HQ, team=squad_b)
    build(match, "off", HQ)  # supplies of his consumed by someone else
    # His squad spawns on his outpost elsewhere while teammates spawn at the HQ next to him.
    match.die("a0", "a1", *squad_b)
    match.idle(15)
    match.spawn(["a0", "a1"], at=(1500.0, 300.0))
    match.spawn(squad_b, at=HQ)
    match.gain("off", 10)
    match.poll()
    match.idle(310)
    assert match.garrisons("off") == 0


def test_commander_ability_on_his_own_garrison_is_not_a_second_one(match):
    build(match, "cmd", (1300.0, 1300.0))
    confirm(match, "cmd", (1301.0, 1301.0), team=("b0", "b1"))
    match.idle(20)
    match.gain("cmd", 50)  # ability, standing on it
    match.poll()
    confirm(match, "cmd", (1301.0, 1301.0), team=("b2", "b3", "b4"))
    match.idle(310)
    assert match.garrisons("cmd") == 1


def test_outpost_passive_income_is_not_a_garrison(match):
    for _ in range(8):
        match.idle(57)
        match.gain("off", 10)
        match.poll()
    assert match.garrisons("off") == 0


def test_other_team_is_independent(match):
    match.add("axis_rifleman", team="axis", unit_id=1, pos=(1600.0, 1600.0))
    match.poll()
    build(match, "axis_off", (1500.0, 1500.0))
    match.die("axis_rifleman")
    match.idle(15)
    match.spawn(["axis_rifleman"], at=(1500.0, 1500.0))
    match.gain("axis_off", 3)
    match.poll()
    assert match.garrisons("axis_off") == 1
    assert match.garrisons("off") == 0


# 7
def passive_minutes(m, player_id, minutes, per_tick=10):
    for _ in range(minutes):
        m.idle(57)
        m.gain(player_id, per_tick)
        m.poll()


def test_node_construction_followed_by_its_passive_production(match):
    match.gain("eng", 90)  # blueprint + construction by the same engineer
    decisions = match.poll()
    assert verdicts(decisions, "eng") == [("node", "pending")]
    passive_minutes(match, "eng", 6)
    assert match.nodes("eng") == 1
    assert ("node", "confirmed") in verdicts(match.decisions, "eng")


def test_node_completed_by_another_engineer_gives_50(match):
    match.gain("eng", 50)
    match.poll()
    passive_minutes(match, "eng", 2)
    assert match.nodes("eng") == 1


def test_passive_production_alone_is_not_a_new_construction(match):
    match.gain("eng", 90)
    match.poll()
    passive_minutes(match, "eng", 10)
    pending = [
        d for d in match.decisions if d.player_id == "eng" and d.verdict == "pending"
    ]
    assert len(pending) == 1
    assert match.nodes("eng") == 1


def test_repair_station_without_passive_production_is_rejected(match):
    match.gain("eng", 90)
    match.poll()
    for _ in range(10):  # repairing a tank with the blowtorch
        match.gain("eng", 5)
        match.poll()
    match.idle(170)
    assert match.nodes("eng") == 0
    assert ("node", "rejected") in verdicts(match.decisions, "eng")


def test_engineer_who_leaves_before_confirmation_keeps_the_node(match):
    match.gain("eng", 90)
    match.poll()
    match.remove("eng")
    match.idle(170)
    assert match.nodes("eng") == 1


def test_node_built_at_the_very_end_counts(match):
    match.gain("eng", 90)
    match.poll()
    match.idle(20)
    match.finalize()
    assert match.nodes("eng") == 1


# 8
def test_several_nodes_and_a_class_change(match):
    match.gain("eng", 90)
    match.poll()
    passive_minutes(match, "eng", 2)
    match.idle(20)
    match.gain("eng", 50)  # second node, other phase
    match.poll()
    for _ in range(4):
        match.idle(36)
        match.gain("eng", 10)  # first node
        match.poll()
        match.idle(21)
        match.gain("eng", 10)  # second node
        match.poll()
    # Dies and comes back as rifleman: the nodes keep producing for their builder.
    match.die("eng")
    match.players["eng"].role = "rifleman"
    match.poll()
    match.spawn(["eng"], at=HQ)
    match.poll()
    for _ in range(4):
        match.idle(30)
        match.gain("eng", 10)
        match.poll()
        match.idle(24)
        match.gain("eng", 10)
        match.poll()
    assert match.nodes("eng") == 2


def test_synchronised_passive_ticks_of_several_nodes(match):
    match.gain("eng", 90)
    match.poll()
    match.idle(9)
    match.gain("eng", 90)
    match.poll()
    passive_minutes(match, "eng", 5, per_tick=20)
    assert match.nodes("eng") == 2


def test_passive_stream_alone_proves_a_node_missed_by_the_tracking(match):
    # Built before the tracking started (CRCON restart): only the passive production is visible.
    passive_minutes(match, "eng", 5)
    assert match.nodes("eng") == 1


def own_nodes(m, player_id, count):
    for _ in range(count):
        m.gain(player_id, 90)
        m.poll()
    passive_minutes(m, player_id, 3, per_tick=10 * count)


def test_a_player_cannot_have_more_than_3_nodes(match):
    own_nodes(match, "eng", 3)
    assert match.nodes("eng") == 3
    match.gain("eng", 50)
    decisions = match.poll()
    assert verdicts(decisions, "eng") == [("node", "none")]
    assert "already 3 nodes" in decisions[0].reason
    passive_minutes(match, "eng", 3, per_tick=30)
    assert match.nodes("eng") == 3


def test_a_team_cannot_have_more_than_9_nodes(match):
    engineers = ["eng", "eng2", "eng3"]
    match.add("eng2", role="engineer", unit_id=3)
    match.add("eng3", role="engineer", unit_id=4)
    match.add("eng4", role="engineer", unit_id=5)
    match.poll()
    for _ in range(3):  # each engineer builds 3 nodes, all producing every minute
        for engineer in engineers:
            match.gain(engineer, 90)
        match.poll()
    for _ in range(3):
        match.idle(57)
        for engineer in engineers:
            match.gain(engineer, 30)
        match.poll()
    assert [match.nodes(engineer) for engineer in engineers] == [3, 3, 3]
    match.gain("eng4", 90)
    decisions = match.poll()
    assert verdicts(decisions, "eng4") == [("node", "none")]
    assert "the team already has 9 nodes" in decisions[0].reason


def test_officer_outpost_income_is_not_a_node(match):
    passive_minutes(match, "off", 6)
    assert match.nodes("off") == 0


# 9
def test_reconnection_resets_the_reference(match):
    match.gain("off", 120)
    match.poll()
    match.remove("off")
    match.poll()
    match.add("off", role="officer", unit_id=0, support=0)
    match.poll()
    match.gain("off", 20)
    match.poll()
    assert match.garrisons("off") == 0


def test_score_reset_is_never_a_construction(match):
    match.gain("off", 40)
    match.poll()
    match.players["off"].support = 0
    decisions = match.poll()
    assert verdicts(decisions, "off") == [("counter", "reset")]
    match.gain("off", 10)
    match.poll()
    assert match.garrisons("off") == 0


def test_previous_match_stats_leaking_at_match_start(match):
    match.players["off"].support = 1250
    decisions = match.poll()
    assert verdicts(decisions, "off") == [("counter", "ignored")]
    match.players["off"].support = 0
    match.poll()
    assert match.garrisons("off") == 0


def test_large_unknown_gain_is_not_a_garrison(match):
    match.gain("off", 300)
    match.poll()
    assert match.garrisons("off") == 0


# 10
def test_garrison_built_during_a_data_gap_cannot_be_confirmed(match):
    match.gain("off", 50)
    decisions = match.poll(after=180)  # CRCON restart / RCON outage
    assert verdicts(decisions, "off") == [("garrison", "none")]
    assert "data gap" in decisions[0].reason
    match.idle(310)
    assert match.garrisons("off") == 0


def test_node_built_during_a_gap_is_found_by_its_passive_production(match):
    match.gain("eng", 90)
    match.poll(after=120)
    passive_minutes(match, "eng", 4)
    assert match.nodes("eng") == 1


# 11
def test_constructions_in_the_first_seconds_of_the_match():
    m = Match()
    m.add("off", role="officer")
    m.add("eng", role="engineer", unit_id=1)
    m.add("rifleman", unit_id=1)
    m.poll()  # first poll of the match: every score is 0
    m.gain("off", 50)
    m.gain("eng", 90)
    m.poll()
    confirm(m, "off", (500.0, 500.0), team=("rifleman",))
    passive_minutes(m, "eng", 2)
    assert m.garrisons("off") == 1
    assert m.nodes("eng") == 1


def test_construction_before_the_first_poll_is_not_invented():
    m = Match()
    m.add("off", role="officer", support=50)
    m.poll()
    m.idle(30)
    assert m.garrisons("off") == 0


# robustness
def test_a_replayed_poll_is_ignored(match):
    build(match, "off", GARRISON)
    assert match.engine.observe(match.snapshots[-1]) == []
    confirm(match, "off", GARRISON)
    assert match.engine.observe(match.snapshots[-1]) == []
    assert match.garrisons("off") == 1


def test_state_survives_a_json_round_trip(match):
    match.gain("eng", 90)
    build(match, "off", GARRISON)
    state = json.loads(json.dumps(match.engine.to_state()))
    match.engine = ActionsEngine(state)
    confirm(match, "off", GARRISON)
    passive_minutes(match, "eng", 3)
    assert match.nodes("eng") == 1
    assert match.garrisons("off") == 1


def test_snapshots_round_trip(match):
    snapshot = match.snapshots[-1]
    assert (
        PollSnapshot.from_dict(json.loads(json.dumps(snapshot.to_dict()))) == snapshot
    )


def test_finalize_is_final(match):
    build(match, "off", GARRISON)
    decisions = match.finalize()
    assert verdicts(decisions, "off") == [("garrison", "rejected")]
    assert match.poll() == []
    assert match.finalize() == []


def test_roles_are_configurable(match):
    match.engine.detectors["garrison"].roles = frozenset({"officer", "rifleman"})
    build(match, "a0", (1200.0, 1200.0))
    confirm(match, "a0", (1200.0, 1200.0), team=("b0",))
    assert match.garrisons("a0") == 1
    match.gain("cmd", 50)
    match.poll()
    candidates = match.engine.detectors["garrison"].candidates
    assert all(c["player_id"] != "cmd" for c in candidates)  # no longer analysed


# Simulated matches (tests/actions_simulation.py): error rates against a known ground truth.
from tests.actions_simulation import Simulation, score


def simulate(seeds, **kwargs):
    total = {}
    for seed in seeds:
        simulation = Simulation(seed, **kwargs)
        engine = ActionsEngine()
        simulation.run(engine)
        for key, value in score(engine, simulation.truth).items():
            total[key] = total.get(key, 0) + value
    return total


def test_simulated_matches_error_rates():
    result = simulate(range(12))
    # Garrisons nobody spawned on within 5 minutes are not countable, by design. Among the
    # others, misses come from spawn waves mixed with other gains of the builder.
    assert result["garrison_fp"] <= 1
    assert result["garrison_fn_used"] <= 0.05 * result["garrison_used"]
    assert result["node_fn"] <= 1
    assert result["node_fp"] <= 1  # repair station in the last 150 s of a match


def test_simulated_matches_with_an_outage():
    result = simulate(range(12), outage=(600, 690))
    assert result["garrison_fp"] <= 1
    # Garrisons built during the outage cannot be confirmed (their position is unknown).
    assert result["garrison_fn_used"] <= 0.11 * result["garrison_used"]
