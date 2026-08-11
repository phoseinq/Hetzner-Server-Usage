from config import Config

# Hetzner location codes -> ISO country, for the few places where only the
# bare code is known (datacenter names like "nbg1-dc3"). Objects returned by
# the API carry their own country, so they do not need this table.
_LOCATION_COUNTRY = {
    'nbg1': 'DE',
    'fsn1': 'DE',
    'hel1': 'FI',
    'ash': 'US',
    'hil': 'US',
    'sin': 'SG',
}


def get_location(obj):
    """Location dict of a server / primary IP / volume.

    Hetzner dropped the `datacenter` field from these objects (it now comes
    back as null) and exposes `location` at the top level instead; older
    payloads are still read through the datacenter fallback.
    """
    if not obj:
        return {}
    loc = obj.get('location')
    if loc:
        return loc
    return (obj.get('datacenter') or {}).get('location') or {}


def location_name(obj):
    return get_location(obj).get('name', '')


def country_flag(country_code):
    code = (country_code or '').strip().upper()
    if len(code) != 2 or not code.isalpha():
        return '🏳️'
    return ''.join(chr(0x1F1E6 + ord(c) - ord('A')) for c in code)


def get_location_info(location):
    """(label, flag) for a location dict or a bare location code."""
    if isinstance(location, dict):
        code = location.get('name', '')
        country = location.get('country', '')
        label = location.get('city') or location.get('description') or code or 'Unknown'
    else:
        code = (location or '').lower()
        country = _LOCATION_COUNTRY.get(code, '')
        label = code.upper() if code else 'Unknown'
    if not country:
        country = _LOCATION_COUNTRY.get(code.lower(), '')
    return (label, country_flag(country))


def traffic_limit_tb(server):
    """Included outgoing traffic for one server, in TB.

    Hetzner reports this per server (it differs by location and is prorated
    for servers created mid-month), so never assume the 20 TB default.
    """
    included = (server or {}).get('included_traffic')
    if included:
        return included / (1024 ** 4)
    return Config.TRAFFIC_LIMIT_TB


def traffic_price_per_tb(server):
    """Net price of one TB over the included traffic, for this server.

    Hetzner quotes it per location on the server type, so it is read from
    there rather than assumed.
    """
    loc = location_name(server)
    prices = (server or {}).get('server_type', {}).get('prices', [])
    entry = next((p for p in prices if p.get('location') == loc), None) or (prices[0] if prices else {})
    try:
        return float(entry.get('price_per_tb_traffic', {}).get('net') or 1.0)
    except (TypeError, ValueError):
        return 1.0


def overage_cost(server):
    """Net EUR currently owed for traffic beyond this server's allowance."""
    tb = (server or {}).get('outgoing_traffic', 0) / (1024 ** 4)
    return max(0.0, tb - traffic_limit_tb(server)) * traffic_price_per_tb(server)


def format_traffic(bytes_value, limit_tb=None):
    limit = limit_tb if limit_tb else Config.TRAFFIC_LIMIT_TB
    tb = bytes_value / (1024 ** 4)
    return f"{tb:.2f}/{limit:.0f} TB"


def get_traffic_emoji(traffic_tb, limit_tb=None):
    limit = limit_tb if limit_tb else Config.TRAFFIC_LIMIT_TB
    percentage = (traffic_tb / limit) * 100

    if percentage >= 85:
        return "🔴"
    elif percentage >= 70:
        return "🟠"
    elif percentage >= 50:
        return "🟡"
    elif percentage >= 25:
        return "🟢"
    else:
        return "⚪"


def paginate_list(items, page, items_per_page=5):
    start_idx = page * items_per_page
    end_idx = start_idx + items_per_page
    total_pages = (len(items) - 1) // items_per_page + 1

    return items[start_idx:end_idx], total_pages, start_idx


def demo():
    assert country_flag('DE') == '🇩🇪'
    assert country_flag('FI') == '🇫🇮'
    assert country_flag('') == '🏳️'
    # new API shape: location at the top level, datacenter gone
    new = {'datacenter': None, 'location': {'name': 'nbg1', 'city': 'Nuremberg', 'country': 'DE'}}
    assert location_name(new) == 'nbg1'
    assert get_location_info(get_location(new)) == ('Nuremberg', '🇩🇪')
    # old API shape still resolves
    old = {'datacenter': {'name': 'fsn1-dc14', 'location': {'name': 'fsn1', 'country': 'DE'}}}
    assert location_name(old) == 'fsn1'
    # bare code, e.g. from a datacenter name
    assert get_location_info('hel1') == ('HEL1', '🇫🇮')
    assert get_location_info('') == ('Unknown', '🏳️')
    # per-server included traffic wins over the global default
    assert traffic_limit_tb({'included_traffic': 1024 ** 4}) == 1
    assert traffic_limit_tb({}) == Config.TRAFFIC_LIMIT_TB
    assert format_traffic(1024 ** 4, 1) == '1.00/1 TB'
    assert get_traffic_emoji(0.9, 1) == '🔴'
    assert get_traffic_emoji(0.9, 20) == '⚪'
    # overage is priced net, at the rate quoted for the server's own location
    srv = {
        'location': {'name': 'hel1', 'country': 'FI'},
        'included_traffic': 20 * 1024 ** 4,
        'outgoing_traffic': 23 * 1024 ** 4,
        'server_type': {'prices': [
            {'location': 'fsn1', 'price_per_tb_traffic': {'net': '1.00', 'gross': '1.21'}},
            {'location': 'hel1', 'price_per_tb_traffic': {'net': '2.50', 'gross': '3.03'}},
        ]},
    }
    assert traffic_price_per_tb(srv) == 2.50
    assert overage_cost(srv) == 7.5                      # 3 TB over at 2.50
    srv['outgoing_traffic'] = 5 * 1024 ** 4
    assert overage_cost(srv) == 0                        # under the allowance
    assert traffic_price_per_tb({}) == 1.0               # no price data -> default
    print('utils demo OK')


if __name__ == '__main__':
    demo()
