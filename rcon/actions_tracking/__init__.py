"""Detection of player actions that the game server does not report (constructions, …).

The game server only exposes counters per player (scores, kills, deaths…), its role and its position.
``ActionsEngine`` follows them poll by poll and lets one detector per action (``detectors/``) decide
which actions explain the changes. See docs/actions-tracking.md.
"""
