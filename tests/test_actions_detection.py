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


def build_known_garrison(m, officer="off", at=GARRISON):
    """The officer builds a garrison at ``at`` (clean +50), then walks away."""
    m.move(officer, at)
    m.poll()
    m.gain(officer, 50)
    m.poll()
    m.move(officer, (at[0] + 200, at[1] + 200))
    m.poll()


# 1
def test_garrison_with_exactly_the_construction_bonus(match):
    match.gain("off", 50)
    decisions = match.poll()
    assert verdicts(decisions, "off") == [("garrison", "counted")]
    assert match.garrisons("off") == 1


def test_small_spawn_credits_are_not_a_garrison(match):
    match.die("a0", "a1", "a2")
    match.poll()
    match.spawn(["a0", "a1", "a2"], at=(950.0, 950.0))  # the officer's outpost
    match.gain("off", 15)
    match.poll()
    assert match.garrisons("off") == 0


# 2
def test_garrison_with_simultaneous_spawn_credits(match):
    match.die("a0", "b0")
    match.poll()
    match.spawn(["a0"], at=(950.0, 950.0))
    match.spawn(["b0"], at=(950.0, 950.0))
    match.gain("off", 58)  # 50 + 5 (squad member) + 3 (teammate)
    decisions = match.poll()
    assert verdicts(decisions, "off") == [("garrison", "counted")]
    assert match.garrisons("off") == 1


# 3
def test_big_spawn_wave_on_a_known_garrison_is_not_a_new_garrison(match):
    build_known_garrison(match)
    squad = [f"a{i}" for i in range(10)]
    match.die(*squad)
    match.poll()
    match.spawn(squad, at=GARRISON)
    match.gain("off", 50)  # 10 squad members x 5: same value as a garrison
    decisions = match.poll()
    assert verdicts(decisions, "off") == [("garrison", "none")]
    assert match.garrisons("off") == 1  # only the first one


def test_spawn_wave_while_the_officer_is_dead(match):
    squad = [f"a{i}" for i in range(10)]
    match.die("off", *squad)
    match.poll()
    match.spawn(squad, at=(2000.0, 2000.0))  # spawn point never observed before
    match.gain("off", 50)
    match.poll()
    assert match.garrisons("off") == 0


def test_ambiguous_wave_while_moving_waits_then_defaults_to_no_garrison(match):
    squad = [f"a{i}" for i in range(10)]
    match.die(*squad)
    match.poll()
    match.spawn(squad, at=(2000.0, 2000.0))
    match.move("off", (930.0, 930.0))  # running: 30 m in 3 s
    match.gain("off", 50)
    decisions = match.poll()
    assert verdicts(decisions, "off") == [("garrison", "pending")]
    match.idle(200)
    assert match.garrisons("off") == 0
    assert ("garrison", "rejected") in verdicts(match.decisions, "off")


def test_ambiguous_wave_confirmed_by_later_spawns_at_the_build_position(match):
    squad = [f"a{i}" for i in range(10)]
    match.die(*squad)
    match.poll()
    match.spawn(squad, at=(2000.0, 2000.0))
    match.move("off", (930.0, 930.0))
    match.gain("off", 50)
    match.poll()
    match.die("b0", "b1")
    match.poll()
    match.spawn(["b0", "b1"], at=(930.0, 930.0))  # teammates use the new garrison
    match.gain("off", 6)
    match.poll()
    assert match.garrisons("off") == 1
    assert ("garrison", "confirmed") in verdicts(match.decisions, "off")


def test_ambiguous_wave_rejected_when_its_spawn_point_turns_out_to_be_the_officers(
    match,
):
    outpost = (2000.0, 2000.0)
    squad = [f"a{i}" for i in range(10)]
    match.die(*squad)
    match.poll()
    match.spawn(squad, at=outpost)
    match.move("off", (930.0, 930.0))
    match.gain("off", 50)
    match.poll()
    # Later, a small unambiguous credit from the same spot reveals the officer's outpost.
    match.die("a0", "a1")
    match.poll()
    match.spawn(["a0", "a1"], at=outpost)
    match.gain("off", 10)
    match.poll()
    match.idle(200)
    assert match.garrisons("off") == 0
    [rejected] = [d for d in match.decisions if d.verdict == "rejected"]
    assert "spawn point" in rejected.reason
    assert rejected.ts < 1_000 + 60  # settled by the evidence, not by the deadline


# 4
def test_garrison_built_during_a_spawn_wave(match):
    build_known_garrison(match)
    build_known_garrison(match, officer="off2", at=(300.0, 600.0))
    match.move("off", (1200.0, 1200.0))
    match.poll()
    squad_b = [f"b{i}" for i in range(15)]
    match.die(*squad_b, "a0", "a1", "a2", "a3")
    match.poll()
    match.spawn(squad_b, at=(300.0, 600.0))  # on off2's garrison
    match.spawn(["a0", "a1", "a2", "a3"], at=GARRISON)  # on off's first garrison
    match.gain("off2", 75)
    match.gain("off", 50 + 20)  # new garrison + 4 squad members on the first one
    decisions = match.poll()
    assert verdicts(decisions, "off") == [("garrison", "counted")]
    assert match.garrisons("off") == 2
    assert (
        match.garrisons("off2") == 1
    )  # the wave on its garrison is not a construction


def test_wave_on_another_builders_spawn_point_does_not_hide_a_garrison(match):
    build_known_garrison(match, officer="off2", at=(300.0, 600.0))
    squad_b = [f"b{i}" for i in range(15)]
    match.die(*squad_b)
    match.poll()
    match.spawn(squad_b, at=(300.0, 600.0))
    match.gain("off2", 75)
    match.gain("off", 50)  # 15 teammates x 3 + 5 would also give 50
    match.poll()
    assert match.garrisons("off") == 1
    assert match.garrisons("off2") == 1


# 5
def test_several_garrisons_by_the_same_officer(match):
    for index, position in enumerate(
        [(900.0, 900.0), (1100.0, 900.0), (1100.0, 1100.0)]
    ):
        match.move("off", position)
        match.poll()
        match.gain("off", 50)
        match.poll()
        match.idle(30)
    assert match.garrisons("off") == 3
    assert match.engine.summary()["off"].reached["garrison"] is not None


def test_second_garrison_within_200m_seconds_later_is_impossible(match):
    match.gain("off", 50)
    match.poll()
    match.move("off", (905.0, 905.0))
    match.gain("off", 50)
    decisions = match.poll()
    assert match.garrisons("off") == 1
    [decision] = [d for d in decisions if d.player_id == "off"]
    assert decision.verdict == "none"
    assert decision.reason.startswith("physically impossible")


def test_one_garrison_at_most_between_two_polls(match):
    match.gain(
        "off", 100
    )  # a second one would need 50 more supplies and a spot 200 m away
    match.poll()
    assert match.garrisons("off") <= 1


def test_another_garrison_200m_away(match):
    match.gain("off", 50)
    match.poll()
    match.idle(15)
    match.move("off", (1150.0, 900.0))  # 250 m away
    match.poll()
    match.gain("off", 50)
    match.poll()
    assert match.garrisons("off") == 2


def test_garrison_rebuilt_at_the_same_place_after_its_destruction(match):
    match.gain("off", 50)
    match.poll()
    match.idle(300)
    match.gain("off", 50)
    decisions = match.poll()
    assert match.garrisons("off") == 2
    assert "replaces a friendly garrison" in decisions[0].reason


def test_ambiguous_gain_near_a_standing_friendly_garrison_is_spawn_credits(match):
    build_known_garrison(match, officer="off2", at=(950.0, 950.0))
    match.idle(90)
    squad = [f"a{i}" for i in range(10)]
    match.die(*squad)
    match.poll()
    match.spawn(squad, at=(2000.0, 2000.0))  # spawn point never observed before
    match.gain("off", 50)  # at (900, 900): 70 m from off2's garrison
    decisions = match.poll()
    assert verdicts(decisions, "off") == [("garrison", "none")]
    assert "already stands within 200 m" in decisions[0].reason
    assert match.garrisons("off") == 0


def test_gap_cannot_hide_more_garrisons_than_physically_possible(match):
    match.gain("off", 200)
    decisions = match.poll(after=40)
    assert match.garrisons("off") <= 2  # 40 s: at most one garrison every 30 s
    assert decisions[0].details["most_garrisons_possible"] == 2


# 6
def test_commander_abilities_are_not_constructions(match):
    for points in (50, 100, 75, 50, 50):
        match.gain("cmd", points)
        match.poll()
        match.idle(30)
    assert match.garrisons("cmd") == 0
    assert match.nodes("cmd") == 0


def test_commander_ability_waits_for_spawns_then_is_rejected(match):
    match.gain("cmd", 50)
    decisions = match.poll()
    assert verdicts(decisions, "cmd") == [("garrison", "pending")]
    assert "commander abilities give +50 too" in decisions[0].reason
    match.idle(200)
    assert match.garrisons("cmd") == 0
    assert ("garrison", "rejected") in verdicts(match.decisions, "cmd")


def test_commander_garrison_confirmed_by_teammates_spawning_on_it(match):
    match.move("cmd", (1300.0, 1300.0))
    match.poll()
    match.gain("cmd", 50)
    match.poll()
    squad_b = ["b0", "b1", "b2", "b3"]
    match.die(*squad_b)
    match.idle(15)
    match.spawn(squad_b, at=(1302.0, 1302.0))
    match.gain("cmd", 12)  # +3 per teammate spawning on the commander's garrison
    match.poll()
    assert match.garrisons("cmd") == 1
    assert ("garrison", "confirmed") in verdicts(match.decisions, "cmd")


def test_commander_at_the_hq_is_not_confirmed_by_hq_spawns(match):
    match.move("cmd", HQ)
    match.poll()
    match.gain("cmd", 50)  # ability
    match.poll()
    squad_b = [f"b{i}" for i in range(10)]
    match.die(*squad_b)
    match.idle(15)
    match.spawn(squad_b, at=HQ)  # HQ spawns credit nobody
    match.poll()
    match.die(*squad_b)
    match.idle(15)
    match.spawn(squad_b, at=HQ)
    match.gain("cmd", 50)  # another ability during the wave
    match.poll()
    match.idle(200)
    assert match.garrisons("cmd") == 0


def test_commander_ability_on_his_own_garrison_is_not_a_second_one(match):
    match.move("cmd", (1300.0, 1300.0))
    match.poll()
    match.gain("cmd", 50)  # real garrison
    match.poll()
    match.idle(20)
    match.gain("cmd", 50)  # ability, standing on it
    decisions = match.poll()
    assert verdicts(decisions, "cmd") == [("garrison", "none")]
    squad_b = ["b0", "b1", "b2"]
    match.die(*squad_b)
    match.idle(15)
    match.spawn(squad_b, at=(1301.0, 1301.0))
    match.gain("cmd", 9)
    match.poll()
    assert match.garrisons("cmd") == 1


def test_outpost_passive_income_is_not_a_garrison(match):
    for _ in range(8):
        match.idle(57)
        match.gain("off", 10)
        match.poll()
    assert match.garrisons("off") == 0


def test_other_team_is_independent(match):
    match.gain("axis_off", 50)
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
def test_long_gap_credits_only_what_spawns_cannot_explain(match):
    match.gain("off", 100)
    match.poll(after=180)  # CRCON restart / RCON outage
    assert (
        match.garrisons("off") == 1
    )  # 100 - 75 (outpost and marking over 3 min) needs one


def test_long_gap_with_many_deaths_explains_the_gain(match):
    match.die(*[f"b{i}" for i in range(15)])
    match.spawn([f"b{i}" for i in range(15)], at=(300.0, 300.0))
    match.gain("off", 50)
    decisions = match.poll(after=180)
    assert verdicts(decisions, "off") == [("garrison", "none")]
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
    m.poll()  # first poll of the match: every score is 0
    m.gain("off", 50)
    m.gain("eng", 90)
    m.poll()
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
    match.gain("off", 50)
    match.poll()
    assert match.engine.observe(match.snapshots[-1]) == []
    assert match.garrisons("off") == 1


def test_state_survives_a_json_round_trip(match):
    match.gain("eng", 90)
    match.poll()
    state = json.loads(json.dumps(match.engine.to_state()))
    match.engine = ActionsEngine(state)
    passive_minutes(match, "eng", 3)
    match.gain("off", 50)
    match.poll()
    assert match.nodes("eng") == 1
    assert match.garrisons("off") == 1


def test_snapshots_round_trip(match):
    snapshot = match.snapshots[-1]
    assert (
        PollSnapshot.from_dict(json.loads(json.dumps(snapshot.to_dict()))) == snapshot
    )


def test_finalize_is_final(match):
    match.finalize()
    match.gain("off", 50)
    assert match.poll() == []
    assert match.finalize() == []


def test_roles_are_configurable(match):
    match.engine.detectors["garrison"].roles = frozenset({"officer", "rifleman"})
    match.gain("a0", 50)
    match.poll()
    assert match.garrisons("a0") == 1
    match.gain("cmd", 50)
    match.poll()
    assert (
        match.engine.detectors["garrison"].pending == []
    )  # commander no longer analysed


def test_gap_uses_the_income_observed_before_it(match):
    # 5 minutes of outpost income (10 pts/min), then a 2 min gap with a garrison.
    passive_minutes(match, "off", 5)
    match.gain("off", 50 + 20)
    decisions = match.poll(after=120)
    assert verdicts(decisions, "off") == [("garrison", "counted")]
    assert decisions[0].details["allowance_basis"].endswith("observed before the gap")


def test_gap_after_heavy_spawn_income_is_not_a_garrison(match):
    squad = [f"a{i}" for i in range(10)]
    for _ in range(
        8
    ):  # the squad keeps spawning on the officer's outpost: 50 pts every 18 s
        match.die(*squad)
        match.idle(15)
        match.spawn(squad, at=(950.0, 950.0))
        match.gain("off", 50)
        match.poll()
    # 40 s outage: two more waves, only visible through the death counters.
    for player_id in squad:
        match.players[player_id].deaths += 2
    match.gain("off", 100)
    decisions = match.poll(after=40)
    assert verdicts(decisions, "off") == [("garrison", "none")]
    assert match.garrisons("off") == 0
    rejected = [d for d in match.decisions if d.verdict == "rejected"]
    assert rejected and "same spawn point" in rejected[0].reason


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
    garrisons = result["garrison_tp"] + result["garrison_fn"]
    # False positives: a commander ability used during an HQ wave right next to him (about one
    # match in ten). Misses: garrisons destroyed and rebuilt in place within 30 s, commander
    # garrisons nobody spawned on.
    assert result["garrison_fp"] <= 0.02 * garrisons
    assert result["garrison_fn"] <= 0.08 * garrisons
    assert result["node_fn"] == 0
    assert result["node_fp"] <= 1  # repair station in the last 150 s of a match


def test_simulated_matches_with_an_outage():
    result = simulate(range(12), outage=(600, 690))
    garrisons = result["garrison_tp"] + result["garrison_fn"]
    assert result["garrison_fp"] <= 0.02 * garrisons
    assert result["garrison_fn"] <= 0.09 * garrisons
