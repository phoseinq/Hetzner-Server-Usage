import logging
import json
from datetime import datetime
from pathlib import Path
from config import Config
from hetzner_api import hetzner_api
from utils import format_traffic, get_traffic_emoji

logger = logging.getLogger(__name__)

STATE_FILE = Path('monitor_state.json')


def _load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:
            pass
    return {}


def _save_state(state: dict):
    try:
        STATE_FILE.write_text(json.dumps(state, indent=2))
    except Exception as e:
        logger.error(f"Failed to save monitor state: {e}")


async def traffic_monitor(bot):
    logger.info("Running hourly traffic monitor check...")
    today = datetime.now().strftime('%Y-%m-%d')
    state = _load_state()

    try:
        servers = await hetzner_api.list_servers()
        if not servers:
            logger.warning("No servers found during monitor check")
            return

        for server in servers:
            server_id = str(server.get('id'))
            server_name = server.get('name', 'Unnamed')
            traffic_bytes = server.get('outgoing_traffic', 0)
            traffic_tb = traffic_bytes / (1024 ** 4)
            usage_pct = (traffic_bytes / Config.TRAFFIC_LIMIT_BYTES) * 100
            emoji = get_traffic_emoji(traffic_tb)

            if server_id not in state:
                state[server_id] = {
                    'warned_75': False,
                    'last_critical_date': '',
                    'last_over_date': '',
                }

            s = state[server_id]

            if usage_pct >= 100:
                if s.get('last_over_date') != today:
                    msg = (
                        f"🔥 *TRAFFIC LIMIT EXCEEDED*\n\n"
                        f"Server: `{server_name}`\n"
                        f"{emoji} Traffic: {format_traffic(traffic_bytes)} ({usage_pct:.1f}%)\n\n"
                        f"You are being charged for overage!\n"
                        f"Reset traffic immediately to stop charges."
                    )
                    await _send(bot, msg)
                    s['last_over_date'] = today

            elif usage_pct >= 98:
                if s.get('last_critical_date') != today:
                    msg = (
                        f"🚨 *CRITICAL TRAFFIC ALERT*\n\n"
                        f"Server: `{server_name}`\n"
                        f"{emoji} Traffic: {format_traffic(traffic_bytes)} ({usage_pct:.1f}%)\n\n"
                        f"⚠️ Traffic limit almost exhausted!\n"
                        f"Consider resetting traffic to avoid overage charges."
                    )
                    await _send(bot, msg)
                    s['last_critical_date'] = today

            elif usage_pct >= 75:
                if not s.get('warned_75'):
                    msg = (
                        f"⚠️ *TRAFFIC WARNING*\n\n"
                        f"Server: `{server_name}`\n"
                        f"{emoji} Traffic: {format_traffic(traffic_bytes)} ({usage_pct:.1f}%)\n\n"
                        f"Traffic usage has exceeded 75% of the monthly limit."
                    )
                    await _send(bot, msg)
                    s['warned_75'] = True

            else:
                if s.get('warned_75'):
                    s['warned_75'] = False
                    logger.info(f"Server {server_name} dropped below 75%, warning reset.")

        _save_state(state)
        logger.info("Hourly traffic monitor check completed")

    except Exception as e:
        logger.error(f"Error in traffic monitor: {e}")


async def _send(bot, message: str):
    try:
        await bot.send_message(chat_id=Config.ADMIN_ID, text=message, parse_mode='Markdown')
    except Exception as e:
        logger.error(f"Failed to send alert: {e}")
