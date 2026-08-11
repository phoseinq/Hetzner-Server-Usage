import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


class PriceStore:
    """Manual monthly prices, overriding what the Hetzner API reports.

    The API only ever returns the current list price. Long-standing accounts
    are billed on the price that was in force when the server was ordered, and
    nothing in the API exposes that, so the real figure has to be entered by
    hand. Overrides are keyed by server type ("cx23"), because a grandfathered
    price applies to every server of that type.

    File format:
      {"types": {"cx23": 3.79}}   # gross EUR per month, as billed
    """

    def __init__(self, data_file='price_overrides.json'):
        self.data_file = Path(data_file)

    def _load(self):
        if not self.data_file.exists():
            return {}
        try:
            data = json.loads(self.data_file.read_text())
            return data.get('types', {}) if isinstance(data, dict) else {}
        except Exception as e:
            logger.error(f"Failed to load price overrides: {e}")
            return {}

    def _save(self, types):
        try:
            self.data_file.write_text(json.dumps({'types': types}, indent=2))
        except Exception as e:
            logger.error(f"Failed to save price overrides: {e}")

    def get(self, server_type):
        """Override for this server type, or None to use the API price."""
        value = self._load().get(str(server_type or '').lower())
        try:
            return float(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    def set(self, server_type, price):
        types = self._load()
        types[str(server_type).lower()] = round(float(price), 2)
        self._save(types)

    def clear(self, server_type):
        types = self._load()
        types.pop(str(server_type).lower(), None)
        self._save(types)

    def all(self):
        return self._load()

    def apply(self, server_type, api_price):
        """Price to bill for one server of this type."""
        override = self.get(server_type)
        return api_price if override is None else override


price_store = PriceStore()


def demo():
    import tempfile, os
    path = os.path.join(tempfile.mkdtemp(), 'p.json')
    s = PriceStore(path)
    assert s.get('cx23') is None
    assert s.apply('cx23', 6.64) == 6.64          # no override -> API price
    s.set('CX23', '3.79')                          # case-insensitive, string ok
    assert s.get('cx23') == 3.79
    assert s.apply('cx23', 6.64) == 3.79
    assert s.apply('cpx22', 23.58) == 23.58        # untouched type unaffected
    assert PriceStore(path).get('cx23') == 3.79    # survives a reload
    s.clear('cx23')
    assert s.get('cx23') is None
    print('price_store demo OK')


if __name__ == '__main__':
    demo()
