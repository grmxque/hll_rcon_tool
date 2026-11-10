"""Replay recorded polls through the actions engine.

    python -m rcon.actions_tracking replay <file.jsonl> [--player PLAYER_ID] [--all]

The files are written by the ``record_actions_snapshots`` setting
($LOGGING_PATH/actions_snapshots). Prints each decision with its reason, then the actions per player
and the TOP 3 garrison builders.
"""

import argparse
import json
import sys

from rcon.actions_tracking.engine import ActionsEngine
from rcon.actions_tracking.model import Decision, PollSnapshot, rank_players


def replay(
    path: str, roles: dict[str, list[str]]
) -> tuple[ActionsEngine, list[Decision]]:
    engine = ActionsEngine(roles=roles)
    decisions = []
    with open(path, encoding="utf-8") as file:
        for line in file:
            if line.strip():
                decisions += engine.observe(PollSnapshot.from_dict(json.loads(line)))
    decisions += engine.finalize()
    return engine, decisions


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
        "--garrison-roles", default="officer,squadleader", help="comma separated"
    )
    replay_parser.add_argument(
        "--node-roles", default="engineer", help="comma separated"
    )
    args = parser.parse_args(argv)

    engine, decisions = replay(
        args.file,
        {
            "garrison": args.garrison_roles.split(","),
            "node": args.node_roles.split(","),
        },
    )
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
