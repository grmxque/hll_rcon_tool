"""Runs the actions engine on the player polls of the log loop and persists it in Redis.

``observe_poll`` is called by ``LogLoop.update_maps_history`` right after it polled
``get_detailed_players`` (no extra RCON call). The engine state is stored per server and per match
(map start timestamp), so a service restart resumes the match (the missing polls are handled as a data
gap) and a new match starts from scratch.
"""

import json
import logging
import os
import time
from pathlib import Path

from rcon.actions_tracking.engine import ActionsEngine
from rcon.actions_tracking.model import (
    Decision,
    PlayerActions,
    PlayerSample,
    PollSnapshot,
)
from rcon.cache_utils import get_redis_client
from rcon.types import GetDetailedPlayers, MapInfo
from rcon.user_config.end_of_game_rewards import EndOfGameRewardsUserConfig
from rcon.utils import get_server_number

logger = logging.getLogger(__name__)

STATE_TTL = 6 * 60 * 60
MAX_STORED_DECISIONS = 2_000
SNAPSHOT_FILES_KEPT = 20
CONFIG_CACHE_SECONDS = 15.0

_config_cache: tuple[float, EndOfGameRewardsUserConfig] | None = None


def load_config() -> EndOfGameRewardsUserConfig:
    """The settings, reloaded at most every 15 s: ``observe_poll`` runs every 3 to 4 s."""
    global _config_cache
    now = time.monotonic()
    if _config_cache is None or now - _config_cache[0] > CONFIG_CACHE_SECONDS:
        _config_cache = (now, EndOfGameRewardsUserConfig.load_from_db())
    return _config_cache[1]


def state_key(map_start: int) -> str:
    return f"actions_tracking:{get_server_number()}:{map_start}"


def decisions_key(map_start: int) -> str:
    return f"{state_key(map_start)}:decisions"


def make_engine(
    config: EndOfGameRewardsUserConfig, state: dict | None
) -> ActionsEngine:
    return ActionsEngine(
        state,
        roles={
            "garrison": config.garrison_builder_roles,
            "node": config.node_builder_roles,
        },
    )


def snapshot_from_players(dp: GetDetailedPlayers, ts: float) -> PollSnapshot:
    samples = []
    for player_id, player in dp["players"].items():
        position = player.get("world_position") or {}

        def counter(key: str, player=player) -> int:
            return int(player.get(key) or 0)

        samples.append(
            PlayerSample(
                player_id=player_id,
                name=player.get("name") or player_id,
                team=player.get("team"),
                unit_id=player.get("unit_id"),
                role=player.get("role"),
                support=counter("support"),
                deaths=counter("deaths"),
                x=float(position.get("x") or 0),
                y=float(position.get("y") or 0),
                z=float(position.get("z") or 0),
                combat=counter("combat"),
                offense=counter("offense"),
                defense=counter("defense"),
                kills=counter("kills"),
                team_kills=counter("team_kills"),
                vehicle_kills=counter("vehicle_kills"),
                vehicles_destroyed=counter("vehicles_destroyed"),
                loadout=player.get("loadout"),
                level=counter("level"),
            )
        )
    return PollSnapshot(ts=ts, players=tuple(samples))


def _load_state(red, map_start: int) -> dict | None:
    raw = red.get(state_key(map_start))
    return json.loads(raw) if raw else None


def _save_state(red, map_start: int, engine: ActionsEngine) -> None:
    red.set(state_key(map_start), json.dumps(engine.to_state()), ex=STATE_TTL)


def _report(red, map_start: int, decisions: list[Decision]) -> None:
    """Every decision goes to the log and to a bounded Redis list (diagnostics)."""
    if not decisions:
        return
    key = decisions_key(map_start)
    pipe = red.pipeline()
    for decision in decisions:
        logger.info(
            "Action %s %s: %s %s (+%s in %ss) %s %s",
            decision.kind,
            decision.verdict,
            decision.name,
            decision.player_id,
            decision.gain,
            None if decision.interval is None else round(decision.interval, 1),
            decision.reason,
            json.dumps(decision.details) if decision.details else "",
        )
        pipe.rpush(key, json.dumps(decision.to_dict()))
    pipe.ltrim(key, -MAX_STORED_DECISIONS, -1)
    pipe.expire(key, STATE_TTL)
    pipe.execute()


def snapshots_dir() -> Path:
    return Path(os.getenv("LOGGING_PATH") or ".") / "actions_snapshots"


def _record_snapshot(map_start: int, snapshot: PollSnapshot) -> None:
    """Append the poll to this match's JSONL file, keeping the last 20 files."""
    directory = snapshots_dir()
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{get_server_number()}_{map_start}.jsonl"
    is_new = not path.exists()
    with path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(snapshot.to_dict()) + "\n")
    if is_new:
        files = sorted(
            directory.glob(f"{get_server_number()}_*.jsonl"),
            key=lambda p: p.stat().st_mtime,
        )
        for old in files[:-SNAPSHOT_FILES_KEPT]:
            old.unlink(missing_ok=True)


def observe_poll(
    rcon, current_map: MapInfo, dp: GetDetailedPlayers, now: float | None = None
) -> None:
    """Feed one player poll of the running match to the detector. Never raises."""
    try:
        config = load_config()
        if not config.actions_tracking_enabled:
            return
        map_start = current_map.get("start")
        if not map_start or current_map.get("end") is not None or not dp.get("players"):
            return
        snapshot = snapshot_from_players(dp, now if now is not None else time.time())
        if config.record_actions_snapshots:
            try:
                _record_snapshot(map_start, snapshot)
            except OSError:
                logger.exception("Actions tracking: could not record the snapshot")
        red = get_redis_client()
        engine = make_engine(config, _load_state(red, map_start))
        decisions = engine.observe(snapshot)
        _save_state(red, map_start, engine)
        _report(red, map_start, decisions)
        if config.thanks_banner_enabled and any(d.counted for d in decisions):
            from rcon.actions_tracking.banner import send_thanks_banner

            send_thanks_banner(rcon, config, engine.summary())
    except Exception:
        logger.exception("Actions tracking: could not process the poll")


def final_actions(
    config: EndOfGameRewardsUserConfig, map_start: int | None, end_ts: float
) -> dict[str, PlayerActions]:
    """Settle the pending cases of the match and return the actions per player."""
    if not map_start or not config.actions_tracking_enabled:
        return {}
    red = get_redis_client()
    state = _load_state(red, map_start)
    if state is None:
        logger.warning(
            "Actions tracking: no data for the match started at %s", map_start
        )
        return {}
    engine = make_engine(config, state)
    decisions = engine.finalize(end_ts)
    _save_state(red, map_start, engine)
    _report(red, map_start, decisions)
    return engine.summary()


def stored_decisions(map_start: int) -> list[dict]:
    return [
        json.loads(d)
        for d in get_redis_client().lrange(decisions_key(map_start), 0, -1)
    ]
