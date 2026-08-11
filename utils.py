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
    print('utils demo OK')


if __name__ == '__main__':
    demo()
