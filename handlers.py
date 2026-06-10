import asyncio
import calendar
import logging
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler
from config import Config
from hetzner_api import hetzner_api
from utils import format_traffic, get_traffic_emoji, get_location_info
from server_manager import reset_server_traffic
from overage_tracker import overage_tracker
from shell_handler import console_entry, active_sessions

logger = logging.getLogger(__name__)


def _gross(price_dict):
    try:
        return float(price_dict.get("gross", 0) or 0)
    except (TypeError, ValueError):
        return 0.0


def _image_price_per_gb(pricing):
    return _gross(pricing.get("image", {}).get("price_per_gb_month", {}))


def _floating_ip_price(pricing, fip):
    loc = fip.get("home_location", {}).get("name")
    for entry in pricing.get("floating_ips", []):
        if entry.get("type") == fip.get("type"):
            for p in entry.get("prices", []):
                if p.get("location") == loc:
                    return _gross(p.get("price_monthly", {}))
    return _gross(pricing.get("floating_ip", {}).get("price_monthly", {}))


def _primary_ip_price(pricing, pip):
    loc = pip.get("datacenter", {}).get("location", {}).get("name")
    for entry in pricing.get("primary_ips", []):
        if entry.get("type") == pip.get("type"):
            for p in entry.get("prices", []):
                if p.get("location") == loc:
                    return _gross(p.get("price_monthly", {}))
    return 0.0


def _main_menu_keyboard():
    return [
        [
            InlineKeyboardButton("📊 Servers", callback_data="list_servers"),
            InlineKeyboardButton("📸 Snapshots", callback_data="snapshots"),
        ],
        [
            InlineKeyboardButton("🌐 Floating IPs", callback_data="fips"),
            InlineKeyboardButton("📍 Primary IPs", callback_data="pips"),
        ],
        [InlineKeyboardButton("💸 Cost Report", callback_data="overage_cost")],
    ]


async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != Config.ADMIN_ID:
        return
    keyboard = _main_menu_keyboard()
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
    elif data == "snapshots":
        await show_snapshots(query, context)
    elif data == "snap_new":
        await snapshot_pick_server(query, context)
    elif data.startswith("snapcreate_"):
        await create_snapshot(query, context, int(data.split("_")[1]))
    elif data.startswith("snapdel_confirm_"):
        await delete_snapshot(query, context, int(data.split("_")[2]))
    elif data.startswith("snapdel_"):
        await delete_snapshot_confirm(query, context, int(data.split("_")[1]))
    elif data.startswith("snap_"):
        await show_snapshot_detail(query, context, int(data.split("_")[1]))
    elif data == "fips":
        await show_ip_list(query, context, "fip")
    elif data == "fip_new":
        await ip_new_type(query, context, "fip")
    elif data.startswith("fipnewt_"):
        await ip_new_place(query, context, "fip", data.split("_")[1])
    elif data.startswith("fipnewl_"):
        _, ip_type, place = data.split("_")
        await ip_new_count(query, context, "fip", ip_type, place)
    elif data.startswith("fipnewc_"):
        _, ip_type, place, count = data.split("_")
        await ip_create(query, context, "fip", ip_type, place, int(count))
    elif data.startswith("fiptog_"):
        await ip_toggle(query, context, "fip", int(data.split("_")[1]))
    elif data == "fipdelsel_yes":
        await ip_delete_selected(query, context, "fip")
    elif data == "fipdelsel":
        await ip_delete_confirm(query, context, "fip")
    elif data == "fipclear":
        context.user_data["fip_sel"] = set()
        await show_ip_list(query, context, "fip")
    elif data == "pips":
        await show_ip_list(query, context, "pip")
    elif data == "pip_new":
        await ip_new_type(query, context, "pip")
    elif data.startswith("pipnewt_"):
        await ip_new_place(query, context, "pip", data.split("_")[1])
    elif data.startswith("pipnewl_"):
        _, ip_type, place = data.split("_")
        await ip_new_count(query, context, "pip", ip_type, place)
    elif data.startswith("pipnewc_"):
        _, ip_type, place, count = data.split("_")
        await ip_create(query, context, "pip", ip_type, place, int(count))
    elif data.startswith("piptog_"):
        await ip_toggle(query, context, "pip", int(data.split("_")[1]))
    elif data == "pipdelsel_yes":
        await ip_delete_selected(query, context, "pip")
    elif data == "pipdelsel":
        await ip_delete_confirm(query, context, "pip")
    elif data == "pipclear":
        context.user_data["pip_sel"] = set()
        await show_ip_list(query, context, "pip")
    elif data.startswith("rebuildgo_"):
        _, sid, image = data.split("_", 2)
        await rebuild_go(query, context, int(sid), image)
    elif data.startswith("rebuildimg_"):
        _, sid, image = data.split("_", 2)
        await rebuild_confirm(query, context, int(sid), image)
    elif data.startswith("rebuild_"):
        await rebuild_pick_image(query, context, int(data.split("_")[1]))
    elif data.startswith("resizego_"):
        _, sid, stype, disk = data.split("_")
        await resize_go(query, context, int(sid), stype, disk == "1")
    elif data.startswith("resizet_"):
        _, sid, stype = data.split("_")
        await resize_confirm(query, context, int(sid), stype)
    elif data.startswith("resizef_"):
        _, sid, family = data.split("_")
        await resize_pick_type(query, context, int(sid), family)
    elif data.startswith("resize_"):
        await resize_pick_family(query, context, int(data.split("_")[1]))
    elif data.startswith("volmenu_"):
        await show_volumes(query, context, int(data.split("_")[1]))
    elif data.startswith("volnewc_"):
        _, sid, size = data.split("_")
        await volume_create(query, context, int(sid), int(size))
    elif data.startswith("volnew_"):
        await volume_pick_size(query, context, int(data.split("_")[1]))
    elif data.startswith("voldetach_"):
        _, vid, sid = data.split("_")
        await volume_detach(query, context, int(vid), int(sid))
    elif data.startswith("volattach_"):
        _, vid, sid = data.split("_")
        await volume_attach(query, context, int(vid), int(sid))
    elif data.startswith("voldelgo_"):
        _, vid, sid = data.split("_")
        await volume_delete(query, context, int(vid), int(sid))
    elif data.startswith("voldel_"):
        _, vid, sid = data.split("_")
        await volume_delete_confirm(query, context, int(vid), int(sid))
    elif data.startswith("srvfip_"):
        await show_server_fips(query, context, int(data.split("_")[1]))
    elif data.startswith("fipas_"):
        _, fid, sid = data.split("_")
        await server_fip_assign(query, context, int(fid), int(sid))
    elif data.startswith("fipun_"):
        _, fid, sid = data.split("_")
        await server_fip_unassign(query, context, int(fid), int(sid))
    elif data.startswith("backupgo_"):
        _, sid, mode = data.split("_")
        await backup_toggle_go(query, context, int(sid), mode)
    elif data.startswith("backup_"):
        await backup_toggle_confirm(query, context, int(data.split("_")[1]))
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

    items_per_page = 10
    total_pages = (len(servers) - 1) // items_per_page + 1
    page_servers = servers[page * items_per_page:(page + 1) * items_per_page]

    keyboard = []
    for s in page_servers:
        tb = s.get("outgoing_traffic", 0) / (1024 ** 4)
        emoji = get_traffic_emoji(tb)
        loc_name, flag = get_location_info(s.get("datacenter", {}).get("location", {}).get("name", ""))
        keyboard.append([InlineKeyboardButton(
            f"{emoji} {s.get('name','Unnamed')} | {flag} {loc_name} | {format_traffic(s.get('outgoing_traffic',0))}",
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
    server = await hetzner_api.get_server(server_id, fresh=refresh)
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
    backups_on = bool(server.get("backup_window"))

    text = (
        f"🖥️ *{name}*\n\n"
        f"📍 Location: `{flag} {loc_name}`\n"
        f"🔧 Type: `{stype}`\n"
        f"💻 CPU: `{cores} cores` | RAM: `{memory} GB` | Disk: `{disk} GB`\n"
        f"🌐 IP: `{ip}`\n"
        f"{status_emoji} Status: `{status.upper()}`\n"
        f"💾 Backups: `{'ON' if backups_on else 'OFF'}`\n\n"
        f"💰 *Pricing*\n"
        f"📦 Server Cost: `{monthly_price}/month`\n"
        f"📊 Overage Cost: `€{overage_eur:.2f}`\n\n"
        f"{emoji} *Traffic Usage*\n"
        f"📊 {format_traffic(traffic_bytes)} ({traffic_pct:.1f}%)\n"
    )

    keyboard = [
        [
            InlineKeyboardButton("♻️ Reset Traffic", callback_data=f"reset_{server_id}"),
            InlineKeyboardButton("📸 Take Snapshot", callback_data=f"snapcreate_{server_id}"),
        ],
        [
            InlineKeyboardButton("🔧 Rebuild OS", callback_data=f"rebuild_{server_id}"),
            InlineKeyboardButton("⚖️ Change Plan", callback_data=f"resize_{server_id}"),
        ],
        [
            InlineKeyboardButton("💽 Volumes", callback_data=f"volmenu_{server_id}"),
            InlineKeyboardButton("🌐 Floating IPs", callback_data=f"srvfip_{server_id}"),
        ],
        [
            InlineKeyboardButton(
                f"💾 Backups: {'ON 🟢' if backups_on else 'OFF 🔴'}",
                callback_data=f"backup_{server_id}",
            ),
            InlineKeyboardButton(
                "🔴 Power OFF" if status == "running" else "🟢 Power ON",
                callback_data=f"poweroff_{server_id}" if status == "running" else f"poweron_{server_id}",
            ),
        ],
        [
            InlineKeyboardButton("💻 SSH Console", callback_data=f"console_{server_id}"),
            InlineKeyboardButton("🔑 Reset Password", callback_data=f"resetpw_{server_id}"),
        ],
        [
            InlineKeyboardButton("🔄 Refresh", callback_data=f"refresh_{server_id}"),
            InlineKeyboardButton("⬅️ Back to List", callback_data="list_servers"),
        ],
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
    servers, pricing, snapshots, floating_ips, primary_ips, volumes = await asyncio.gather(
        hetzner_api.list_servers(),
        hetzner_api.get_pricing(),
        hetzner_api.list_images(),
        hetzner_api.list_floating_ips(),
        hetzner_api.list_primary_ips(),
        hetzner_api.list_volumes(),
    )
    if not servers:
        await query.edit_message_text("⚠️ No servers found or API error occurred.")
        return

    backup_pct = 20.0
    try:
        backup_pct = float(pricing.get("server_backup", {}).get("percentage", 20) or 20)
    except (TypeError, ValueError):
        pass

    total_server_cost = backup_cost = 0
    backup_count = 0
    server_details = []

    for s in servers:
        tb    = s.get("outgoing_traffic", 0) / (1024 ** 4)
        name  = s.get("name", "Unnamed")
        stype = s.get("server_type", {}).get("name", "?")
        prices = s.get("server_type", {}).get("prices", [])
        sp = float(prices[0].get("price_monthly", {}).get("gross", 0)) if prices else 0
        total_server_cost += sp
        ov = max(0, tb - Config.TRAFFIC_LIMIT_TB) * 1.0
        overage_tracker.update_live_overage(s["id"], ov)
        ov_month = overage_tracker.get_server_month_overage(s["id"])
        line = f"• `{name}` ({stype}): €{sp:.2f} | {format_traffic(s.get('outgoing_traffic', 0))}"
        if s.get("backup_window"):
            backup_count += 1
            backup_cost += sp * backup_pct / 100
            line += " | 💾"
        if ov_month > 0:
            line += f" | ⚠️ €{ov_month:.2f} overage"
        server_details.append(line)

    monthly_overage = overage_tracker.get_current_month_overage()
    total_historic = overage_tracker.get_total_overage()
    monthly_breakdown = overage_tracker.get_monthly_breakdown()

    snap_size = sum(i.get("image_size") or 0 for i in snapshots)
    snapshot_cost = snap_size * _image_price_per_gb(pricing)
    floating_cost = sum(_floating_ip_price(pricing, f) for f in floating_ips)
    assigned_pips = [p for p in primary_ips if p.get("assignee_id")]
    unassigned_pips = [p for p in primary_ips if not p.get("assignee_id")]
    extra_primary_cost = sum(_primary_ip_price(pricing, p) for p in unassigned_pips)

    vol_per_gb = _gross(pricing.get("volume", {}).get("price_per_gb_month", {}) or pricing.get("volume", {}))
    vol_size = sum(v.get("size") or 0 for v in volumes)
    volume_cost = vol_size * vol_per_gb

    total_usage = (
        total_server_cost + monthly_overage + backup_cost
        + snapshot_cost + floating_cost + extra_primary_cost + volume_cost
    )

    now = datetime.now()
    days_in_month = calendar.monthrange(now.year, now.month)[1]
    projected_overage = monthly_overage / now.day * days_in_month

    primary_line = f"📍 Primary IPs: {len(assigned_pips)} on servers (free)"
    if unassigned_pips:
        primary_line += f" | {len(unassigned_pips)} unassigned → €{extra_primary_cost:.2f}"

    text = (
        f"💸 *COST REPORT*\n\n"
        f"📦 *Servers (This Month)*\n" + "\n".join(server_details) + "\n\n"
        f"🧩 *Other Resources*\n"
        f"📸 Snapshots ({len(snapshots)}): {snap_size:.1f} GB → €{snapshot_cost:.2f}\n"
        f"💽 Volumes ({len(volumes)}): {vol_size} GB → €{volume_cost:.2f}\n"
        f"💾 Backups ({backup_count} servers): €{backup_cost:.2f}\n"
        f"🌐 Floating IPs ({len(floating_ips)}): €{floating_cost:.2f}\n"
        f"{primary_line}\n\n"
        f"📊 *Summary*\n"
        f"📦 Server costs: €{total_server_cost:.2f}\n"
        f"📈 Overage: €{monthly_overage:.2f}\n"
        f"📸 Snapshots: €{snapshot_cost:.2f}\n"
        f"💽 Volumes: €{volume_cost:.2f}\n"
        f"💾 Backups: €{backup_cost:.2f}\n"
        f"🌐 Floating IPs: €{floating_cost:.2f}\n"
    )
    if unassigned_pips:
        text += f"📍 Extra primary IPs: €{extra_primary_cost:.2f}\n"
    text += f"💰 Total: €{total_usage:.2f}\n\n"
    if monthly_overage > 0:
        text += (
            f"🔮 *Projected Month-End Overage*\n"
            f"~€{projected_overage:.2f} at the current usage rate\n\n"
        )
    text += f"🔴 *Total Overage Loss (All Time)*\n€{total_historic:.2f}\n"
    if monthly_breakdown and len(monthly_breakdown) > 1:
        text += "\n*Monthly History:*\n"
        for month, cost in monthly_breakdown[:6]:
            text += f"• {month}: €{cost:.2f}\n"
    text += f"\n🕓 Updated: `{now.strftime('%H:%M:%S')}`"

    keyboard = [
        [InlineKeyboardButton("🔄 Refresh", callback_data="overage_cost")],
        [InlineKeyboardButton("📊 Server Management", callback_data="list_servers")],
        [InlineKeyboardButton("⬅️ Back", callback_data="start_menu")],
    ]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")


async def show_snapshots(query, context):
    images, pricing = await asyncio.gather(
        hetzner_api.list_images(),
        hetzner_api.get_pricing(),
    )
    per_gb = _image_price_per_gb(pricing)

    text = "📸 *SNAPSHOTS*\n\n"
    keyboard = [[InlineKeyboardButton("➕ Take Snapshot", callback_data="snap_new")]]

    if not images:
        text += "No snapshots yet.\n"
    else:
        total_size = sum(i.get("image_size") or 0 for i in images)
        text += (
            f"Total: {len(images)} | {total_size:.1f} GB | "
            f"€{total_size * per_gb:.2f}/month\n\n"
            f"Tap a snapshot to manage it:"
        )
        for img in images:
            s_emoji = "✅" if img.get("status") == "available" else "⏳"
            size = img.get("image_size") or 0
            label = img.get("description") or img.get("name") or str(img["id"])
            keyboard.append([InlineKeyboardButton(
                f"{s_emoji} {label} | {size:.1f} GB",
                callback_data=f"snap_{img['id']}",
            )])

    text += f"\n\n🕓 Updated: `{datetime.now().strftime('%H:%M:%S')}`"
    keyboard.append([InlineKeyboardButton("🔄 Refresh", callback_data="snapshots")])
    keyboard.append([InlineKeyboardButton("⬅️ Back to Menu", callback_data="start_menu")])
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")


async def show_snapshot_detail(query, context, image_id):
    img, pricing = await asyncio.gather(
        hetzner_api.get_image(image_id),
        hetzner_api.get_pricing(),
    )
    if not img:
        await query.edit_message_text(
            "⚠️ Snapshot not found.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="snapshots")]]),
        )
        return

    size = img.get("image_size") or 0
    cost = size * _image_price_per_gb(pricing)
    status = img.get("status", "unknown")
    s_emoji = "✅" if status == "available" else "⏳"
    created = img.get("created", "")[:16].replace("T", " ")
    source = img.get("created_from", {}) or {}

    text = (
        f"📸 *Snapshot Detail*\n\n"
        f"🏷 Name: `{img.get('description') or img.get('name') or image_id}`\n"
        f"🆔 ID: `{img['id']}`\n"
        f"🖥 Server: `{source.get('name', 'N/A')}`\n"
        f"💾 Size: `{size:.2f} GB`\n"
        f"💰 Cost: `€{cost:.2f}/month`\n"
        f"{s_emoji} Status: `{status.upper()}`\n"
        f"📅 Created: `{created}`\n"
    )
    keyboard = [
        [InlineKeyboardButton("🗑 Delete Snapshot", callback_data=f"snapdel_{image_id}")],
        [InlineKeyboardButton("⬅️ Back to Snapshots", callback_data="snapshots")],
    ]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")


async def delete_snapshot_confirm(query, context, image_id):
    img = await hetzner_api.get_image(image_id)
    label = (img.get("description") or img.get("name") or str(image_id)) if img else str(image_id)
    keyboard = [
        [
            InlineKeyboardButton("✅ Yes, delete it", callback_data=f"snapdel_confirm_{image_id}"),
            InlineKeyboardButton("❌ Cancel", callback_data=f"snap_{image_id}"),
        ]
    ]
    await query.edit_message_text(
        f"🗑 *Delete Snapshot*\n\n"
        f"Snapshot: `{label}`\n\n"
        f"⚠️ This cannot be undone. Are you sure?",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown",
    )


async def delete_snapshot(query, context, image_id):
    await query.edit_message_text("🗑 Deleting snapshot...")
    result = await hetzner_api.delete_image(image_id)
    keyboard = [[InlineKeyboardButton("⬅️ Back to Snapshots", callback_data="snapshots")]]
    if result is not None:
        await query.edit_message_text(
            "✅ Snapshot deleted successfully.",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
    else:
        await query.edit_message_text(
            "❌ Failed to delete snapshot. Check the logs and try again.",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )


async def snapshot_pick_server(query, context):
    servers = await hetzner_api.list_servers()
    if not servers:
        await query.edit_message_text("⚠️ No servers found or API error occurred.")
        return
    keyboard = []
    for s in servers:
        loc_name, flag = get_location_info(s.get("datacenter", {}).get("location", {}).get("name", ""))
        keyboard.append([InlineKeyboardButton(
            f"🖥 {s.get('name', 'Unnamed')} | {flag} {loc_name}",
            callback_data=f"snapcreate_{s['id']}",
        )])
    keyboard.append([InlineKeyboardButton("⬅️ Back to Snapshots", callback_data="snapshots")])
    await query.edit_message_text(
        "📸 *Take Snapshot*\n\nChoose a server:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown",
    )


async def create_snapshot(query, context, server_id):
    server = await hetzner_api.get_server(server_id)
    if not server:
        await query.edit_message_text("⚠️ Server not found or API error.")
        return
    name = server.get("name", "Server")
    description = f"{name} {datetime.now().strftime('%Y-%m-%d %H:%M')}"

    await query.edit_message_text(f"📸 Creating snapshot of `{name}`...", parse_mode="Markdown")
    result = await hetzner_api.create_snapshot(server_id, description)

    keyboard = [[InlineKeyboardButton("📸 View Snapshots", callback_data="snapshots")],
                [InlineKeyboardButton("🖥 Back to Server", callback_data=f"server_{server_id}")],
                [InlineKeyboardButton("⬅️ Back to Menu", callback_data="start_menu")]]
    if result:
        await query.edit_message_text(
            f"⏳ *Snapshot creation started*\n\n"
            f"🖥 Server: `{name}`\n"
            f"🏷 Name: `{description}`\n\n"
            f"The server keeps running. It can take several minutes — "
            f"the snapshot shows ⏳ in the list until it becomes ✅ available.",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown",
        )
    else:
        await query.edit_message_text(
            "❌ Failed to start snapshot creation. Check the logs and try again.",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )


async def rebuild_pick_image(query, context, server_id):
    server, images = await asyncio.gather(
        hetzner_api.get_server(server_id),
        hetzner_api.list_images("system"),
    )
    if not server:
        await query.edit_message_text("⚠️ Server not found or API error.")
        return
    arch = server.get("server_type", {}).get("architecture", "x86")
    usable = [
        i for i in images
        if i.get("architecture") == arch and not i.get("deprecated")
    ]
    keyboard = []
    row = []
    for img in sorted(usable, key=lambda i: i.get("name", "")):
        row.append(InlineKeyboardButton(
            img.get("description") or img.get("name"),
            callback_data=f"rebuildimg_{server_id}_{img.get('name')}",
        ))
        if len(row) == 2:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)
    keyboard.append([InlineKeyboardButton("⬅️ Back to Server", callback_data=f"server_{server_id}")])
    await query.edit_message_text(
        f"🔧 *Rebuild OS*\n\n"
        f"Server: `{server.get('name')}`\n\n"
        f"Choose the operating system image:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown",
    )


async def rebuild_confirm(query, context, server_id, image):
    server = await hetzner_api.get_server(server_id)
    name = server.get("name", "Server") if server else "Server"
    keyboard = [
        [
            InlineKeyboardButton("✅ Yes, rebuild it", callback_data=f"rebuildgo_{server_id}_{image}"),
            InlineKeyboardButton("❌ Cancel", callback_data=f"server_{server_id}"),
        ]
    ]
    await query.edit_message_text(
        f"🔧 *Rebuild OS*\n\n"
        f"Server: `{name}`\n"
        f"New image: `{image}`\n\n"
        f"🚨 *ALL DATA ON THE SERVER WILL BE ERASED* and the OS will be "
        f"reinstalled from scratch.\n\n"
        f"Are you absolutely sure?",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown",
    )


async def rebuild_go(query, context, server_id, image):
    await query.edit_message_text("🔧 Rebuilding server... this takes a minute or two.")
    result = await hetzner_api.rebuild_server(server_id, image)
    keyboard = [[InlineKeyboardButton("🖥 Back to Server", callback_data=f"server_{server_id}")]]
    if result:
        pw = result.get("root_password")
        text = f"✅ *Rebuild started* with `{image}`.\n\n"
        if pw:
            text += f"🔑 New root password:\n`{pw}`\n\n⚠️ Save it now — it won't be shown again."
        else:
            text += "Your SSH keys were installed, no new password was generated."
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    else:
        await query.edit_message_text(
            "❌ Rebuild failed. Check the logs and try again.",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )


_FAMILY_LABEL = {
    "cx": "CX — Intel shared",
    "cpx": "CPX — AMD shared",
    "cax": "CAX — ARM64 Ampere",
    "ccx": "CCX — Dedicated vCPU",
}


def _type_family(name):
    return "".join(c for c in name if c.isalpha()).lower()


def _type_price(stype, location):
    for p in stype.get("prices", []):
        if p.get("location") == location:
            return _gross(p.get("price_monthly", {}))
    prices = stype.get("prices", [])
    return _gross(prices[0].get("price_monthly", {})) if prices else 0.0


async def _resize_candidates(server):
    """Server types actually available in this server's datacenter,
    same architecture, not deprecated, excluding the current type."""
    types, dc = await asyncio.gather(
        hetzner_api.get_server_types(),
        hetzner_api.get_datacenter(server.get("datacenter", {}).get("name", "")),
    )
    avail = set()
    if dc:
        st = dc.get("server_types", {})
        avail = set(st.get("available_for_migration") or st.get("available") or [])
    cur = server.get("server_type", {})
    return [
        t for t in types
        if t.get("id") in avail
        and t.get("architecture") == cur.get("architecture")
        and not t.get("deprecation")
        and t.get("name") != cur.get("name")
    ]


async def resize_pick_family(query, context, server_id):
    server = await hetzner_api.get_server(server_id)
    if not server:
        await query.edit_message_text("⚠️ Server not found or API error.")
        return
    candidates = await _resize_candidates(server)
    if not candidates:
        await query.edit_message_text(
            "⚠️ No other plans are available for this server in its datacenter.",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("⬅️ Back to Server", callback_data=f"server_{server_id}")]]
            ),
        )
        return
    families = sorted({_type_family(t["name"]) for t in candidates})
    keyboard = [
        [InlineKeyboardButton(
            _FAMILY_LABEL.get(f, f.upper()),
            callback_data=f"resizef_{server_id}_{f}",
        )]
        for f in families
    ]
    keyboard.append([InlineKeyboardButton("⬅️ Back to Server", callback_data=f"server_{server_id}")])
    cur = server.get("server_type", {})
    await query.edit_message_text(
        f"⚖️ *Change Plan*\n\n"
        f"Server: `{server.get('name')}`\n"
        f"Current: `{cur.get('name')}` ({cur.get('cores')}C / {cur.get('memory'):.0f}GB / {cur.get('disk')}GB)\n\n"
        f"Choose a CPU family (only plans *available in this datacenter* are shown):",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown",
    )


async def resize_pick_type(query, context, server_id, family):
    server = await hetzner_api.get_server(server_id)
    if not server:
        await query.edit_message_text("⚠️ Server not found or API error.")
        return
    loc = server.get("datacenter", {}).get("location", {}).get("name", "")
    candidates = [t for t in await _resize_candidates(server) if _type_family(t["name"]) == family]
    candidates.sort(key=lambda t: (t.get("cores", 0), t.get("memory", 0)))
    cur = server.get("server_type", {})
    keyboard = []
    for t in candidates:
        arrow = "🔼" if t.get("memory", 0) > cur.get("memory", 0) else "🔽"
        keyboard.append([InlineKeyboardButton(
            f"{arrow} {t['name']} | {t.get('cores')}C / {t.get('memory'):.0f}GB / {t.get('disk')}GB | €{_type_price(t, loc):.2f}",
            callback_data=f"resizet_{server_id}_{t['name']}",
        )])
    keyboard.append([InlineKeyboardButton("⬅️ Back", callback_data=f"resize_{server_id}")])
    await query.edit_message_text(
        f"⚖️ *Change Plan* — {_FAMILY_LABEL.get(family, family.upper())}\n\n"
        f"Current: `{cur.get('name')}`\n\nChoose the new plan:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown",
    )


async def resize_confirm(query, context, server_id, new_type_name):
    server, types = await asyncio.gather(
        hetzner_api.get_server(server_id),
        hetzner_api.get_server_types(),
    )
    new_type = next((t for t in types if t.get("name") == new_type_name), None)
    if not server or not new_type:
        await query.edit_message_text("⚠️ Server or plan not found.")
        return
    cur = server.get("server_type", {})
    loc = server.get("datacenter", {}).get("location", {}).get("name", "")
    text = (
        f"⚖️ *Confirm Plan Change*\n\n"
        f"Server: `{server.get('name')}`\n\n"
        f"Current: `{cur.get('name')}` — {cur.get('cores')}C / {cur.get('memory'):.0f}GB / "
        f"{cur.get('disk')}GB — €{_type_price(cur, loc):.2f}\n"
        f"New: `{new_type['name']}` — {new_type.get('cores')}C / {new_type.get('memory'):.0f}GB / "
        f"{new_type.get('disk')}GB — €{_type_price(new_type, loc):.2f}\n\n"
        f"⚠️ The server will be powered off during the change.\n"
    )
    keyboard = [[InlineKeyboardButton(
        "✅ Change (CPU/RAM only — can downgrade later)",
        callback_data=f"resizego_{server_id}_{new_type_name}_0",
    )]]
    if new_type.get("disk", 0) > cur.get("disk", 0):
        text += "\n💡 Upgrading the disk too is permanent — you can never downgrade afterwards."
        keyboard.append([InlineKeyboardButton(
            "📈 Change + upgrade disk (PERMANENT)",
            callback_data=f"resizego_{server_id}_{new_type_name}_1",
        )])
    keyboard.append([InlineKeyboardButton("❌ Cancel", callback_data=f"server_{server_id}")])
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")


async def resize_go(query, context, server_id, new_type_name, upgrade_disk):
    steps = []

    async def log(line):
        steps.append(line)
        try:
            await query.edit_message_text("⚖️ *Changing Plan*\n\n" + "\n".join(steps), parse_mode="Markdown")
        except Exception:
            pass

    server = await hetzner_api.get_server(server_id)
    if not server:
        await query.edit_message_text("⚠️ Server not found or API error.")
        return
    was_running = server.get("status") == "running"

    if was_running:
        await log("🔴 Powering off server...")
        await hetzner_api.power_off(server_id)
        if not await hetzner_api.wait_for_status(server_id, "off", max_attempts=40):
            await log("❌ Server failed to power off.")
            return
        await log("✅ Server is OFF")

    await log(f"⚖️ Switching to `{new_type_name}`{' (with disk upgrade)' if upgrade_disk else ''}...")
    result = await hetzner_api.change_server_type(server_id, new_type_name, upgrade_disk=upgrade_disk)
    if not result or result.get("error"):
        msg = (result or {}).get("error", {}).get("message", "Unknown error")
        await log(f"❌ Change failed: {msg}")
        return

    for _ in range(30):
        await asyncio.sleep(5)
        server = await hetzner_api.get_server(server_id, fresh=True)
        if server and server.get("server_type", {}).get("name") == new_type_name:
            await log("✅ Plan changed successfully")
            break

    if was_running:
        await log("🟢 Powering server back on...")
        await hetzner_api.power_on(server_id)
        await hetzner_api.wait_for_status(server_id, "running", max_attempts=40)
        await log("✅ Server is RUNNING")

    keyboard = [[InlineKeyboardButton("🖥 Back to Server", callback_data=f"server_{server_id}")]]
    await query.edit_message_text(
        "⚖️ *Changing Plan*\n\n" + "\n".join(steps) + "\n\n🎉 *Done!*",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown",
    )


async def show_volumes(query, context, server_id):
    volumes, server, pricing = await asyncio.gather(
        hetzner_api.list_volumes(),
        hetzner_api.get_server(server_id),
        hetzner_api.get_pricing(),
    )
    if not server:
        await query.edit_message_text("⚠️ Server not found or API error.")
        return
    loc = server.get("datacenter", {}).get("location", {}).get("name", "")
    per_gb = _gross(pricing.get("volume", {}).get("price_per_gb_month", {}) or pricing.get("volume", {}))
    attached = [v for v in volumes if v.get("server") == server_id]
    free = [v for v in volumes if not v.get("server") and v.get("location", {}).get("name") == loc]

    text = f"💽 *Volumes — `{server.get('name')}`*\n\n"
    keyboard = [[InlineKeyboardButton("➕ New Volume", callback_data=f"volnew_{server_id}")]]

    if attached:
        text += "*Attached:*\n"
        for v in attached:
            text += (
                f"• `{v.get('name')}` | {v.get('size')} GB | "
                f"`{v.get('linux_device', '?')}` | €{v.get('size', 0) * per_gb:.2f}/mo\n"
            )
            keyboard.append([
                InlineKeyboardButton(f"🔌 Detach {v.get('name')}", callback_data=f"voldetach_{v['id']}_{server_id}"),
                InlineKeyboardButton("🗑 Delete", callback_data=f"voldel_{v['id']}_{server_id}"),
            ])
        text += "\n"
    if free:
        text += f"*Available in {loc} (not attached):*\n"
        for v in free:
            text += f"• `{v.get('name')}` | {v.get('size')} GB | €{v.get('size', 0) * per_gb:.2f}/mo\n"
            keyboard.append([
                InlineKeyboardButton(f"🔗 Attach {v.get('name')} ({v.get('size')}GB)",
                                     callback_data=f"volattach_{v['id']}_{server_id}"),
                InlineKeyboardButton("🗑 Delete", callback_data=f"voldel_{v['id']}_{server_id}"),
            ])
        text += "\n"
    if not attached and not free:
        text += "No volumes yet.\n\n"

    text += f"🕓 Updated: `{datetime.now().strftime('%H:%M:%S')}`"
    keyboard.append([InlineKeyboardButton("🔄 Refresh", callback_data=f"volmenu_{server_id}")])
    keyboard.append([InlineKeyboardButton("⬅️ Back to Server", callback_data=f"server_{server_id}")])
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")


async def volume_pick_size(query, context, server_id):
    keyboard = [
        [InlineKeyboardButton(f"{size} GB", callback_data=f"volnewc_{server_id}_{size}")
         for size in (10, 20, 50, 100)],
        [InlineKeyboardButton("⬅️ Back", callback_data=f"volmenu_{server_id}")],
    ]
    await query.edit_message_text(
        "💽 *New Volume*\n\nChoose the size (ext4, auto-mounted):",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown",
    )


async def volume_create(query, context, server_id, size):
    await query.edit_message_text(f"💽 Creating {size} GB volume...")
    name = f"vol-{datetime.now().strftime('%y%m%d%H%M%S')}"
    result = await hetzner_api.create_volume(name, size, server_id)
    keyboard = [[InlineKeyboardButton("💽 Back to Volumes", callback_data=f"volmenu_{server_id}")]]
    if result:
        device = result.get("volume", {}).get("linux_device", "?")
        await query.edit_message_text(
            f"✅ *Volume created & attached*\n\n"
            f"🏷 Name: `{name}`\n💾 Size: `{size} GB`\n📁 Device: `{device}`\n\n"
            f"It is formatted as ext4 and auto-mounted.",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown",
        )
    else:
        await query.edit_message_text(
            "❌ Volume creation failed. Check the logs and try again.",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )


async def volume_attach(query, context, volume_id, server_id):
    await query.edit_message_text("🔗 Attaching volume...")
    await hetzner_api.attach_volume(volume_id, server_id)
    await asyncio.sleep(3)
    await show_volumes(query, context, server_id)


async def volume_detach(query, context, volume_id, server_id):
    await query.edit_message_text("🔌 Detaching volume...")
    await hetzner_api.detach_volume(volume_id)
    await asyncio.sleep(3)
    await show_volumes(query, context, server_id)


async def volume_delete_confirm(query, context, volume_id, server_id):
    keyboard = [
        [
            InlineKeyboardButton("✅ Yes, delete it", callback_data=f"voldelgo_{volume_id}_{server_id}"),
            InlineKeyboardButton("❌ Cancel", callback_data=f"volmenu_{server_id}"),
        ]
    ]
    await query.edit_message_text(
        "🗑 *Delete Volume*\n\n"
        "⚠️ All data on the volume will be lost. If it is attached, it will "
        "be detached first.\n\nAre you sure?",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown",
    )


async def volume_delete(query, context, volume_id, server_id):
    await query.edit_message_text("🗑 Deleting volume...")
    volumes = await hetzner_api.list_volumes()
    vol = next((v for v in volumes if v.get("id") == volume_id), None)
    if vol and vol.get("server"):
        await hetzner_api.detach_volume(volume_id)
        await asyncio.sleep(5)
    result = await hetzner_api.delete_volume(volume_id)
    keyboard = [[InlineKeyboardButton("💽 Back to Volumes", callback_data=f"volmenu_{server_id}")]]
    if result is not None:
        await query.edit_message_text("✅ Volume deleted.", reply_markup=InlineKeyboardMarkup(keyboard))
    else:
        await query.edit_message_text(
            "❌ Failed to delete the volume. Check the logs and try again.",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )


async def show_server_fips(query, context, server_id):
    fips, server, servers = await asyncio.gather(
        hetzner_api.list_floating_ips(),
        hetzner_api.get_server(server_id),
        hetzner_api.list_servers(),
    )
    if not server:
        await query.edit_message_text("⚠️ Server not found or API error.")
        return
    server_names = {s["id"]: s.get("name", "?") for s in servers}

    text = f"🌐 *Floating IPs — `{server.get('name')}`*\n\n"
    keyboard = []
    if not fips:
        text += "No floating IPs exist yet. Create one from the main menu → 🌐 Floating IPs.\n"
    else:
        for f in fips:
            owner = f.get("server")
            if owner == server_id:
                state = "🔗 attached to this server"
                btn = InlineKeyboardButton(f"🔌 Detach {f.get('ip')}", callback_data=f"fipun_{f['id']}_{server_id}")
            elif owner:
                state = f"🔗 on `{server_names.get(owner, owner)}`"
                btn = InlineKeyboardButton(f"🔗 Move {f.get('ip')} here", callback_data=f"fipas_{f['id']}_{server_id}")
            else:
                state = "🆓 unassigned"
                btn = InlineKeyboardButton(f"🔗 Attach {f.get('ip')}", callback_data=f"fipas_{f['id']}_{server_id}")
            text += f"• `{f.get('ip')}` ({f.get('type')}) — {state}\n"
            keyboard.append([btn])

    text += f"\n🕓 Updated: `{datetime.now().strftime('%H:%M:%S')}`"
    keyboard.append([InlineKeyboardButton("🔄 Refresh", callback_data=f"srvfip_{server_id}")])
    keyboard.append([InlineKeyboardButton("⬅️ Back to Server", callback_data=f"server_{server_id}")])
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")


async def server_fip_assign(query, context, fip_id, server_id):
    await query.edit_message_text("🔗 Attaching floating IP...")
    await hetzner_api.assign_floating_ip(fip_id, server_id)
    await asyncio.sleep(2)
    await show_server_fips(query, context, server_id)


async def server_fip_unassign(query, context, fip_id, server_id):
    await query.edit_message_text("🔌 Detaching floating IP...")
    await hetzner_api.unassign_floating_ip(fip_id)
    await asyncio.sleep(2)
    await show_server_fips(query, context, server_id)


async def backup_toggle_confirm(query, context, server_id):
    server = await hetzner_api.get_server(server_id)
    if not server:
        await query.edit_message_text("⚠️ Server not found or API error.")
        return
    enabled = bool(server.get("backup_window"))
    name = server.get("name", "Server")
    if enabled:
        text = (
            f"💾 *Disable Backups*\n\n"
            f"Server: `{name}`\n\n"
            f"⚠️ Automatic backups will stop and *existing backups will be deleted*.\n\n"
            f"Are you sure?"
        )
        yes = InlineKeyboardButton("✅ Yes, disable", callback_data=f"backupgo_{server_id}_off")
    else:
        text = (
            f"💾 *Enable Backups*\n\n"
            f"Server: `{name}`\n\n"
            f"Hetzner keeps 7 daily backups. Cost: *+20% of the server plan price* per month.\n\n"
            f"Enable backups?"
        )
        yes = InlineKeyboardButton("✅ Yes, enable", callback_data=f"backupgo_{server_id}_on")
    keyboard = [[yes, InlineKeyboardButton("❌ Cancel", callback_data=f"server_{server_id}")]]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")


async def backup_toggle_go(query, context, server_id, mode):
    await query.edit_message_text("💾 Updating backup settings...")
    if mode == "on":
        result = await hetzner_api.enable_backup(server_id)
    else:
        result = await hetzner_api.disable_backup(server_id)
    if result:
        await asyncio.sleep(2)
        await show_server_detail(query, context, server_id, refresh=True)
    else:
        await query.edit_message_text(
            "❌ Failed to change backup settings. Check the logs and try again.",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("🖥 Back to Server", callback_data=f"server_{server_id}")]]
            ),
        )


_IP_LABEL = {"fip": ("🌐", "Floating IP"), "pip": ("📍", "Primary IP")}


async def _fetch_ips(kind):
    if kind == "fip":
        return await hetzner_api.list_floating_ips()
    return await hetzner_api.list_primary_ips()


def _ip_assignee_id(kind, ip):
    return ip.get("server") if kind == "fip" else ip.get("assignee_id")


def _ip_location_name(kind, ip):
    if kind == "fip":
        return ip.get("home_location", {}).get("name", "")
    return ip.get("datacenter", {}).get("location", {}).get("name", "")


async def show_ip_list(query, context, kind):
    emoji, label = _IP_LABEL[kind]
    ips, pricing, servers = await asyncio.gather(
        _fetch_ips(kind),
        hetzner_api.get_pricing(),
        hetzner_api.list_servers(),
    )
    server_names = {s["id"]: s.get("name", "?") for s in servers}

    existing_ids = {ip["id"] for ip in ips}
    sel = {i for i in context.user_data.get(f"{kind}_sel", set()) if i in existing_ids}
    context.user_data[f"{kind}_sel"] = sel

    text = f"{emoji} *{label}s*\n\n"
    keyboard = [[InlineKeyboardButton(f"➕ Create {label}s", callback_data=f"{kind}_new")]]

    if not ips:
        text += f"No {label.lower()}s yet.\n"
    else:
        total_cost = 0
        for ip in ips:
            aid = _ip_assignee_id(kind, ip)
            loc = _ip_location_name(kind, ip)
            _, flag = get_location_info(loc)
            if kind == "fip":
                price = _floating_ip_price(pricing, ip)
            else:
                price = 0 if aid else _primary_ip_price(pricing, ip)
            total_cost += price
            attach = f"🔗 {server_names.get(aid, aid)}" if aid else "🆓 unassigned"
            cost_str = "free (on server)" if (kind == "pip" and aid) else f"€{price:.2f}/mo"
            text += f"• `{ip.get('ip')}` {flag} {ip.get('type')} | {attach} | {cost_str}\n"
            keyboard.append([InlineKeyboardButton(
                f"{'✅' if ip['id'] in sel else '⬜'} {ip.get('ip')}",
                callback_data=f"{kind}tog_{ip['id']}",
            )])
        text += f"\nTotal: {len(ips)} | €{total_cost:.2f}/month\n"
        text += "\nTap IPs to select them, then delete together."

    if sel:
        keyboard.append([
            InlineKeyboardButton(f"🗑 Delete Selected ({len(sel)})", callback_data=f"{kind}delsel"),
            InlineKeyboardButton("♻️ Clear", callback_data=f"{kind}clear"),
        ])
    text += f"\n\n🕓 Updated: `{datetime.now().strftime('%H:%M:%S')}`"
    keyboard.append([InlineKeyboardButton("🔄 Refresh", callback_data=f"{kind}s")])
    keyboard.append([InlineKeyboardButton("⬅️ Back to Menu", callback_data="start_menu")])
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")


async def ip_toggle(query, context, kind, ip_id):
    sel = context.user_data.setdefault(f"{kind}_sel", set())
    sel.symmetric_difference_update({ip_id})
    await show_ip_list(query, context, kind)


async def ip_new_type(query, context, kind):
    emoji, label = _IP_LABEL[kind]
    keyboard = [
        [
            InlineKeyboardButton("IPv4", callback_data=f"{kind}newt_ipv4"),
            InlineKeyboardButton("IPv6", callback_data=f"{kind}newt_ipv6"),
        ],
        [InlineKeyboardButton("⬅️ Back", callback_data=f"{kind}s")],
    ]
    await query.edit_message_text(
        f"{emoji} *Create {label}s*\n\nChoose the IP type:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown",
    )


async def ip_new_place(query, context, kind, ip_type):
    emoji, label = _IP_LABEL[kind]
    if kind == "fip":
        places = [loc.get("name", "") for loc in await hetzner_api.list_locations()]
    else:
        # primary IPs are created in a specific datacenter
        places = [dc.get("name", "") for dc in await hetzner_api.list_datacenters()]
    keyboard = []
    row = []
    for place in places:
        if not place:
            continue
        _, flag = get_location_info(place.split("-")[0])
        row.append(InlineKeyboardButton(f"{flag} {place}", callback_data=f"{kind}newl_{ip_type}_{place}"))
        if len(row) == 2:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)
    keyboard.append([InlineKeyboardButton("⬅️ Back", callback_data=f"{kind}_new")])
    await query.edit_message_text(
        f"{emoji} *Create {label}s* ({ip_type})\n\nChoose the location:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown",
    )


async def ip_new_count(query, context, kind, ip_type, place):
    emoji, label = _IP_LABEL[kind]
    keyboard = [
        [InlineKeyboardButton(str(n), callback_data=f"{kind}newc_{ip_type}_{place}_{n}")
         for n in (1, 2, 3, 5)],
        [InlineKeyboardButton("⬅️ Back", callback_data=f"{kind}newt_{ip_type}")],
    ]
    await query.edit_message_text(
        f"{emoji} *Create {label}s* ({ip_type} @ {place})\n\nHow many?",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown",
    )


async def ip_create(query, context, kind, ip_type, place, count):
    emoji, label = _IP_LABEL[kind]
    await query.edit_message_text(f"{emoji} Creating {count} {label.lower()}(s)...")
    stamp = datetime.now().strftime('%y%m%d%H%M%S')
    lines = []
    ok = 0
    for i in range(count):
        name = f"{kind}-{stamp}-{i + 1}"
        if kind == "fip":
            result = await hetzner_api.create_floating_ip(ip_type, place, name)
            created = (result or {}).get("floating_ip", {})
        else:
            result = await hetzner_api.create_primary_ip(ip_type, place, name)
            created = (result or {}).get("primary_ip", {})
        if result:
            ok += 1
            lines.append(f"✅ `{created.get('ip', name)}`")
        else:
            lines.append(f"❌ {name} — creation failed")
    text = (
        f"{emoji} *Create {label}s — done ({ok}/{count})*\n\n" + "\n".join(lines)
    )
    keyboard = [
        [InlineKeyboardButton(f"{emoji} View {label}s", callback_data=f"{kind}s")],
        [InlineKeyboardButton("⬅️ Back to Menu", callback_data="start_menu")],
    ]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")


async def ip_delete_confirm(query, context, kind):
    emoji, label = _IP_LABEL[kind]
    sel = context.user_data.get(f"{kind}_sel", set())
    if not sel:
        await show_ip_list(query, context, kind)
        return
    ips = await _fetch_ips(kind)
    chosen = [ip for ip in ips if ip["id"] in sel]
    lines = "\n".join(f"• `{ip.get('ip')}`" for ip in chosen)
    note = ""
    if kind == "pip" and any(_ip_assignee_id(kind, ip) for ip in chosen):
        note = "\n⚠️ Primary IPs attached to a server cannot be deleted — those will fail."
    keyboard = [
        [
            InlineKeyboardButton(f"✅ Yes, delete {len(chosen)}", callback_data=f"{kind}delsel_yes"),
            InlineKeyboardButton("❌ Cancel", callback_data=f"{kind}s"),
        ]
    ]
    await query.edit_message_text(
        f"🗑 *Delete {label}s*\n\n{lines}\n{note}\n"
        f"⚠️ This cannot be undone. Are you sure?",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown",
    )


async def ip_delete_selected(query, context, kind):
    emoji, label = _IP_LABEL[kind]
    sel = context.user_data.get(f"{kind}_sel", set())
    if not sel:
        await show_ip_list(query, context, kind)
        return
    await query.edit_message_text(f"🗑 Deleting {len(sel)} {label.lower()}(s)...")
    ips = await _fetch_ips(kind)
    ip_by_id = {ip["id"]: ip for ip in ips}
    lines = []
    ok = 0
    for ip_id in sorted(sel):
        addr = ip_by_id.get(ip_id, {}).get("ip", ip_id)
        if kind == "fip":
            result = await hetzner_api.delete_floating_ip(ip_id)
        else:
            result = await hetzner_api.delete_primary_ip(ip_id)
        if result is not None:
            ok += 1
            lines.append(f"✅ `{addr}` deleted")
        else:
            lines.append(f"❌ `{addr}` failed (still attached?)")
    context.user_data[f"{kind}_sel"] = set()
    text = f"🗑 *Delete {label}s — done ({ok}/{len(lines)})*\n\n" + "\n".join(lines)
    keyboard = [
        [InlineKeyboardButton(f"{emoji} View {label}s", callback_data=f"{kind}s")],
        [InlineKeyboardButton("⬅️ Back to Menu", callback_data="start_menu")],
    ]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")


async def show_start_menu(query):
    keyboard = _main_menu_keyboard()
    await query.edit_message_text(
        "🚀 *Hetzner Server Manager Bot*\n\n"
        "Manage your Hetzner Cloud servers with ease.\n"
        "Monitor traffic, reset limits, and control server states.\n\n"
        "Click below to access your server panel.",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown",
    )
