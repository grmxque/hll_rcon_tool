"""Replay recorded polls through the actions engine.

    python -m rcon.actions_tracking replay <file.jsonl> [--player PLAYER_ID] [--all]
    python -m rcon.actions_tracking replay <file.jsonl> --trace <PLAYER_ID or name>
    python -m rcon.actions_tracking replay <file.jsonl> --spawns

The files are written by the ``record_actions_snapshots`` setting
($LOGGING_PATH/actions_snapshots). Prints each decision with its reason, then the actions per player
and the TOP 3 garrison builders. ``--spawns`` prints the spawn points instead, as they were classified
when last used.
"""

import argparse
import json
import math
import sys

from rcon.actions_tracking.engine import ActionsEngine
from rcon.actions_tracking.model import Decision, PollSnapshot, rank_players


def replay(
    path: str, roles: dict[str, list[str]], on_poll=None
) -> tuple[ActionsEngine, list[Decision]]:
    """Feed every poll of the file to a new engine; ``on_poll(snapshot, engine)`` after each."""
    engine = ActionsEngine(roles=roles)
    decisions = []
    with open(path, encoding="utf-8") as file:
        for line in file:
            if line.strip():
                snapshot = PollSnapshot.from_dict(json.loads(line))
                decisions += engine.observe(snapshot)
                if on_poll:
                    on_poll(snapshot, engine)
    decisions += engine.finalize()
    return engine, decisions


def tracer(wanted: str):
    """Print, at each poll, what the server reported for one player (ID or part of the name):
    position, deaths, role, support, and the jumps and spawns the tracking saw."""
    state = {"start": None, "previous": {}}

    def on_poll(snapshot: PollSnapshot, engine: ActionsEngine) -> None:
        state["start"] = state["start"] or snapshot.ts
        for sample in snapshot.players:
            if wanted != sample.player_id and wanted.lower() not in sample.name.lower():
                continue
            notes = []
            previous = state["previous"].get(sample.player_id)
            if previous is not None and sample.alive and previous.alive:
                moved = math.hypot(sample.x - previous.x, sample.y - previous.y) / 100
                if moved > 100:
                    notes.append(f"jump {moved:.0f} m")
            if previous is not None and sample.deaths > previous.deaths:
                notes.append("death +1")
            memory = engine.players.get(sample.player_id, {})
            if sample.alive and not memory.get("deployed", True):
                notes.append("not deployed (menus or body)")
            if any(
                s["player_id"] == sample.player_id
                for s in engine.spawns.at(snapshot.ts)
            ):
                notes.append("SPAWN detected")
            position = (
                f"({sample.x:9.0f}, {sample.y:9.0f}, {sample.z:7.0f})"
                if sample.alive
                else "(0, 0, 0) dead/deploying"
            )
            print(
                f"+{snapshot.ts - state['start']:7.1f}s {sample.name} {position} "
                f"deaths={sample.deaths} role={sample.role} support={sample.support} "
                + " ".join(notes)
            )
            state["previous"][sample.player_id] = sample

    return on_poll


def spawn_points_tracer():
    """Keep the last state of every spawn point, including those forgotten since."""
    seen: dict[int, dict] = {}

    def on_poll(snapshot: PollSnapshot, engine: ActionsEngine) -> None:
        for point in engine.points.points:
            seen[point["id"]] = dict(point)

    return seen, on_poll


def print_spawn_points(points: dict[int, dict], start: float | None, names) -> None:
    start = start or 0
    for point in sorted(points.values(), key=lambda p: (p["team"], p["first_ts"])):
        credited = ", ".join(
            f"{names.get(pid, pid)} {count}" for pid, count in point["credited"].items()
        )
        print(
            f"#{point['id']:<3} {point['team']:<6} {point['kind']:<8} "
            f"({point['x']:9.0f}, {point['y']:9.0f}) "
            f"+{point['first_ts'] - start:6.0f}s..+{point['last_ts'] - start:6.0f}s "
            f"spawns={point['spawns']} squads={point['squads']} "
            f"credited=[{credited}] uncredited={point['uncredited']}: {point['reason']}"
        )


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m rcon.actions_tracking")
    sub = parser.add_subparsers(dest="command", required=True)
    replay_parser = sub.add_parser("replay", help="replay a JSONL snapshot file")
    replay_parser.add_argument("file")
    replay_parser.add_argument(
        "--player", help="only show the decisions of this player ID"
    )
    replay_parser.add_argument(
        "--all", action="store_true", help="also show resets and ignored counters"
    )
    replay_parser.add_argument(
        "--garrison-roles", default="officer,armycommander", help="comma separated"
    )
    replay_parser.add_argument(
        "--node-roles", default="engineer", help="comma separated"
    )
    replay_parser.add_argument(
        "--trace",
        help="print every poll of this player (ID or part of the name): position, deaths…",
    )
    replay_parser.add_argument(
        "--spawns",
        action="store_true",
        help="print the spawn points: kind, squads, credits",
    )
    args = parser.parse_args(argv)

    points, on_points = spawn_points_tracer()
    engine, decisions = replay(
        args.file,
        {
            "garrison": args.garrison_roles.split(","),
            "node": args.node_roles.split(","),
        },
        on_poll=tracer(args.trace)
        if args.trace
        else on_points
        if args.spawns
        else None,
    )
    if args.trace:
        return 0
    if args.spawns:
        print_spawn_points(points, engine.first_ts, engine.names)
        return 0
    for decision in decisions:
        if args.player and decision.player_id != args.player:
            continue
        if decision.kind == "counter" and not args.all:
            continue
        print(
            f"{decision.ts:.0f} {decision.kind:<8} {decision.verdict:<9} "
            f"{decision.name} ({decision.player_id}) +{decision.gain}: {decision.reason}"
        )
        if decision.details:
            print(f"    {json.dumps(decision.details)}")

    summary = engine.summary()
    print("\nActions:")
    for player in sorted(summary.values(), key=lambda p: (p.name, p.player_id)):
        counts = ", ".join(
            f"{action}: {count}" for action, count in sorted(player.counts.items())
        )
        print(f"  {player.name} ({player.player_id}): {counts}")
    top = rank_players(summary, "garrison")[:3]
    print(
        "\nTOP 3 garrisons: "
        + ", ".join(f"{p.name} ({p.count('garrison')})" for p in top)
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
