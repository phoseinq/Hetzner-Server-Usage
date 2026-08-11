import json
import logging
import re
from pathlib import Path
from datetime import datetime

logger = logging.getLogger(__name__)

MONTH_KEY = re.compile(r'^\d{4}-\d{2}$')


class OverageTracker:
    """Tracks traffic overage cost per month, per server.

    Overage is accumulated as *deltas*: each poll compares the current cost
    derived from Hetzner's traffic counter against the last value seen for
    that server, and books only the difference into the month it happened in.
    A drop means the counter was reset (month rollover, or the bot's own
    upgrade/downgrade trick), so everything read after it counts as new.

    Booking deltas is what keeps months separate. The previous model stored a
    per-month snapshot of the counter, so a month that began before Hetzner
    reset the counter opened by re-absorbing the previous month's traffic and
    then billed it a second time when the reset landed.

    Data format:
      {
        "months": {"2026-08": {"servers": {"12345": 0.20}, "updated_at": "..."}},
        "last_seen": {"12345": 9.35}
      }
    """

    def __init__(self, data_file='overage_history.json'):
        self.data_file = Path(data_file)

    def _load_data(self):
        if not self.data_file.exists():
            return {'months': {}, 'last_seen': {}}
        try:
            return self._migrate(json.loads(self.data_file.read_text()))
        except Exception as e:
            logger.error(f"Failed to load overage data: {e}")
            return {'months': {}, 'last_seen': {}}

    @staticmethod
    def _migrate(data):
        """Convert the old {"YYYY-MM": {servers: {id: {committed, live}}}} form.

        Every month collapses to a single figure per server. A month whose
        `committed` merely re-books the previous month's `live` has that
        amount subtracted — that double count is the bug this replaces.
        """
        if 'months' in data:
            return data
        months, last_seen = {}, {}
        prev_live = {}
        for month in sorted(k for k in data if MONTH_KEY.match(k)):
            entry = data[month] or {}
            servers = {}
            for sid, s in (entry.get('servers') or {}).items():
                if isinstance(s, dict):
                    committed, live = s.get('committed', 0) or 0, s.get('live', 0) or 0
                else:
                    committed, live = s or 0, 0
                carried = min(prev_live.get(sid, 0), committed)
                cost = round(max(0.0, committed + live - carried), 2)
                if cost:
                    servers[sid] = cost
                prev_live[sid] = live
                last_seen[sid] = live
            old_total = entry.get('overage_cost')
            if old_total and not servers:
                servers['_migrated'] = round(float(old_total), 2)
            months[month] = {'servers': servers, 'updated_at': entry.get('updated_at', '')}
        return {'months': months, 'last_seen': last_seen}

    def _save_data(self, data):
        try:
            self.data_file.write_text(json.dumps(data, indent=2))
        except Exception as e:
            logger.error(f"Failed to save overage data: {e}")

    @staticmethod
    def _now_month():
        return datetime.now().strftime('%Y-%m')

    def _month_servers(self, data, month=None):
        entry = data.setdefault('months', {}).setdefault(month or self._now_month(), {})
        return entry.setdefault('servers', {}), entry

    def update_live_overage(self, server_id, overage_cost):
        """Book whatever overage accrued since the last reading."""
        sid = str(server_id)
        data = self._load_data()
        last_seen = data.setdefault('last_seen', {})
        previous = last_seen.get(sid, 0) or 0
        current = max(0.0, round(overage_cost, 2))

        if current + 1e-6 < previous:
            # counter went back down => a new traffic cycle started
            logger.info(f"Server {sid}: traffic reset detected (€{previous:.2f} -> €{current:.2f})")
            delta = current
        else:
            delta = current - previous

        last_seen[sid] = current
        if delta > 0:
            servers, entry = self._month_servers(data)
            servers[sid] = round(servers.get(sid, 0) + delta, 2)
            entry['updated_at'] = datetime.now().isoformat()
        self._save_data(data)

    def commit_overage(self, server_id):
        """Finalize before a traffic reset the bot performs itself.

        Deltas are booked as they are read, so the cost is already recorded;
        this only clears the baseline so the post-reset counter is not read as
        a drop twice.
        """
        data = self._load_data()
        data.setdefault('last_seen', {})[str(server_id)] = 0
        self._save_data(data)

    def get_server_month_overage(self, server_id):
        data = self._load_data()
        servers, _ = self._month_servers(data)
        return round(servers.get(str(server_id), 0), 2)

    def get_total_overage(self):
        data = self._load_data()
        return round(sum(
            sum(m.get('servers', {}).values()) for m in data.get('months', {}).values()
        ), 2)

    def get_monthly_breakdown(self):
        data = self._load_data()
        return [
            (month, round(sum(entry.get('servers', {}).values()), 2))
            for month, entry in sorted(data.get('months', {}).items(), reverse=True)
        ]

    def get_current_month_overage(self):
        data = self._load_data()
        servers, _ = self._month_servers(data)
        return round(sum(servers.values()), 2)


overage_tracker = OverageTracker()


def demo():
    import tempfile, os
    path = os.path.join(tempfile.mkdtemp(), 'o.json')
    t = OverageTracker(path)
    month = datetime.now().strftime('%Y-%m')

    # overage grows within a month -> only the increments are booked
    t.update_live_overage(1, 2.0)
    t.update_live_overage(1, 5.0)
    assert t.get_server_month_overage(1) == 5.0, t.get_server_month_overage(1)
    # a traffic reset must not re-bill what was already counted
    t.update_live_overage(1, 0.0)
    t.update_live_overage(1, 1.5)
    assert t.get_server_month_overage(1) == 6.5
    assert t.get_current_month_overage() == 6.5
    assert t.get_total_overage() == 6.5

    # the reported bug: July ends at 9.15, the counter keeps running into
    # August and is only reset later. August must be billed 0.20, not 9.35.
    old = {
        "2026-07": {"servers": {"42": {"committed": 0, "live": 9.15}}},
        "2026-08": {"servers": {"42": {"committed": 9.35, "live": 0.0}}},
    }
    path2 = os.path.join(tempfile.mkdtemp(), 'o2.json')
    open(path2, 'w').write(json.dumps(old))
    t2 = OverageTracker(path2)
    assert dict(t2.get_monthly_breakdown()) == {"2026-07": 9.15, "2026-08": 0.20}, t2.get_monthly_breakdown()
    assert t2.get_total_overage() == 9.35

    # months stay separate going forward
    t3 = OverageTracker(os.path.join(tempfile.mkdtemp(), 'o3.json'))
    t3.update_live_overage(7, 4.0)
    data = json.loads(open(t3.data_file).read())
    data['months']['1999-01'] = data['months'].pop(month)
    open(t3.data_file, 'w').write(json.dumps(data))
    t3.update_live_overage(7, 4.6)          # counter carried across the boundary
    assert t3.get_server_month_overage(7) == 0.6, t3.get_server_month_overage(7)
    assert t3.get_total_overage() == 4.6
    print('overage_tracker demo OK')


if __name__ == '__main__':
    demo()
