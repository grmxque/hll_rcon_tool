# Archived copy of the previous plugin (https://github.com/grmxque/HLL_CRCON_Top_stats), kept for reference only.
# ruff: noqa
# fmt: off
import logging

from rcon.rcon import Rcon, StructuredLogLineWithMetaData
from rcon.scoreboard import get_cached_live_game_stats
from rcon.utils import get_server_number
from typing import List, Dict, Tuple
from datetime import datetime, timedelta, timezone

logger = logging.getLogger('rcon')

# Global dictionary to store VIP days for players
player_vip_days: Dict[str, Tuple[int, str]] = {}

# Global list to store connected players IDs
connected_player_ids: List[str] = []

# Global list to store current vip players
current_vips: List[Dict] = []

# Should we display the stats to every player on match end ?
# True or False
ENABLED = True
# Can be enabled/disabled on your different game servers
# ie : ["1"]           = enabled only on server 1
#      ["1", "2"]      = enabled on servers 1 and 2
#      ["2", "4", "5"] = enabled on servers 2, 4 and 5
ENABLE_ON_SERVERS = ["1"]

# Configuration of VIP reward
BOT_NAME = "Top stats VIP"
# Minimum of players to enable vips reward. Should match your server seeding rules.
MIN_PLAYERS_FOR_VIP_GAME_SCORES = 50
# Days of VIP earned when player in top game scores
GAME_TOP_SCORES_VIP_DAYS_REWARD = 4
# Days of VIP earned when player score
INDIVIDUAL_SCORES_VIP_DAYS_REWARD = 2
# Days of VIP earned for fidelity
FIDELITY_VIP_DAYS_REWARD = 0

# Threshold values for player score
THRESHOLD_KILLS = 70
THRESHOLD_KILLSTREAK = 10
THRESHOLD_KPM = 0.75
MIN_KILLS_FOR_KDR_VIP = 30
THRESHOLD_KDR = 1.5

# Translations
# Available : 0 for english, 1 for french
LANG = 0

# ----------------------------------------------
TRANSL = {
    "end_of_game": ["END OF GAME", "FIN DE PARTIE"],
    "best_scores": ["BEST SCORES", "MEILLEURS SCORES"],
    "offense": ["OFFENSE", "ATTAQUE"],
    "defense": ["DEFENSE", "DÉFENSE"],
    "support": ["SUPPORT", "SUPPORT"],
    "combat": ["COMBAT", "COMBAT"],
    "kills": ["KILLS", "KILLS"],
    "kills_streak": ["KILLS STREAK", "SÉRIE DE KILLS"],
    "kills_per_minute": ["KILLS/MINUTE", "KILLS/MINUTE"],
    "kill_death_ratio": ["KILLS/DEATHS", "KILLS/MORTS"],
    "days_of_vip": ["DAYS OF VIP", "JOURS DE VIP"],
    "you_won": ["YOU WON", "TU AS GAGNÉ"]
}

def refresh_connected_players(rcon):
    try:
        connected_player_ids.clear()
        connected_player_ids.extend(
            [p[1] for p in rcon.get_player_ids() if isinstance(p, (list, tuple)) and len(p) > 1]
        )
        logger.info(f"Connected players list refreshed: {len(connected_player_ids)} online.")
    except Exception as e:
        logger.error(f"Error refreshing connected players list: {e}")
        connected_player_ids.clear()

def refresh_current_vips(rcon):
    try:
        current_vips.clear()
        vips = rcon.get_vip_ids()
        current_vips.extend(
            [v for v in vips if isinstance(v, dict) and "player_id" in v]
        )
        logger.info(f"Current VIP list refreshed: {len(current_vips)} VIPs loaded.")
    except Exception as e:
        logger.error(f"Error refreshing current VIP list: {e}")
        current_vips.clear()

def is_online(player_id):
    if not player_id:
        return False
    return player_id in connected_player_ids

def is_vip(player_id):
    if not player_id:
        return False
    return any(v.get("player_id") == player_id for v in current_vips)

def get_vip_info(player_id):
    if not player_id:
        return None

    for v in current_vips:
        if v.get("player_id") == player_id:
            return v

    return None

def is_unlimited_vip(vip_expiration):
    if vip_expiration is None:
        return True

    unlimited_date = "3000-01-01T00:00:00+00:00"

    if isinstance(vip_expiration, str):
        return vip_expiration == unlimited_date
    elif isinstance(vip_expiration, datetime):
        try:
            return vip_expiration == datetime.fromisoformat(unlimited_date)
        except ValueError:
            return False
    return False

def register_vip_reward(player, vip_days_to_add):
    player_id = player.get("player_id")
    player_name = player.get("player")
    if player_id:
        current_days = player_vip_days.get(player_id, (0, player_name))[0]
        player_vip_days[player_id] = (current_days + vip_days_to_add, player_name)

def ensure_datetime(expiration, player_id):
    if isinstance(expiration, datetime):
        return expiration
    try:
        return datetime.fromisoformat(str(expiration))
    except Exception as e:
        logger.error(f"Failed to convert expiration for player {player_id}: {e}")
        return None

def handle_new_vip(rcon, player_id, combined_name, hours_to_add, now):
    expiration = now + timedelta(hours=hours_to_add)
    expiration = ensure_datetime(expiration, player_id)
    if expiration:
        rcon.add_vip(player_id, combined_name, expiration.isoformat())

def parse_expiration(vip_expiration, player_id):
    if isinstance(vip_expiration, datetime):
        return vip_expiration
    elif isinstance(vip_expiration, str):
        try:
            return datetime.fromisoformat(vip_expiration)
        except ValueError as e:
            logger.error(f"Invalid expiration date format for player {player_id}: {e}")
            return None
    else:
        logger.warning(f"Unsupported type for vip_expiration for player {player_id}: {type(vip_expiration)}")
        return None

def handle_existing_vip(rcon, player_id, combined_name, hours_to_add, existing_vip):
    current_expiration = parse_expiration(existing_vip.get('vip_expiration'), player_id)
    if current_expiration:
        new_expiration = current_expiration + timedelta(hours=hours_to_add)
        rcon.add_vip(player_id, combined_name, new_expiration.isoformat())

def process_vips_reward(rcon):
    logger.info(f"Processing VIP reward for {len(player_vip_days)} players")
    now = datetime.now(timezone.utc)

    for player_id, (days_to_add, player_name) in player_vip_days.items():
        hours_to_add = days_to_add * 24
        combined_name = f"{player_name} - {BOT_NAME}"

        vip_status = is_vip(player_id)
        if not vip_status:
            handle_new_vip(rcon, player_id, combined_name, hours_to_add, now)
            continue

        vip_data = get_vip_info(player_id)
        if vip_data and not is_unlimited_vip(vip_data.get('vip_expiration')):
            handle_existing_vip(rcon, player_id, combined_name, hours_to_add, vip_data)

    player_vip_days.clear()

def get_top_player_for_category(players, category):
    return max(players, key=lambda x: x.get(category, 0))

def get_game_top_scores(rcon, players):
    is_vip_reward_enabled = should_enable_vip_reward(rcon)
    categories = ["offense", "defense", "support", "combat"]
    message = f"▒ {TRANSL['best_scores'][LANG]} ▒\n"

    if is_vip_reward_enabled:
        message += f"+{GAME_TOP_SCORES_VIP_DAYS_REWARD} {TRANSL['days_of_vip'][LANG]} / TOP\n"
    
    for category in categories:
        winner = get_top_player_for_category(players, category)
        winner_name = winner.get("player")
        score = winner.get(category, 0)
        message += f"Top {TRANSL[category][LANG]} : {winner_name} ({score})\n"
        if is_vip_reward_enabled:
            register_vip_reward(winner, GAME_TOP_SCORES_VIP_DAYS_REWARD)

    return message

def get_individual_scores_message(player):
    player_id = player.get("player_id")
    player_name = player.get("player")
    kills = player.get("kills", 0)
    ks = player.get("kills_streak", 0)
    kpm = player.get("kills_per_minute", 0)
    kdr = player.get("kill_death_ratio", 0)

    message = f"▒ {player_name} ▒\n"

    if is_vip(player_id):
        vip_days = FIDELITY_VIP_DAYS_REWARD
    else:
        vip_days = 0

    if kills >= THRESHOLD_KILLS:
        vip_days += INDIVIDUAL_SCORES_VIP_DAYS_REWARD
        message += f"{TRANSL['kills'][LANG]} : {kills} (+{INDIVIDUAL_SCORES_VIP_DAYS_REWARD} {TRANSL['days_of_vip'][LANG]})\n"
    else:
        message += f"{TRANSL['kills'][LANG]} : {kills}\n"

    if ks >= THRESHOLD_KILLSTREAK:
        vip_days += INDIVIDUAL_SCORES_VIP_DAYS_REWARD
        message += f"{TRANSL['kills_streak'][LANG]} : {ks} (+{INDIVIDUAL_SCORES_VIP_DAYS_REWARD} {TRANSL['days_of_vip'][LANG]})\n"
    else:
        message += f"{TRANSL['kills_streak'][LANG]} : {ks}\n"

    if kpm >= THRESHOLD_KPM:
        vip_days += INDIVIDUAL_SCORES_VIP_DAYS_REWARD
        message += f"{TRANSL['kills_per_minute'][LANG]} : {kpm} (+{INDIVIDUAL_SCORES_VIP_DAYS_REWARD} {TRANSL['days_of_vip'][LANG]})\n"
    else:
        message += f"{TRANSL['kills_per_minute'][LANG]} : {kpm}\n"

    if kills >= MIN_KILLS_FOR_KDR_VIP and kdr >= THRESHOLD_KDR:
        vip_days += INDIVIDUAL_SCORES_VIP_DAYS_REWARD
        message += f"{TRANSL['kill_death_ratio'][LANG]} : {kdr} (+{INDIVIDUAL_SCORES_VIP_DAYS_REWARD} {TRANSL['days_of_vip'][LANG]})\n"
    else:
        message += f"{TRANSL['kill_death_ratio'][LANG]} : {kdr}\n"

    if vip_days > 0:
        register_vip_reward(player, vip_days)
    
    return message
    
def get_player_congratulations_message(player):
    player_id = player.get("player_id")
    vip_info = player_vip_days.get(player_id)
    if vip_info:
        vip_days = vip_info[0]
        return f"{TRANSL['you_won'][LANG]} {vip_days} {TRANSL['days_of_vip'][LANG]}!"
    else:
        return ""

def should_enable_vip_reward(rcon):
    try:
        server_status = rcon.get_status()
        return server_status.get("current_players", 0) >= MIN_PLAYERS_FOR_VIP_GAME_SCORES
    except Exception as e:
        logger.error(f"Error checking server status: {e}")
        return False

def send_message(rcon, player, message):
    try:
        player_id = player.get("player_id")
        player_name = player.get("player")

        rcon.message_player(
            player_id=player_id,
            player_name=player_name,
            message=message,
            by="top_stats"
        )
    except Exception as e:
        logger.error(f"Error sending message to player {player_name}: {e}")

def send_messages(rcon, players):
    logger.info(f"Sending messages for {len(players)} players")
    base_message = f"█ {TRANSL['end_of_game'][LANG]} █"
    game_top_scores_message = get_game_top_scores(rcon, players)
    for player in players:
        player_id = player.get("player_id")
        if not is_online(player_id):
            continue
        player_scores_message = get_individual_scores_message(player)
        player_congratulations_message = get_player_congratulations_message(player)
        player_message = base_message + "\n\n" + game_top_scores_message + "\n" + player_scores_message + "\n" + player_congratulations_message
        send_message(rcon, player, player_message)

def get_players():
    try:
        live_game_stats: dict = get_cached_live_game_stats()
        players = live_game_stats.get("stats", [])
        logger.info(f"Found {len(players)} players in live game stats")
        return players
    except Exception as e:
        logger.error(f"Error getting players: {str(e)}")
        return []

def top_stats_on_match_end(rcon: Rcon, struct_log: StructuredLogLineWithMetaData):
    server_number = get_server_number()
    if ENABLED and server_number in ENABLE_ON_SERVERS:
        try:
            refresh_connected_players(rcon)
            refresh_current_vips(rcon)
            players = get_players()
            if players:
                logger.info(f"Processing stats for {len(players)} players")
                send_messages(rcon, players)
                if player_vip_days:
                    process_vips_reward(rcon)
            else:
                logger.info("No players online")
        except Exception as e:
            logger.error(f"Error processing end match stats: {str(e)}")
