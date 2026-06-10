import json
import logging
from pathlib import Path
from datetime import datetime

logger = logging.getLogger(__name__)

class OverageTracker:
    """Tracks traffic overage costs per month, per server.

    Data format:
    {
      "2026-06": {
        "servers": {
          "12345678": {"committed": 1.50, "live": 0.25}
        },
        "updated_at": "2026-06-10T16:00:00"
      }
    }

    'committed' is overage from traffic cycles that already ended (the
    counter was reset), so it survives traffic resets. 'live' mirrors the
    current Hetzner traffic counter. A month's total is committed + live
    summed over all servers. Entries written by older versions of the bot
    ({"overage_cost": x}) are still readable and get migrated on first write.
    """

    def __init__(self, data_file='overage_history.json'):
        self.data_file = Path(data_file)
        self._ensure_file()

    def _ensure_file(self):
        if not self.data_file.exists():
            self.data_file.write_text('{}')

    def _load_data(self):
        try:
            return json.loads(self.data_file.read_text())
        except Exception as e:
            logger.error(f"Failed to load overage data: {e}")
            return {}

    def _save_data(self, data):
        try:
            self.data_file.write_text(json.dumps(data, indent=2))
        except Exception as e:
            logger.error(f"Failed to save overage data: {e}")

    @staticmethod
    def _month_total(month_data):
        if 'servers' in month_data:
            return sum(
                s.get('committed', 0) + s.get('live', 0)
                for s in month_data['servers'].values()
            )
        return month_data.get('overage_cost', 0)

    def _current_month_entry(self, data):
        month = datetime.now().strftime('%Y-%m')
        entry = data.setdefault(month, {})
        if 'servers' not in entry:
            servers = {}
            old_cost = entry.pop('overage_cost', 0)
            if old_cost:
                # old format stored one snapshot value; keep it as committed
                servers['_migrated'] = {'committed': old_cost, 'live': 0}
            entry['servers'] = servers
        return entry

    def update_live_overage(self, server_id, overage_cost):
        data = self._load_data()
        entry = self._current_month_entry(data)
        s = entry['servers'].setdefault(str(server_id), {'committed': 0, 'live': 0})
        if overage_cost + 1e-6 < s.get('live', 0):
            # counter went down => traffic was reset; the old cost is final
            s['committed'] = round(s.get('committed', 0) + s.get('live', 0), 2)
            logger.info(
                f"Server {server_id}: traffic reset detected, "
                f"committed overage now €{s['committed']:.2f}"
            )
        s['live'] = round(overage_cost, 2)
        entry['updated_at'] = datetime.now().isoformat()
        self._save_data(data)

    def commit_overage(self, server_id):
        """Finalize the live overage for a server.

        Called right before a traffic reset so the cost incurred in this
        cycle is preserved in the monthly history.
        """
        data = self._load_data()
        entry = self._current_month_entry(data)
        s = entry['servers'].setdefault(str(server_id), {'committed': 0, 'live': 0})
        if s.get('live', 0) > 0:
            s['committed'] = round(s.get('committed', 0) + s['live'], 2)
            s['live'] = 0
            entry['updated_at'] = datetime.now().isoformat()
            self._save_data(data)
            logger.info(
                f"Server {server_id}: overage committed, "
                f"€{s['committed']:.2f} total this month"
            )

    def get_server_month_overage(self, server_id):
        data = self._load_data()
        month = datetime.now().strftime('%Y-%m')
        servers = data.get(month, {}).get('servers', {})
        s = servers.get(str(server_id), {})
        return round(s.get('committed', 0) + s.get('live', 0), 2)

    def get_total_overage(self):
        data = self._load_data()
        total = sum(self._month_total(m) for m in data.values())
        return round(total, 2)

    def get_monthly_breakdown(self):
        data = self._load_data()
        breakdown = []
        for month, month_data in sorted(data.items(), reverse=True):
            breakdown.append((month, round(self._month_total(month_data), 2)))
        return breakdown

    def get_current_month_overage(self):
        data = self._load_data()
        month = datetime.now().strftime('%Y-%m')
        if month in data:
            return round(self._month_total(data[month]), 2)
        return 0

overage_tracker = OverageTracker()
