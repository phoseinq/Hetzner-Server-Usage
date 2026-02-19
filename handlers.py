import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler
from config import Config
from hetzner_api import hetzner_api
from utils import format_traffic, get_traffic_emoji, get_location_info
from server_manager import reset_server_traffic
from overage_tracker import overage_tracker
from shell_handler import console_entry, active_sessions

logger = logging.getLogger(__name__)


async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != Config.ADMIN_ID:
        return
    keyboard = [
        [InlineKeyboardButton("📊 Server Management Panel", callback_data="list_servers")],
        [InlineKeyboardButton("💸 Cost Report", callback_data="overage_cost")],
    ]
    await update.message.reply_text(
        "🚀 *Hetzner Server Manager Bot*\n\n"
        "Manage your Hetzner Cloud servers with ease.\n"
        "Monitor traffic, reset limits, and control server states.\n\n"
        "Click below to access your server panel.",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown",
    )


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id != Config.ADMIN_ID:
        await query.answer("⛔ Unauthorized", show_alert=True)
        return
    await query.answer()
    data = query.data

    if data == "list_servers":
        await show_server_list(query, context)
    elif data.startswith("page_"):
        await show_server_list(query, context, int(data.split("_")[1]))
    elif data.startswith("server_"):
        await show_server_detail(query, context, int(data.split("_")[1]))
    elif data.startswith("refresh_"):
        await show_server_detail(query, context, int(data.split("_")[1]), refresh=True)
    elif data.startswith("poweron_"):
        await power_action(query, context, int(data.split("_")[1]), "on")
    elif data.startswith("poweroff_"):
        await power_action(query, context, int(data.split("_")[1]), "off")
    elif data.startswith("reset_"):
        await reset_traffic(query, context, int(data.split("_")[1]))
    elif data.startswith("resetpw_confirm_"):
        await reset_password_confirm(query, context, int(data.split("_")[2]))
    elif data.startswith("resetpw_"):
        await reset_password(query, context, int(data.split("_")[1]))
    elif data == "overage_cost":
        await show_overage_cost(query, context)
    elif data == "start_menu":
        await show_start_menu(query)


async def _start_console(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    server_id = int(query.data.split("_")[1])
    server = await hetzner_api.get_server(server_id)
    if not server:
        await query.edit_message_text("⚠️ Server not found.")
        return ConversationHandler.END
    if server.get("status") != "running":
        await query.answer("⚠️ Server must be RUNNING to open a console.", show_alert=True)
        return ConversationHandler.END
    ip = server.get("public_net", {}).get("ipv4", {}).get("ip", "")
    name = server.get("name", "Server")
    return await console_entry(query, context, server_id, ip, name)


async def show_server_list(query, context, page=0):
    servers = await hetzner_api.list_servers()
    if not servers:
        await query.edit_message_text("⚠️ No servers found or API error occurred.")
        return

    items_per_page = 5
    total_pages = (len(servers) - 1) // items_per_page + 1
    page_servers = servers[page * items_per_page:(page + 1) * items_per_page]

    keyboard = []
    for s in page_servers:
        tb = s.get("outgoing_traffic", 0) / (1024 ** 4)
        emoji = get_traffic_emoji(tb)
        loc_name, flag = get_location_info(s.get("datacenter", {}).get("location", {}).get("name", ""))
        keyboard.append([InlineKeyboardButton(
            f"{emoji} {s.get('name','Unnamed')} | {loc_name} {flag} | {format_traffic(s.get('outgoing_traffic',0))}",
            callback_data=f"server_{s['id']}",
        )])

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️ Previous", callback_data=f"page_{page-1}"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton("Next ➡️", callback_data=f"page_{page+1}"))
    if nav:
        keyboard.append(nav)
    keyboard.append([InlineKeyboardButton("⬅️ Back to Menu", callback_data="start_menu")])

    await query.edit_message_text(
        "📋 *SERVER LIST*\n",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown",
    )


async def show_server_detail(query, context, server_id, refresh=False):
    server = await hetzner_api.get_server(server_id)
    if not server:
        await query.edit_message_text("⚠️ Server not found or API error.")
        return

    name   = server.get("name", "Unnamed")
    status = server.get("status", "unknown")
    stype  = server.get("server_type", {}).get("name", "Unknown")
    loc_name, flag = get_location_info(server.get("datacenter", {}).get("location", {}).get("name", ""))
    traffic_bytes = server.get("outgoing_traffic", 0)
    traffic_tb    = traffic_bytes / (1024 ** 4)
    traffic_pct   = (traffic_bytes / Config.TRAFFIC_LIMIT_BYTES) * 100
    emoji         = get_traffic_emoji(traffic_tb)
    overage_eur   = max(0, traffic_tb - Config.TRAFFIC_LIMIT_TB) * 1.0
    ip     = server.get("public_net", {}).get("ipv4", {}).get("ip", "N/A")
    cores  = server.get("server_type", {}).get("cores", "N/A")
    memory = server.get("server_type", {}).get("memory", "N/A")
    disk   = server.get("server_type", {}).get("disk", "N/A")

    prices = server.get("server_type", {}).get("prices", [])
    monthly_price = "N/A"
    if prices:
        raw = prices[0].get("price_monthly", {}).get("gross", None)
        if raw:
            monthly_price = f"€{float(raw):.2f}"

    status_emoji = "🟢" if status == "running" else "🔴" if status == "off" else "🟡"

    text = (
        f"🖥️ *{name}*\n\n"
        f"📍 Location: `{loc_name} {flag}`\n"
        f"🔧 Type: `{stype}`\n"
        f"💻 CPU: `{cores} cores` | RAM: `{memory} GB` | Disk: `{disk} GB`\n"
        f"🌐 IP: `{ip}`\n"
        f"{status_emoji} Status: `{status.upper()}`\n\n"
        f"💰 *Pricing*\n"
        f"📦 Server Cost: `{monthly_price}/month`\n"
        f"📊 Overage Cost: `€{overage_eur:.2f}`\n\n"
        f"{emoji} *Traffic Usage*\n"
        f"📊 {format_traffic(traffic_bytes)} ({traffic_pct:.1f}%)\n"
    )

    keyboard = [
        [InlineKeyboardButton("♻️ Reset Traffic", callback_data=f"reset_{server_id}")],
        [
            InlineKeyboardButton(
                "🔴 Power OFF" if status == "running" else "🟢 Power ON",
                callback_data=f"poweroff_{server_id}" if status == "running" else f"poweron_{server_id}",
            ),
            InlineKeyboardButton("🔄 Refresh", callback_data=f"refresh_{server_id}"),
        ],
        [
            InlineKeyboardButton("💻 SSH Console", callback_data=f"console_{server_id}"),
            InlineKeyboardButton("🔑 Reset Password", callback_data=f"resetpw_{server_id}"),
        ],
        [InlineKeyboardButton("⬅️ Back to List", callback_data="list_servers")],
    ]

    await query.edit_message_text(
        text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown",
    )


async def power_action(query, context, server_id, action):
    await query.edit_message_text(f"⚙️ {'Starting' if action == 'on' else 'Stopping'} server...")
    result = await (hetzner_api.power_on(server_id) if action == "on" else hetzner_api.power_off(server_id))
    if result:
        await hetzner_api.wait_for_status(server_id, "running" if action == "on" else "off")
        await show_server_detail(query, context, server_id, refresh=True)
    else:
        await query.edit_message_text("❌ Power action failed. Please try again.")


async def reset_traffic(query, context, server_id):
    await query.edit_message_text("🔄 Starting traffic reset process...\n\nThis may take several minutes.")

    async def update_progress(logs):
        log_text = "\n".join(f"{e} {m}" for e, m in logs)
        try:
            await query.edit_message_text(f"*Traffic Reset Process*\n\n{log_text}", parse_mode="Markdown")
        except Exception:
            pass

    success, logs = await reset_server_traffic(server_id, update_progress)
    log_text = "\n".join(f"{e} {m}" for e, m in logs)
    final = f"*Traffic Reset Process*\n\n{log_text}\n\n"
    final += "✅ *Process completed successfully!*" if success else "❌ *Process failed. Check logs above.*"

    keyboard = [
        [InlineKeyboardButton("🔄 Refresh Status", callback_data=f"refresh_{server_id}")],
        [InlineKeyboardButton("⬅️ Back to List", callback_data="list_servers")],
    ]
    await query.edit_message_text(final, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")


async def reset_password(query, context, server_id):
    server = await hetzner_api.get_server(server_id)
    name = server.get("name", "Server") if server else "Server"
    keyboard = [
        [
            InlineKeyboardButton("✅ Yes, reset it", callback_data=f"resetpw_confirm_{server_id}"),
            InlineKeyboardButton("❌ Cancel", callback_data=f"server_{server_id}"),
        ]
    ]
    await query.edit_message_text(
        f"🔑 *Reset Root Password*\n\n"
        f"Server: `{name}`\n\n"
        f"⚠️ This will generate a new random root password.\n"
        f"The server must be running with qemu-guest-agent installed.\n\n"
        f"Are you sure?",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown",
    )


async def reset_password_confirm(query, context, server_id):
    await query.edit_message_text("🔑 Resetting root password...", parse_mode="Markdown")
    result = await hetzner_api.reset_password(server_id)
    keyboard = [[InlineKeyboardButton("⬅️ Back to Server", callback_data=f"server_{server_id}")]]
    if result and result.get("root_password"):
        pw = result["root_password"]
        await query.edit_message_text(
            f"✅ *Password Reset Successful*\n\n"
            f"🔑 New root password:\n`{pw}`\n\n"
            f"⚠️ Save this password now — it won't be shown again.",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown",
        )
    else:
        await query.edit_message_text(
            "❌ *Password reset failed.*\n\n"
            "Make sure qemu-guest-agent is installed and the server is running.",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown",
        )


async def show_overage_cost(query, context):
    servers = await hetzner_api.list_servers()
    if not servers:
        await query.edit_message_text("⚠️ No servers found or API error occurred.")
        return

    total_server_cost = monthly_overage = total_usage = 0
    server_details = []

    for s in servers:
        tb   = s.get("outgoing_traffic", 0) / (1024 ** 4)
        name = s.get("name", "Unnamed")
        prices = s.get("server_type", {}).get("prices", [])
        sp = float(prices[0].get("price_monthly", {}).get("gross", 0)) if prices else 0
        total_server_cost += sp
        ov = max(0, tb - Config.TRAFFIC_LIMIT_TB) * 1.0
        monthly_overage += ov
        total_usage += sp + ov
        if ov > 0:
            server_details.append(f"• {name}: €{ov:.2f}")

    overage_tracker.record_monthly_overage(monthly_overage)
    total_historic = overage_tracker.get_total_overage()
    monthly_breakdown = overage_tracker.get_monthly_breakdown()

    text = (
        f"💸 *COST REPORT*\n\n"
        f"📦 *Server Costs (This Month)*\n€{total_server_cost:.2f}\n\n"
        f"📊 *Overage Costs (This Month)*\n€{monthly_overage:.2f}\n\n"
    )
    if server_details:
        text += "*Overage Breakdown:*\n" + "\n".join(server_details) + "\n\n"
    text += (
        f"📈 *Total Usage*\n€{total_usage:.2f}\n\n"
        f"🔴 *Total Overage Loss (All Time)*\n€{total_historic:.2f}\n"
    )
    if monthly_breakdown and len(monthly_breakdown) > 1:
        text += "\n*Monthly History:*\n"
        for month, cost in monthly_breakdown[:6]:
            text += f"• {month}: €{cost:.2f}\n"

    keyboard = [
        [InlineKeyboardButton("📊 Server Management", callback_data="list_servers")],
        [InlineKeyboardButton("⬅️ Back", callback_data="start_menu")],
    ]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")


async def show_start_menu(query):
    keyboard = [
        [InlineKeyboardButton("📊 Server Management Panel", callback_data="list_servers")],
        [InlineKeyboardButton("💸 Cost Report", callback_data="overage_cost")],
    ]
    await query.edit_message_text(
        "🚀 *Hetzner Server Manager Bot*\n\n"
        "Manage your Hetzner Cloud servers with ease.\n"
        "Monitor traffic, reset limits, and control server states.\n\n"
        "Click below to access your server panel.",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown",
    )
