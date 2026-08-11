import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


class PriceStore:
    """Manual monthly prices, overriding what the Hetzner API reports.

    The API only ever returns the current list price. A long-standing account
    keeps the price each server was ordered at, and nothing in the API exposes
    it, so the real figure has to be entered by hand. Overrides are keyed by
    server id, not by type: two servers of the same type ordered years apart
    are billed differently.

    File format:
      {"servers": {"123638116": 3.79}}   # gross EUR per month, as billed
    """

    def __init__(self, data_file='price_overrides.json'):
        self.data_file = Path(data_file)

    def _load(self):
        if not self.data_file.exists():
            return {}
        try:
            data = json.loads(self.data_file.read_text())
            return data.get('servers', {}) if isinstance(data, dict) else {}
        except Exception as e:
            logger.error(f"Failed to load price overrides: {e}")
            return {}

    def _save(self, servers):
        try:
            self.data_file.write_text(json.dumps({'servers': servers}, indent=2))
        except Exception as e:
            logger.error(f"Failed to save price overrides: {e}")

    def get(self, server_id):
        """Override for this server, or None to use the API price."""
        value = self._load().get(str(server_id))
        try:
            return float(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    def set(self, server_id, price):
        servers = self._load()
        servers[str(server_id)] = round(float(price), 2)
        self._save(servers)

    def clear(self, server_id):
        servers = self._load()
        servers.pop(str(server_id), None)
        self._save(servers)

    def all(self):
        return self._load()

    def apply(self, server_id, api_price):
        """Price to bill for this server."""
        override = self.get(server_id)
        return api_price if override is None else override


price_store = PriceStore()


def demo():
    import tempfile, os
    path = os.path.join(tempfile.mkdtemp(), 'p.json')
    s = PriceStore(path)
    assert s.get(123) is None
    assert s.apply(123, 6.64) == 6.64              # no override -> API price
    s.set(123, '3.79')                              # int id, string price
    assert s.get('123') == 3.79                     # id type does not matter
    assert s.apply(123, 6.64) == 3.79
    # two servers of the same type stay independent
    assert s.apply(456, 6.64) == 6.64
    s.set(456, 5.00)
    assert (s.apply(123, 6.64), s.apply(456, 6.64)) == (3.79, 5.00)
    assert PriceStore(path).get(123) == 3.79        # survives a reload
    s.clear(123)
    assert s.get(123) is None and s.get(456) == 5.00
    print('price_store demo OK')


if __name__ == '__main__':
    demo()
