import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from config import Config
from hetzner_api import hetzner_api
from utils import format_traffic, get_traffic_emoji, paginate_list, get_location_info
from server_manager import reset_server_traffic
from overage_tracker import overage_tracker

logger = logging.getLogger(__name__)

async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    if user_id != Config.ADMIN_ID:
        logger.warning(f"Unauthorized access attempt from user {user_id}")
        return
    
    welcome_text = (
        "🚀 *Hetzner Server Manager Bot*\n\n"
        "Manage your Hetzner Cloud servers with ease.\n"
        "Monitor traffic, reset limits, and control server states.\n\n"
        "Click below to access your server panel."
    )
    
    keyboard = [
        [InlineKeyboardButton("📊 Server Management Panel", callback_data="list_servers")],
        [InlineKeyboardButton("💸 Cost Report", callback_data="overage_cost")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        welcome_text,
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    
    if user_id != Config.ADMIN_ID:
        await query.answer("⛔ Unauthorized", show_alert=True)
        return
    
    await query.answer()
    
    data = query.data
    
    if data == "list_servers":
        await show_server_list(query, context)
    
    elif data.startswith("page_"):
        page = int(data.split("_")[1])
        await show_server_list(query, context, page)
    
    elif data.startswith("server_"):
        server_id = int(data.split("_")[1])
        await show_server_detail(query, context, server_id)
    
    elif data.startswith("refresh_"):
        server_id = int(data.split("_")[1])
        await show_server_detail(query, context, server_id, refresh=True)
    
    elif data.startswith("poweron_"):
        server_id = int(data.split("_")[1])
        await power_action(query, context, server_id, "on")
    
    elif data.startswith("poweroff_"):
        server_id = int(data.split("_")[1])
        await power_action(query, context, server_id, "off")
    
    elif data.startswith("reset_"):
        server_id = int(data.split("_")[1])
        await reset_traffic(query, context, server_id)
    
    elif data == "overage_cost":
        await show_overage_cost(query, context)
    
    elif data == "start_menu":
        await show_start_menu(query)

async def show_server_list(query, context, page=0):
    servers = await hetzner_api.list_servers()
    
    if not servers:
        await query.edit_message_text("⚠️ No servers found or API error occurred.")
        return
    
    items_per_page = 5
    total_pages = (len(servers) - 1) // items_per_page + 1
    start_idx = page * items_per_page
    end_idx = start_idx + items_per_page
    page_servers = servers[start_idx:end_idx]
    
    text = "📋 *SERVER LIST*\n"
    
    keyboard = []
    for server in page_servers:
        traffic_bytes = server.get('outgoing_traffic', 0)
        traffic_tb = traffic_bytes / (1024 ** 4)
        emoji = get_traffic_emoji(traffic_tb)
        
        location_code = server.get('datacenter', {}).get('location', {}).get('name', 'Unknown')
        location_name, flag = get_location_info(location_code)
        name = server.get('name', 'Unnamed')
        
        keyboard.append([InlineKeyboardButton(
            f"{emoji} {name} | {location_name} {flag} | {format_traffic(traffic_bytes)}",
            callback_data=f"server_{server['id']}"
        )])
    
    nav_buttons = []
    if page > 0:
        nav_buttons.append(InlineKeyboardButton("⬅️ Previous", callback_data=f"page_{page-1}"))
    if page < total_pages - 1:
        nav_buttons.append(InlineKeyboardButton("Next ➡️", callback_data=f"page_{page+1}"))
    
    if nav_buttons:
        keyboard.append(nav_buttons)
    
    keyboard.append([InlineKeyboardButton("⬅️ Back to Menu", callback_data="start_menu")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(text, reply_markup=reply_markup, parse_mode='Markdown')

async def show_server_detail(query, context, server_id, refresh=False):
    server = await hetzner_api.get_server(server_id)
    
    if not server:
        await query.edit_message_text("⚠️ Server not found or API error.")
        return
    
    name = server.get('name', 'Unnamed')
    status = server.get('status', 'unknown')
    server_type = server.get('server_type', {}).get('name', 'Unknown')
    location_code = server.get('datacenter', {}).get('location', {}).get('name', 'Unknown')
    location_name, flag = get_location_info(location_code)
    
    traffic_bytes = server.get('outgoing_traffic', 0)
    traffic_tb = traffic_bytes / (1024 ** 4)
    traffic_pct = (traffic_bytes / Config.TRAFFIC_LIMIT_BYTES) * 100
    emoji = get_traffic_emoji(traffic_tb)
    
    overage_cost_eur = max(0, traffic_tb - Config.TRAFFIC_LIMIT_TB) * 1.0
    
    ip = server.get('public_net', {}).get('ipv4', {}).get('ip', 'N/A')
    
    cores = server.get('server_type', {}).get('cores', 'N/A')
    memory = server.get('server_type', {}).get('memory', 'N/A')
    disk = server.get('server_type', {}).get('disk', 'N/A')
    
    prices = server.get('server_type', {}).get('prices', [])
    monthly_price = 'N/A'
    if prices:
        price_obj = prices[0]
        price_value = price_obj.get('price_monthly', {}).get('gross', 'N/A')
        if price_value != 'N/A':
            monthly_price = f"€{float(price_value):.2f}"
    
    status_emoji = "🟢" if status == "running" else "🔴" if status == "off" else "🟡"
    
    text = (
        f"🖥️ *{name}*\n\n"
        f"📍 Location: `{location_name} {flag}`\n"
        f"🔧 Type: `{server_type}`\n"
        f"💻 CPU: `{cores} cores` | RAM: `{memory} GB` | Disk: `{disk} GB`\n"
        f"🌐 IP: `{ip}`\n"
        f"{status_emoji} Status: `{status.upper()}`\n\n"
        f"💰 *Pricing*\n"
        f"📦 Server Cost: `{monthly_price}/month`\n"
        f"📊 Overage Cost: `€{overage_cost_eur:.2f}`\n\n"
        f"{emoji} *Traffic Usage*\n"
        f"📊 {format_traffic(traffic_bytes)} ({traffic_pct:.1f}%)\n"
    )
    
    keyboard = [
        [InlineKeyboardButton("♻️ Reset Traffic", callback_data=f"reset_{server_id}")],
        [
            InlineKeyboardButton("🔴 Power OFF" if status == "running" else "🟢 Power ON", 
                               callback_data=f"poweroff_{server_id}" if status == "running" else f"poweron_{server_id}"),
            InlineKeyboardButton("🔄 Refresh", callback_data=f"refresh_{server_id}")
        ],
        [InlineKeyboardButton("⬅️ Back to List", callback_data="list_servers")]
    ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(text, reply_markup=reply_markup, parse_mode='Markdown')

async def power_action(query, context, server_id, action):
    await query.edit_message_text(f"⚙️ {'Starting' if action == 'on' else 'Stopping'} server...")
    
    if action == "on":
        result = await hetzner_api.power_on(server_id)
    else:
        result = await hetzner_api.power_off(server_id)
    
    if result:
        await hetzner_api.wait_for_status(server_id, "running" if action == "on" else "off")
        await show_server_detail(query, context, server_id, refresh=True)
    else:
        await query.edit_message_text("❌ Power action failed. Please try again.")

async def reset_traffic(query, context, server_id):
    await query.edit_message_text("🔄 Starting traffic reset process...\n\nThis may take several minutes.")
    
    async def update_progress(logs):
        log_text = "\n".join([f"{emoji} {msg}" for emoji, msg in logs])
        progress_text = f"*Traffic Reset Process*\n\n{log_text}"
        try:
            await query.edit_message_text(progress_text, parse_mode='Markdown')
        except:
            pass
    
    success, logs = await reset_server_traffic(server_id, update_progress)
    
    log_text = "\n".join([f"{emoji} {msg}" for emoji, msg in logs])
    final_text = f"*Traffic Reset Process*\n\n{log_text}\n\n"
    
    if success:
        final_text += "✅ *Process completed successfully!*"
    else:
        final_text += "❌ *Process failed. Check logs above.*"
    
    keyboard = [
        [InlineKeyboardButton("🔄 Refresh Status", callback_data=f"refresh_{server_id}")],
        [InlineKeyboardButton("⬅️ Back to List", callback_data="list_servers")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(final_text, reply_markup=reply_markup, parse_mode='Markdown')

async def show_overage_cost(query, context):
    servers = await hetzner_api.list_servers()
    
    if not servers:
        await query.edit_message_text("⚠️ No servers found or API error occurred.")
        return
    
    total_server_cost_eur = 0
    monthly_overage_eur = 0
    total_usage_eur = 0
    server_details = []
    
    for server in servers:
        traffic_bytes = server.get('outgoing_traffic', 0)
        traffic_tb = traffic_bytes / (1024 ** 4)
        name = server.get('name', 'Unnamed')
        
        prices = server.get('server_type', {}).get('prices', [])
        server_price = 0
        if prices:
            price_value = prices[0].get('price_monthly', {}).get('gross', 0)
            server_price = float(price_value) if price_value else 0
            total_server_cost_eur += server_price
        
        overage_eur = max(0, traffic_tb - Config.TRAFFIC_LIMIT_TB) * 1.0
        monthly_overage_eur += overage_eur
        
        usage_eur = server_price + overage_eur
        total_usage_eur += usage_eur
        
        if overage_eur > 0:
            server_details.append(f"• {name}: €{overage_eur:.2f}")
    
    overage_tracker.record_monthly_overage(monthly_overage_eur)
    
    total_historic_overage = overage_tracker.get_total_overage()
    monthly_breakdown = overage_tracker.get_monthly_breakdown()
    
    text = (
        f"💸 *COST REPORT*\n\n"
        f"📦 *Server Costs (This Month)*\n"
        f"€{total_server_cost_eur:.2f}\n\n"
        f"📊 *Overage Costs (This Month)*\n"
        f"€{monthly_overage_eur:.2f}\n\n"
    )
    
    if server_details:
        text += "*Overage Breakdown:*\n" + "\n".join(server_details) + "\n\n"
    
    text += (
        f"📈 *Total Usage*\n"
        f"€{total_usage_eur:.2f}\n\n"
        f"💰 *Total Cost*\n"
        f"€{total_usage_eur:.2f}\n\n"
        f"🔴 *Total Overage Loss (All Time)*\n"
        f"€{total_historic_overage:.2f}\n"
    )
    
    if monthly_breakdown and len(monthly_breakdown) > 1:
        text += "\n*Monthly History:*\n"
        for month, cost in monthly_breakdown[:6]:
            text += f"• {month}: €{cost:.2f}\n"
    
    keyboard = [
        [InlineKeyboardButton("📊 Server Management", callback_data="list_servers")],
        [InlineKeyboardButton("⬅️ Back", callback_data="start_menu")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(text, reply_markup=reply_markup, parse_mode='Markdown')

async def show_start_menu(query):
    welcome_text = (
        "🚀 *Hetzner Server Manager Bot*\n\n"
        "Manage your Hetzner Cloud servers with ease.\n"
        "Monitor traffic, reset limits, and control server states.\n\n"
        "Click below to access your server panel."
    )
    
    keyboard = [
        [InlineKeyboardButton("📊 Server Management Panel", callback_data="list_servers")],
        [InlineKeyboardButton("💸 Cost Report", callback_data="overage_cost")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(welcome_text, reply_markup=reply_markup, parse_mode='Markdown')
