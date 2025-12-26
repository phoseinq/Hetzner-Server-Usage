import logging
import csv
from datetime import datetime
from pathlib import Path
from config import Config
from hetzner_api import hetzner_api
from utils import format_traffic, get_traffic_emoji

logger = logging.getLogger(__name__)

class TrafficTracker:
    def __init__(self):
        self.data_file = Path(Config.DATA_FILE)
        self._ensure_file()
    
    def _ensure_file(self):
        if not self.data_file.exists():
            with open(self.data_file, 'w', newline='') as f:
                writer = csv.writer(f)
                writer.writerow(['server_id', 'last_warning', 'last_critical'])
    
    def get_server_status(self, server_id):
        with open(self.data_file, 'r') as f:
            reader = csv.DictReader(f)
            for row in reader:
                if int(row['server_id']) == server_id:
                    return {
                        'last_warning': row['last_warning'],
                        'last_critical': row['last_critical']
                    }
        return {'last_warning': '', 'last_critical': ''}
    
    def update_server_status(self, server_id, warning=None, critical=None):
        rows = []
        found = False
        
        with open(self.data_file, 'r') as f:
            reader = csv.DictReader(f)
            for row in reader:
                if int(row['server_id']) == server_id:
                    if warning:
                        row['last_warning'] = warning
                    if critical:
                        row['last_critical'] = critical
                    found = True
                rows.append(row)
        
        if not found:
            rows.append({
                'server_id': server_id,
                'last_warning': warning or '',
                'last_critical': critical or ''
            })
        
        with open(self.data_file, 'w', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=['server_id', 'last_warning', 'last_critical'])
            writer.writeheader()
            writer.writerows(rows)

tracker = TrafficTracker()

async def traffic_monitor(bot):
    logger.info("🔍 Running daily traffic monitor check...")
    
    today = datetime.now().strftime('%Y-%m-%d')
    
    try:
        servers = await hetzner_api.list_servers()
        
        if not servers:
            logger.warning("No servers found during monitor check")
            return
        
        for server in servers:
            server_id = server.get('id')
            server_name = server.get('name', 'Unnamed')
            traffic_bytes = server.get('outgoing_traffic', 0)
            traffic_tb = traffic_bytes / (1024 ** 4)
            usage_pct = (traffic_bytes / Config.TRAFFIC_LIMIT_BYTES) * 100
            
            status = tracker.get_server_status(server_id)
            emoji = get_traffic_emoji(traffic_tb)
            
            if usage_pct >= Config.CRITICAL_THRESHOLD * 100:
                if status['last_critical'] != today:
                    message = (
                        f"🚨 *CRITICAL TRAFFIC ALERT*\n\n"
                        f"Server: `{server_name}`\n"
                        f"{emoji} Traffic: {format_traffic(traffic_bytes)} ({usage_pct:.1f}%)\n\n"
                        f"⚠️ Traffic limit almost exhausted!\n"
                        f"Consider resetting traffic to avoid overage charges."
                    )
                    
                    try:
                        await bot.send_message(
                            chat_id=Config.ADMIN_ID,
                            text=message,
                            parse_mode='Markdown'
                        )
                        tracker.update_server_status(server_id, critical=today)
                        logger.info(f"Critical alert sent for server {server_name}")
                    except Exception as e:
                        logger.error(f"Failed to send critical alert: {e}")
            
            elif usage_pct >= Config.WARNING_THRESHOLD * 100:
                if status['last_warning'] != today:
                    message = (
                        f"⚠️ *TRAFFIC WARNING*\n\n"
                        f"Server: `{server_name}`\n"
                        f"{emoji} Traffic: {format_traffic(traffic_bytes)} ({usage_pct:.1f}%)\n\n"
                        f"Traffic usage has exceeded 75% of the monthly limit."
                    )
                    
                    try:
                        await bot.send_message(
                            chat_id=Config.ADMIN_ID,
                            text=message,
                            parse_mode='Markdown'
                        )
                        tracker.update_server_status(server_id, warning=today)
                        logger.info(f"Warning alert sent for server {server_name}")
                    except Exception as e:
                        logger.error(f"Failed to send warning alert: {e}")
        
        logger.info("✅ Daily traffic monitor check completed")
        
    except Exception as e:
        logger.error(f"Error in traffic monitor: {e}")