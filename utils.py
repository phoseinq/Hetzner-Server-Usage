from config import Config

def format_traffic(bytes_value):
    tb = bytes_value / (1024 ** 4)
    return f"{tb:.2f}/{Config.TRAFFIC_LIMIT_TB} TB"

def get_traffic_emoji(traffic_tb):
    percentage = (traffic_tb / Config.TRAFFIC_LIMIT_TB) * 100
    
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

def get_location_info(location_code):
    locations = {
        'nbg1': ('Germany', '🇩🇪'),
        'fsn1': ('Germany', '🇩🇪'),
        'hel1': ('Finland', '🇫🇮'),
        'ash': ('USA', '🇺🇸'),
        'hil': ('USA', '🇺🇸'),
        'sin': ('Singapore', '🇸🇬'),
    }
    
    return locations.get(location_code.lower(), ('Unknown', '🏳️'))

def paginate_list(items, page, items_per_page=5):
    start_idx = page * items_per_page
    end_idx = start_idx + items_per_page
    total_pages = (len(items) - 1) // items_per_page + 1
    
    return items[start_idx:end_idx], total_pages, start_idx
