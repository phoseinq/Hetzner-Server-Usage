import json
import logging
import re
from pathlib import Path
from datetime import datetime

logger = logging.getLogger(__name__)

MONTH_KEY = re.compile(r'^\d{4}-\d{2}$')


class OverageTracker:
    """Tracks traffic overage cost per month, per server.

    What a server owes right now is whatever its traffic counter is currently
    over the allowance — nothing more. Hetzner bills one cycle per calendar
    month, off the counter, so an amount that was on the counter and is no
    longer there will never be charged. Under the allowance means owing
    nothing, whatever the counter did earlier in the month.

    Past cycles are kept for history. When the counter drops, the value it
    held is filed under the month the cycle started in: `paid` if that cycle
    ran to the end of its month and was billed, `avoided` if it was cut short
    inside the same month by a traffic reset, which is what a reset is for.

    Data format:
      {
        "months": {"2026-08": {"paid": {"123": 5.0}, "avoided": {"456": 2.0}}},
        "live": {"123": {"cost": 0.2, "month": "2026-08"}}
      }

    `live.month` is the month the current cycle *started* in, not the month it
    was last read in, so a counter that has not rolled over yet is not charged
    to the new month.
    """

    def __init__(self, data_file='overage_history.json'):
        self.data_file = Path(data_file)

    def _load_data(self):
        if not self.data_file.exists():
            return {'months': {}, 'live': {}}
        try:
            return self._migrate(json.loads(self.data_file.read_text()))
        except Exception as e:
            logger.error(f"Failed to load overage data: {e}")
            return {'months': {}, 'live': {}}

    @classmethod
    def _migrate(cls, data):
        """Bring older files up to the current shape.

        Two older layouts exist: `{"YYYY-MM": {servers: {id: {committed,
        live}}}}`, and the delta form that replaced it, which accumulated a
        month total under `servers`. Both are read into `paid`. The month in
        progress goes to `avoided` instead — its counter is being read live,
        so anything already recorded for it is by definition no longer on the
        counter and will not be billed.
        """
        if 'live' in data and 'months' in data:
            return data
        now = cls._now_month()
        months, live = {}, {}

        if 'months' in data:                       # delta form
            for month, entry in data['months'].items():
                booked = entry.get('servers', {})
                bucket = 'avoided' if month == now else 'paid'
                months[month] = {
                    'paid': {} if month == now else dict(booked),
                    'avoided': {**entry.get('avoided', {}), **(booked if month == now else {})},
                    'updated_at': entry.get('updated_at', ''),
                }
            for sid, cost in (data.get('last_seen') or {}).items():
                live[sid] = {'cost': float(cost or 0), 'month': now}
            return {'months': months, 'live': live}

        prev_live = {}                             # original committed/live form
        for month in sorted(k for k in data if MONTH_KEY.match(k)):
            entry = data[month] or {}
            paid = {}
            for sid, s in (entry.get('servers') or {}).items():
                if isinstance(s, dict):
                    committed, was_live = s.get('committed', 0) or 0, s.get('live', 0) or 0
                else:
                    committed, was_live = s or 0, 0
                # a month whose `committed` only re-books the previous month's
                # `live` is the old double count; drop that part
                carried = min(prev_live.get(sid, 0), committed)
                cost = round(max(0.0, committed + was_live - carried), 2)
                if cost:
                    paid[sid] = cost
                prev_live[sid] = was_live
                live[sid] = {'cost': was_live, 'month': now}
            old_total = entry.get('overage_cost')
            if old_total and not paid:
                paid['_migrated'] = round(float(old_total), 2)
            months[month] = {'paid': paid, 'avoided': {}, 'updated_at': entry.get('updated_at', '')}

        if now in months:                          # month in progress is not owed
            entry = months[now]
            entry['avoided'].update(entry.pop('paid'))
            entry['paid'] = {}
        return {'months': months, 'live': live}

    def _save_data(self, data):
        try:
            self.data_file.write_text(json.dumps(data, indent=2))
        except Exception as e:
            logger.error(f"Failed to save overage data: {e}")

    @staticmethod
    def _now_month():
        return datetime.now().strftime('%Y-%m')

    @classmethod
    def _file_cycle(cls, data, sid, cycle, bucket):
        """Record a finished cycle's cost under the month it started in."""
        cost = round(cycle.get('cost', 0) or 0, 2)
        if not cost:
            return 0
        month = cycle.get('month') or cls._now_month()
        entry = data.setdefault('months', {}).setdefault(month, {})
        target = entry.setdefault(bucket, {})
        target[sid] = round(target.get(sid, 0) + cost, 2)
        entry['updated_at'] = datetime.now().isoformat()
        return cost

    def update_live_overage(self, server_id, overage_cost):
        """Record the current overage, closing the old cycle if it reset."""
        sid = str(server_id)
        data = self._load_data()
        live = data.setdefault('live', {})
        now = self._now_month()
        cycle = live.get(sid)
        current = max(0.0, round(overage_cost, 2))

        if cycle and current + 1e-6 < (cycle.get('cost') or 0):
            # the counter dropped, so that cycle is over. It was billed only
            # if it ran past the end of its own month; a drop inside the same
            # month is a traffic reset, and a reset is what stops the charge.
            billed = cycle.get('month') != now
            filed = self._file_cycle(data, sid, cycle, 'paid' if billed else 'avoided')
            logger.info(
                f"Server {sid}: traffic cycle ended (€{cycle.get('cost', 0):.2f} -> €{current:.2f})"
                + (f", €{filed:.2f} recorded as {'paid' if billed else 'avoided'}" if filed else "")
            )
            cycle = None

        if cycle is None:
            cycle = {'cost': current, 'month': now}
        else:
            cycle['cost'] = current
        live[sid] = cycle
        self._save_data(data)

    def commit_overage(self, server_id):
        """Settle before a traffic reset the bot performs itself.

        The reset is about to wipe the counter, so nothing here gets billed;
        it is filed as avoided and the live cycle starts over at zero.
        """
        sid = str(server_id)
        data = self._load_data()
        cycle = data.setdefault('live', {}).get(sid)
        if cycle:
            filed = self._file_cycle(data, sid, cycle, 'avoided')
            if filed:
                logger.info(f"Server {sid}: €{filed:.2f} overage cleared by traffic reset")
        data['live'][sid] = {'cost': 0.0, 'month': self._now_month()}
        self._save_data(data)

    def get_server_month_overage(self, server_id):
        """What this server owes this month — what its counter says, or nothing.

        A cycle that started in an earlier month belongs to that month's bill,
        so it is not charged here even while its counter keeps running.
        """
        cycle = self._load_data().get('live', {}).get(str(server_id))
        if not cycle or cycle.get('month') != self._now_month():
            return 0
        return round(cycle.get('cost', 0) or 0, 2)

    def get_current_month_overage(self):
        live = self._load_data().get('live', {})
        now = self._now_month()
        return round(sum(
            c.get('cost', 0) or 0 for c in live.values() if c.get('month') == now
        ), 2)

    def get_current_month_avoided(self):
        entry = self._load_data().get('months', {}).get(self._now_month(), {})
        return round(sum(entry.get('avoided', {}).values()), 2)

    def get_total_overage(self):
        """Everything actually billed, across every month."""
        return round(sum(
            sum(m.get('paid', {}).values()) for m in self._load_data().get('months', {}).values()
        ), 2)

    def get_total_avoided(self):
        return round(sum(
            sum(m.get('avoided', {}).values()) for m in self._load_data().get('months', {}).values()
        ), 2)

    def get_monthly_breakdown(self):
        """Billed cost per month, newest first. Excludes the month in progress,
        which is still on the counter and shown live in the summary."""
        data = self._load_data()
        now = self._now_month()
        return [
            (month, round(sum(entry.get('paid', {}).values()), 2))
            for month, entry in sorted(data.get('months', {}).items(), reverse=True)
            if month != now
        ]


overage_tracker = OverageTracker()


def demo():
    import tempfile, os
    now = datetime.now().strftime('%Y-%m')

    def fresh():
        return OverageTracker(os.path.join(tempfile.mkdtemp(), 'o.json'))

    # what is owed is what the counter says, nothing accumulated
    t = fresh()
    t.update_live_overage(1, 2.0)
    t.update_live_overage(1, 5.0)
    assert t.get_server_month_overage(1) == 5.0
    # back under the allowance => nothing owed, so no warning
    t.update_live_overage(1, 0.0)
    assert t.get_server_month_overage(1) == 0, "warning must clear once under the limit"
    assert t.get_current_month_overage() == 0
    assert t.get_current_month_avoided() == 5.0     # the reset saved it
    assert t.get_total_overage() == 0               # it was never billed
    # traffic climbs again in the same month
    t.update_live_overage(1, 1.5)
    assert t.get_server_month_overage(1) == 1.5

    # the bot's own reset settles the same way, and does not double-file
    t = fresh()
    t.update_live_overage(2, 4.0)
    t.commit_overage(2)
    assert t.get_server_month_overage(2) == 0
    assert t.get_current_month_avoided() == 4.0
    t.update_live_overage(2, 0.0)
    assert t.get_current_month_avoided() == 4.0, "filed twice"

    # a cycle that ran to the end of its month WAS billed: it lands in that
    # month as paid, and the new month starts owing nothing
    t = fresh()
    t.update_live_overage(3, 6.0)
    data = json.loads(open(t.data_file).read())
    data['live']['3']['month'] = '1999-01'          # cycle belongs to last month
    open(t.data_file, 'w').write(json.dumps(data))
    assert t.get_server_month_overage(3) == 0, "last month's cycle is not this month's bill"
    t.update_live_overage(3, 0.0)                   # Hetzner's monthly reset
    assert dict(t.get_monthly_breakdown())['1999-01'] == 6.0
    assert t.get_total_overage() == 6.0
    assert t.get_total_avoided() == 0

    # the reported bug: July ends at 9.15, the counter keeps running into
    # August and resets later. August must not be billed for July.
    old = {
        "2026-07": {"servers": {"42": {"committed": 0, "live": 9.15}}},
        "2026-08": {"servers": {"42": {"committed": 9.35, "live": 0.0}}},
    }
    p = os.path.join(tempfile.mkdtemp(), 'o2.json')
    open(p, 'w').write(json.dumps(old))
    t = OverageTracker(p)
    assert dict(t.get_monthly_breakdown())["2026-07"] == 9.15
    assert t.get_server_month_overage(42) == 0

    # and the stale leftovers the delta form recorded stop raising a warning
    delta_form = {
        "months": {now: {"servers": {"7": 0.20}}, "1999-01": {"servers": {"7": 3.0}}},
        "last_seen": {"7": 0.0},
    }
    p = os.path.join(tempfile.mkdtemp(), 'o3.json')
    open(p, 'w').write(json.dumps(delta_form))
    t = OverageTracker(p)
    assert t.get_server_month_overage(7) == 0, "stale month total still warns"
    assert t.get_current_month_avoided() == 0.20    # kept, just not owed
    assert dict(t.get_monthly_breakdown())['1999-01'] == 3.0
    print('overage_tracker demo OK')


if __name__ == '__main__':
    demo()
