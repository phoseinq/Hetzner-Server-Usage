import asyncio
import calendar
import logging
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import BadRequest
from telegram.ext import ContextTypes, ConversationHandler
from config import Config
from hetzner_api import hetzner_api, set_account, account_count, account_name, all_apis
from utils import (
    format_traffic, get_traffic_emoji, get_location_info,
    get_location, location_name, traffic_limit_tb,
    traffic_price_per_tb, overage_cost, type_family, type_price,
)
from server_manager import reset_server_traffic
from overage_tracker import overage_tracker
from price_store import price_store
from shell_handler import console_entry, active_sessions

logger = logging.getLogger(__name__)


async def _edit(query, text, **kwargs):
    """edit_message_text that tolerates an unchanged message.

    Telegram rejects an edit whose text and markup are identical to what is
    already on screen, which is exactly what a Refresh button produces when
    nothing moved.
    """
    try:
        await query.edit_message_text(text, **kwargs)
    except BadRequest as e:
        if "not modified" not in str(e).lower():
            raise
        try:
            await query.answer("Already up to date")
        except Exception:
            pass


def _net(price_dict):
    """Price before VAT. VAT is added once, at the end of the cost report."""
    try:
        return float(price_dict.get("net", 0) or 0)
    except (TypeError, ValueError):
        return 0.0


def _vat_rate(pricing):
    try:
        return float(pricing.get("vat_rate") or 0)
    except (TypeError, ValueError):
        return 0.0


def _api_price(server):
    """List price for the location this server actually runs in."""
    return _type_price(server.get("server_type", {}), location_name(server))


def _server_price(server):
    """Monthly price of a server as billed, in EUR.

    A manual override wins over the API: the API always reports today's list
    price, while an old server keeps the price it was ordered at.
    """
    return price_store.apply(server.get("id"), _api_price(server))


def _image_price_per_gb(pricing):
    return _net(pricing.get("image", {}).get("price_per_gb_month", {}))


def _floating_ip_price(pricing, fip):
    loc = fip.get("home_location", {}).get("name")
    for entry in pricing.get("floating_ips", []):
        if entry.get("type") == fip.get("type"):
            for p in entry.get("prices", []):
                if p.get("location") == loc:
                    return _net(p.get("price_monthly", {}))
    return _net(pricing.get("floating_ip", {}).get("price_monthly", {}))


def _primary_ip_price(pricing, pip):
    loc = location_name(pip)
    for entry in pricing.get("primary_ips", []):
        if entry.get("type") == pip.get("type"):
            for p in entry.get("prices", []):
                if p.get("location") == loc:
                    return _net(p.get("price_monthly", {}))
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

    # every server-scoped action runs against the account the admin picked
    set_account(context.user_data.get("acct", 0))

    if data.startswith("acct_"):
        context.user_data["acct"] = int(data.split("_")[1])
        set_account(context.user_data["acct"])
        await show_server_list(query, context)
    elif data == "list_servers":
        if account_count() > 1:
            await show_account_picker(query, context)
        else:
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
    # same buttons as the price conversation, for a message left over from
    # before a restart — the conversation state is gone but the panel is not
    elif data.startswith("priceclear_"):
        sid = int(data.split("_")[1])
        price_store.clear(sid)
        await show_server_detail(query, context, sid)
    elif data.startswith("pricecancel_"):
        await show_server_detail(query, context, int(data.split("_")[1]))
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
    elif data.startswith("srvsnap_"):
        await show_server_snapshots(query, context, int(data.split("_")[1]))
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
    set_account(context.user_data.get("acct", 0))
    server_id = int(query.data.split("_")[1])
    server = await hetzner_api.get_server(server_id)
    if not server:
        await _edit(query, "⚠️ Server not found.")
        return ConversationHandler.END
    if server.get("status") != "running":
        await query.answer("⚠️ Server must be RUNNING to open a console.", show_alert=True)
        return ConversationHandler.END
    ip = server.get("public_net", {}).get("ipv4", {}).get("ip", "")
    name = server.get("name", "Server")
    return await console_entry(query, context, server_id, ip, name)


async def show_account_picker(query, context):
    keyboard = [[InlineKeyboardButton(f"🔑 {account_name(i)}", callback_data=f"acct_{i}")]
                for i in range(account_count())]
    keyboard.append([InlineKeyboardButton("⬅️ Back to Menu", callback_data="start_menu")])
    await _edit(query, 
        "🔑 *Choose a Hetzner account*",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown",
    )


async def show_server_list(query, context, page=0):
    servers = await hetzner_api.list_servers()
    if not servers:
        await _edit(query, "⚠️ No servers found or API error occurred.")
        return

    items_per_page = 10
    total_pages = (len(servers) - 1) // items_per_page + 1
    page_servers = servers[page * items_per_page:(page + 1) * items_per_page]

    keyboard = []
    for s in page_servers:
        tb = s.get("outgoing_traffic", 0) / (1024 ** 4)
        limit_tb = traffic_limit_tb(s)
        emoji = get_traffic_emoji(tb, limit_tb)
        loc_name, flag = get_location_info(get_location(s))
        keyboard.append([InlineKeyboardButton(
            f"{emoji} {s.get('name','Unnamed')} | {flag} {loc_name} | {format_traffic(s.get('outgoing_traffic',0), limit_tb)}",
            callback_data=f"server_{s['id']}",
        )])

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️ Previous", callback_data=f"page_{page-1}"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton("Next ➡️", callback_data=f"page_{page+1}"))
    if nav:
        keyboard.append(nav)
    if account_count() > 1:
        keyboard.append([InlineKeyboardButton("🔑 Switch Account", callback_data="list_servers")])
    keyboard.append([InlineKeyboardButton("⬅️ Back to Menu", callback_data="start_menu")])

    header = "📋 *SERVER LIST*"
    if account_count() > 1:
        header += f"\n🔑 Account: `{account_name(context.user_data.get('acct', 0))}`"
    await _edit(query, 
        header + "\n",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown",
    )


async def show_server_detail(query, context, server_id, refresh=False):
    server = await hetzner_api.get_server(server_id, fresh=refresh)
    if not server:
        await _edit(query, "⚠️ Server not found or API error.")
        return

    name   = server.get("name", "Unnamed")
    status = server.get("status", "unknown")
    stype  = server.get("server_type", {}).get("name", "Unknown")
    loc_name, flag = get_location_info(get_location(server))
    traffic_bytes = server.get("outgoing_traffic", 0)
    traffic_tb    = traffic_bytes / (1024 ** 4)
    limit_tb      = traffic_limit_tb(server)
    traffic_pct   = (traffic_tb / limit_tb) * 100
    emoji         = get_traffic_emoji(traffic_tb, limit_tb)
    overage_tracker.update_live_overage(server_id, overage_cost(server))
    overage_eur   = overage_tracker.get_server_month_overage(server_id)
    ip     = server.get("public_net", {}).get("ipv4", {}).get("ip", "N/A")
    cores  = server.get("server_type", {}).get("cores", "N/A")
    memory = server.get("server_type", {}).get("memory", "N/A")
    disk   = server.get("server_type", {}).get("disk", "N/A")

    price = _server_price(server)
    custom_price = price_store.get(server_id) is not None
    monthly_price = f"`€{price:.2f}/month`" if price else "`N/A`"
    if custom_price:
        monthly_price += " ✏️"

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
        f"💰 *Pricing* (excl. VAT)\n"
        f"📦 Server Cost: {monthly_price}\n"
        f"📊 Overage This Month: `€{overage_eur:.2f}` (€{traffic_price_per_tb(server):.2f}/TB)\n\n"
        f"{emoji} *Traffic Usage*\n"
        f"📊 {format_traffic(traffic_bytes, limit_tb)} ({traffic_pct:.1f}%)\n"
    )

    keyboard = [
        [
            InlineKeyboardButton("♻️ Reset Traffic", callback_data=f"reset_{server_id}"),
            InlineKeyboardButton("📸 Snapshots", callback_data=f"srvsnap_{server_id}"),
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
            InlineKeyboardButton(
                "💰 Edit Price ✏️" if custom_price else "💰 Edit Price",
                callback_data=f"priceset_{server_id}",
            ),
        ],
        [
            InlineKeyboardButton("🔄 Refresh", callback_data=f"refresh_{server_id}"),
            InlineKeyboardButton("⬅️ Back to List", callback_data="list_servers"),
        ],
    ]

    await _edit(query, 
        text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown",
    )


async def power_action(query, context, server_id, action):
    await _edit(query, f"⚙️ {'Starting' if action == 'on' else 'Stopping'} server...")
    result = await (hetzner_api.power_on(server_id) if action == "on" else hetzner_api.power_off(server_id))
    if result:
        await hetzner_api.wait_for_status(server_id, "running" if action == "on" else "off")
        await show_server_detail(query, context, server_id, refresh=True)
    else:
        await _edit(query, "❌ Power action failed. Please try again.")


async def reset_traffic(query, context, server_id):
    await _edit(query, "🔄 Starting traffic reset process...\n\nThis may take several minutes.")

    async def update_progress(logs):
        log_text = "\n".join(f"{e} {m}" for e, m in logs)
        try:
            await _edit(query, f"*Traffic Reset Process*\n\n{log_text}", parse_mode="Markdown")
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
    await _edit(query, final, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")


async def reset_password(query, context, server_id):
    server = await hetzner_api.get_server(server_id)
    name = server.get("name", "Server") if server else "Server"
    keyboard = [
        [
            InlineKeyboardButton("✅ Yes, reset it", callback_data=f"resetpw_confirm_{server_id}"),
            InlineKeyboardButton("❌ Cancel", callback_data=f"server_{server_id}"),
        ]
    ]
    await _edit(query, 
        f"🔑 *Reset Root Password*\n\n"
        f"Server: `{name}`\n\n"
        f"⚠️ This will generate a new random root password.\n"
        f"The server must be running with qemu-guest-agent installed.\n\n"
        f"Are you sure?",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown",
    )


async def reset_password_confirm(query, context, server_id):
    await _edit(query, "🔑 Resetting root password...", parse_mode="Markdown")
    result = await hetzner_api.reset_password(server_id)
    keyboard = [[InlineKeyboardButton("⬅️ Back to Server", callback_data=f"server_{server_id}")]]
    if result and result.get("root_password"):
        pw = result["root_password"]
        await _edit(query, 
            f"✅ *Password Reset Successful*\n\n"
            f"🔑 New root password:\n`{pw}`\n\n"
            f"⚠️ Save this password now — it won't be shown again.",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown",
        )
    else:
        await _edit(query, 
            "❌ *Password reset failed.*\n\n"
            "Make sure qemu-guest-agent is installed and the server is running.",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown",
        )


async def show_overage_cost(query, context):
    multi = account_count() > 1
    total_server_cost = backup_cost = 0
    backup_count = 0
    snap_size = floating_cost = 0
    assigned_pip_count = 0
    vol_size = 0
    snap_count = fip_count = vol_count = 0
    server_details = []
    pricing = {}
    any_servers = False

    snapshot_cost = extra_primary_cost = volume_cost = 0
    unassigned_pip_count = 0
    vat_amount = 0
    vat_rates = set()

    for idx, name, api in all_apis():
        servers, pr, snapshots, floating_ips, primary_ips, volumes = await asyncio.gather(
            api.list_servers(), api.get_pricing(), api.list_images(),
            api.list_floating_ips(), api.list_primary_ips(), api.list_volumes(),
        )
        pr = pr or {}
        pricing = pr or pricing
        if servers:
            any_servers = True
        try:
            backup_pct = float(pr.get("server_backup", {}).get("percentage", 20) or 20)
        except (TypeError, ValueError):
            backup_pct = 20.0

        acct_cost = 0
        if multi and servers:
            server_details.append(f"\n🔑 *{name}*")
        for s in servers:
            sname = s.get("name", "Unnamed")
            stype = s.get("server_type", {}).get("name", "?")
            limit_tb = traffic_limit_tb(s)
            sp = _server_price(s)
            total_server_cost += sp
            acct_cost += sp
            overage_tracker.update_live_overage(s["id"], overage_cost(s))
            ov_month = overage_tracker.get_server_month_overage(s["id"])
            acct_cost += ov_month
            edited = " ✏️" if price_store.get(s["id"]) is not None else ""
            line = f"• `{sname}` ({stype}): €{sp:.2f}{edited} | {format_traffic(s.get('outgoing_traffic', 0), limit_tb)}"
            if s.get("backup_window"):
                backup_count += 1
                backup_cost += sp * backup_pct / 100
                acct_cost += sp * backup_pct / 100
                line += " | 💾"
            if ov_month > 0:
                line += f" | ⚠️ €{ov_month:.2f} overage"
            server_details.append(line)

        # every rate below is this account's own, so an account that is not
        # charged VAT does not inherit another account's rate
        acct_snap_size = sum(i.get("image_size") or 0 for i in snapshots)
        acct_snapshot_cost = acct_snap_size * _image_price_per_gb(pr)
        acct_floating = sum(_floating_ip_price(pr, f) for f in floating_ips)
        acct_unassigned = [p for p in primary_ips if not p.get("assignee_id")]
        acct_pip_cost = sum(_primary_ip_price(pr, p) for p in acct_unassigned)
        acct_vol_size = sum(v.get("size") or 0 for v in volumes)
        acct_volume_cost = acct_vol_size * _net(
            pr.get("volume", {}).get("price_per_gb_month", {}) or pr.get("volume", {})
        )
        acct_cost += acct_snapshot_cost + acct_floating + acct_pip_cost + acct_volume_cost

        rate = _vat_rate(pr)
        vat_rates.add(rate)
        vat_amount += acct_cost * rate / 100

        snap_size += acct_snap_size
        snap_count += len(snapshots)
        snapshot_cost += acct_snapshot_cost
        floating_cost += acct_floating
        fip_count += len(floating_ips)
        assigned_pip_count += len([p for p in primary_ips if p.get("assignee_id")])
        unassigned_pip_count += len(acct_unassigned)
        extra_primary_cost += acct_pip_cost
        vol_size += acct_vol_size
        vol_count += len(volumes)
        volume_cost += acct_volume_cost

    if not any_servers:
        await _edit(query, "⚠️ No servers found or API error occurred.")
        return

    monthly_overage = overage_tracker.get_current_month_overage()
    monthly_avoided = overage_tracker.get_current_month_avoided()
    total_historic = overage_tracker.get_total_overage()
    total_avoided = overage_tracker.get_total_avoided()
    monthly_breakdown = overage_tracker.get_monthly_breakdown()

    total_usage = (
        total_server_cost + monthly_overage + backup_cost
        + snapshot_cost + floating_cost + extra_primary_cost + volume_cost
    )

    now = datetime.now()
    days_in_month = calendar.monthrange(now.year, now.month)[1]
    projected_overage = monthly_overage / now.day * days_in_month

    primary_line = f"📍 Primary IPs: {assigned_pip_count} on servers (free)"
    if unassigned_pip_count:
        primary_line += f" | {unassigned_pip_count} unassigned → €{extra_primary_cost:.2f}"

    # one label only when every account is charged the same; mixed accounts
    # get the amount without a rate, because no single rate describes it
    charged = sorted(r for r in vat_rates if r)
    vat_label = f"VAT {charged[0]:.0f}%" if len(set(charged)) == 1 else "VAT"
    text = (
        f"💸 *COST REPORT*\n\n"
        f"📦 *Servers (This Month)*\n" + "\n".join(server_details) + "\n\n"
        f"🧩 *Other Resources*\n"
        f"📸 Snapshots ({snap_count}): {snap_size:.1f} GB → €{snapshot_cost:.2f}\n"
        f"💽 Volumes ({vol_count}): {vol_size} GB → €{volume_cost:.2f}\n"
        f"💾 Backups ({backup_count} servers): €{backup_cost:.2f}\n"
        f"🌐 Floating IPs ({fip_count}): €{floating_cost:.2f}\n"
        f"{primary_line}\n\n"
        f"📊 *Summary* (excl. VAT)\n"
        f"📦 Server costs: €{total_server_cost:.2f}\n"
        f"📈 Overage: €{monthly_overage:.2f}\n"
        f"📸 Snapshots: €{snapshot_cost:.2f}\n"
        f"💽 Volumes: €{volume_cost:.2f}\n"
        f"💾 Backups: €{backup_cost:.2f}\n"
        f"🌐 Floating IPs: €{floating_cost:.2f}\n"
    )
    if unassigned_pip_count:
        text += f"📍 Extra primary IPs: €{extra_primary_cost:.2f}\n"
    if vat_amount:
        text += f"🧾 Subtotal: €{total_usage:.2f}\n"
        text += f"➕ {vat_label}: €{vat_amount:.2f}\n"
    text += f"💰 *Total: €{total_usage + vat_amount:.2f}*\n\n"
    if monthly_overage > 0:
        text += (
            f"🔮 *Projected Month-End Overage*\n"
            f"~€{projected_overage:.2f} at the current usage rate\n\n"
        )
    if monthly_avoided:
        text += (
            f"♻️ *Saved by Traffic Resets*\n"
            f"€{monthly_avoided:.2f} this month is not billed — the counter was reset "
            f"before Hetzner charged it.\n\n"
        )
    text += f"🔴 *Total Overage Paid (All Time)*\n€{total_historic:.2f}\n"
    if total_avoided:
        text += f"♻️ *Total Saved by Resets*\n€{total_avoided:.2f}\n"
    if monthly_breakdown:
        text += "\n*Billed in Previous Months:*\n"
        for month, cost in monthly_breakdown[:6]:
            text += f"• {month}: €{cost:.2f}\n"
    if vat_amount and multi:
        text += (
            "\n_Every price above is net. VAT is added once per account, at the rate "
            "Hetzner reports for each — accounts it reports no VAT for are billed net._\n"
        )
    elif vat_amount:
        text += (
            f"\n_Every price above is net; {vat_label} is the rate Hetzner reports for "
            "this account, added once in the total._\n"
        )
    else:
        text += "\n_Hetzner reports no VAT for this account, so the total is net._\n"
    if price_store.all():
        text += "_✏️ marks a price set by hand in the server's own panel._\n"
    text += f"\n🕓 Updated: `{now.strftime('%H:%M:%S')}`"

    keyboard = [
        [InlineKeyboardButton("🔄 Refresh", callback_data="overage_cost")],
        [InlineKeyboardButton("📊 Server Management", callback_data="list_servers")],
        [InlineKeyboardButton("⬅️ Back", callback_data="start_menu")],
    ]
    await _edit(query, text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")


WAIT_PRICE = 100


async def price_ask(update, context):
    """Entry point of the price conversation: ask this server's real price."""
    query = update.callback_query
    if query.from_user.id != Config.ADMIN_ID:
        await query.answer("⛔ Unauthorized", show_alert=True)
        return ConversationHandler.END
    await query.answer()
    set_account(context.user_data.get("acct", 0))
    server_id = int(query.data.split("_")[1])
    context.user_data["price_server"] = server_id

    server = await hetzner_api.get_server(server_id)
    if not server:
        await _edit(query, "⚠️ Server not found or API error.")
        return ConversationHandler.END

    stype = server.get("server_type", {}).get("name", "?")
    api_price = _api_price(server)
    current = price_store.get(server_id)
    text = (
        f"💰 *Price* — `{server.get('name')}`\n\n"
        f"Type: `{stype}` | Hetzner list price: `€{api_price:.2f}/month`\n"
    )
    if current is not None:
        text += f"Currently set to: `€{current:.2f}/month` ✏️\n"
    text += (
        "\nSend the monthly price you are actually billed for *this server*, "
        "in EUR and *without VAT* — e.g. `3.79`. VAT is added once, in the "
        "cost report total."
    )

    keyboard = [[InlineKeyboardButton("⬅️ Cancel", callback_data=f"pricecancel_{server_id}")]]
    if current is not None:
        keyboard.insert(0, [InlineKeyboardButton(
            f"♻️ Use list price (€{api_price:.2f})", callback_data=f"priceclear_{server_id}"
        )])
    await _edit(query, text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    return WAIT_PRICE


async def price_recv(update, context):
    server_id = context.user_data.get("price_server")
    raw = (update.message.text or "").strip().replace("€", "").replace(",", ".")
    try:
        value = float(raw)
        if value < 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("⚠️ Send a number, e.g. `3.79`.", parse_mode="Markdown")
        return WAIT_PRICE

    price_store.set(server_id, value)
    keyboard = [[InlineKeyboardButton("⬅️ Back to Server", callback_data=f"server_{server_id}"),
                 InlineKeyboardButton("💸 Cost Report", callback_data="overage_cost")]]
    await update.message.reply_text(
        f"✅ Price set to €{value:.2f}/month for this server.",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown",
    )
    return ConversationHandler.END


async def price_clear(update, context):
    query = update.callback_query
    await query.answer()
    set_account(context.user_data.get("acct", 0))
    server_id = int(query.data.split("_")[1])
    price_store.clear(server_id)
    await show_server_detail(query, context, server_id)
    return ConversationHandler.END


async def price_cancel(update, context):
    query = update.callback_query
    await query.answer()
    set_account(context.user_data.get("acct", 0))
    await show_server_detail(query, context, int(query.data.split("_")[1]))
    return ConversationHandler.END


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
            lock = "🔒 " if img.get("protection", {}).get("delete") else ""
            size = img.get("image_size") or 0
            label = img.get("description") or img.get("name") or str(img["id"])
            keyboard.append([InlineKeyboardButton(
                f"{s_emoji} {lock}{label} | {size:.1f} GB",
                callback_data=f"snap_{img['id']}",
            )])

    text += f"\n\n🕓 Updated: `{datetime.now().strftime('%H:%M:%S')}`"
    keyboard.append([InlineKeyboardButton("🔄 Refresh", callback_data="snapshots")])
    keyboard.append([InlineKeyboardButton("⬅️ Back to Menu", callback_data="start_menu")])
    await _edit(query, text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")


async def show_snapshot_detail(query, context, image_id):
    img, pricing = await asyncio.gather(
        hetzner_api.get_image(image_id),
        hetzner_api.get_pricing(),
    )
    if not img:
        await _edit(query, 
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

    protected = bool(img.get("protection", {}).get("delete"))
    text = (
        f"📸 *Snapshot Detail*\n\n"
        f"🏷 Name: `{img.get('description') or img.get('name') or image_id}`\n"
        f"🆔 ID: `{img['id']}`\n"
        f"🖥 Server: `{source.get('name', 'N/A')}`\n"
        f"💾 Size: `{size:.2f} GB`\n"
        f"💰 Cost: `€{cost:.2f}/month`\n"
        f"{s_emoji} Status: `{status.upper()}`\n"
        f"🔒 Protection: `{'ON' if protected else 'OFF'}`\n"
        f"📅 Created: `{created}`\n"
    )
    keyboard = [
        [InlineKeyboardButton("🗑 Delete Snapshot", callback_data=f"snapdel_{image_id}")],
        [InlineKeyboardButton("⬅️ Back to Snapshots", callback_data="snapshots")],
    ]
    await _edit(query, text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")


async def delete_snapshot_confirm(query, context, image_id):
    img = await hetzner_api.get_image(image_id)
    label = (img.get("description") or img.get("name") or str(image_id)) if img else str(image_id)
    protected = bool(img and img.get("protection", {}).get("delete"))
    note = "\n🔒 This snapshot is *delete-protected* — protection will be removed first.\n" if protected else ""
    keyboard = [
        [
            InlineKeyboardButton("✅ Yes, delete it", callback_data=f"snapdel_confirm_{image_id}"),
            InlineKeyboardButton("❌ Cancel", callback_data=f"snap_{image_id}"),
        ]
    ]
    await _edit(query, 
        f"🗑 *Delete Snapshot*\n\n"
        f"Snapshot: `{label}`\n{note}\n"
        f"⚠️ This cannot be undone. Are you sure?",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown",
    )


async def delete_snapshot(query, context, image_id):
    await _edit(query, "🗑 Deleting snapshot...")
    img = await hetzner_api.get_image(image_id)
    if img and img.get("protection", {}).get("delete"):
        # protected snapshots cannot be deleted; lift the protection first
        unlocked = await hetzner_api.change_image_protection(image_id, False)
        if unlocked is None:
            await _edit(query, 
                "❌ Could not remove the delete protection. Check the logs.",
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("⬅️ Back to Snapshots", callback_data="snapshots")]]
                ),
            )
            return
    result = await hetzner_api.delete_image(image_id)
    keyboard = [[InlineKeyboardButton("⬅️ Back to Snapshots", callback_data="snapshots")]]
    if result is not None:
        await _edit(query, 
            "✅ Snapshot deleted successfully.",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
    else:
        await _edit(query, 
            "❌ Failed to delete snapshot. Check the logs and try again.",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )


async def show_server_snapshots(query, context, server_id):
    images, server, pricing = await asyncio.gather(
        hetzner_api.list_images(),
        hetzner_api.get_server(server_id),
        hetzner_api.get_pricing(),
    )
    if not server:
        await _edit(query, "⚠️ Server not found or API error.")
        return
    per_gb = _image_price_per_gb(pricing)
    own = [i for i in images if (i.get("created_from") or {}).get("id") == server_id]

    text = f"📸 *Snapshots — `{server.get('name')}`*\n\n"
    keyboard = [[InlineKeyboardButton("➕ Take Snapshot", callback_data=f"snapcreate_{server_id}")]]

    if not own:
        text += "This server has no snapshots yet.\n"
    else:
        total_size = sum(i.get("image_size") or 0 for i in own)
        text += (
            f"Total: {len(own)} | {total_size:.1f} GB | €{total_size * per_gb:.2f}/month\n\n"
            f"Tap a snapshot to manage it:"
        )
        for img in own:
            s_emoji = "✅" if img.get("status") == "available" else "⏳"
            lock = "🔒 " if img.get("protection", {}).get("delete") else ""
            size = img.get("image_size") or 0
            label = img.get("description") or img.get("name") or str(img["id"])
            keyboard.append([InlineKeyboardButton(
                f"{s_emoji} {lock}{label} | {size:.1f} GB",
                callback_data=f"snap_{img['id']}",
            )])

    text += f"\n\n🕓 Updated: `{datetime.now().strftime('%H:%M:%S')}`"
    keyboard.append([InlineKeyboardButton("🔄 Refresh", callback_data=f"srvsnap_{server_id}")])
    keyboard.append([InlineKeyboardButton("⬅️ Back to Server", callback_data=f"server_{server_id}")])
    await _edit(query, text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")


async def snapshot_pick_server(query, context):
    servers = await hetzner_api.list_servers()
    if not servers:
        await _edit(query, "⚠️ No servers found or API error occurred.")
        return
    keyboard = []
    for s in servers:
        loc_name, flag = get_location_info(get_location(s))
        keyboard.append([InlineKeyboardButton(
            f"🖥 {s.get('name', 'Unnamed')} | {flag} {loc_name}",
            callback_data=f"snapcreate_{s['id']}",
        )])
    keyboard.append([InlineKeyboardButton("⬅️ Back to Snapshots", callback_data="snapshots")])
    await _edit(query, 
        "📸 *Take Snapshot*\n\nChoose a server:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown",
    )


async def create_snapshot(query, context, server_id):
    server = await hetzner_api.get_server(server_id)
    if not server:
        await _edit(query, "⚠️ Server not found or API error.")
        return
    name = server.get("name", "Server")
    description = f"{name} {datetime.now().strftime('%Y-%m-%d %H:%M')}"

    await _edit(query, f"📸 Creating snapshot of `{name}`...", parse_mode="Markdown")
    result = await hetzner_api.create_snapshot(server_id, description)

    keyboard = [[InlineKeyboardButton("📸 View Server Snapshots", callback_data=f"srvsnap_{server_id}")],
                [InlineKeyboardButton("🖥 Back to Server", callback_data=f"server_{server_id}")],
                [InlineKeyboardButton("⬅️ Back to Menu", callback_data="start_menu")]]
    if result:
        await _edit(query, 
            f"⏳ *Snapshot creation started*\n\n"
            f"🖥 Server: `{name}`\n"
            f"🏷 Name: `{description}`\n\n"
            f"The server keeps running. It can take several minutes — "
            f"the snapshot shows ⏳ in the list until it becomes ✅ available.",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown",
        )
    else:
        await _edit(query, 
            "❌ Failed to start snapshot creation. Check the logs and try again.",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )


async def rebuild_pick_image(query, context, server_id):
    server, images = await asyncio.gather(
        hetzner_api.get_server(server_id),
        hetzner_api.list_images("system"),
    )
    if not server:
        await _edit(query, "⚠️ Server not found or API error.")
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
    await _edit(query, 
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
    await _edit(query, 
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
    await _edit(query, "🔧 Rebuilding server... this takes a minute or two.")
    result = await hetzner_api.rebuild_server(server_id, image)
    keyboard = [[InlineKeyboardButton("🖥 Back to Server", callback_data=f"server_{server_id}")]]
    if result:
        pw = result.get("root_password")
        text = f"✅ *Rebuild started* with `{image}`.\n\n"
        if pw:
            text += f"🔑 New root password:\n`{pw}`\n\n⚠️ Save it now — it won't be shown again."
        else:
            text += "Your SSH keys were installed, no new password was generated."
        await _edit(query, text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    else:
        await _edit(query, 
            "❌ Rebuild failed. Check the logs and try again.",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )


_FAMILY_LABEL = {
    "cx": "CX — Intel shared",
    "cpx": "CPX — AMD shared",
    "cax": "CAX — ARM64 Ampere",
    "ccx": "CCX — Dedicated vCPU",
}


_type_family = type_family
_type_price = type_price


async def _server_datacenter(server):
    """The datacenter a server sits in.

    Server objects no longer carry a `datacenter` field, so the datacenter is
    matched by location instead (falling back to the old field when present).
    """
    dc_name = (server.get("datacenter") or {}).get("name", "")
    if dc_name:
        return await hetzner_api.get_datacenter(dc_name)
    loc = location_name(server)
    if not loc:
        return None
    dcs = await hetzner_api.list_datacenters()
    return next((dc for dc in dcs if dc.get("location", {}).get("name") == loc), None)


async def _resize_candidates(server):
    """Every plan this server can actually be moved to.

    What the datacenter lists as `available_for_migration` is not a filter
    here: the API accepts plans that list leaves out, which is the same route
    the traffic reset takes. Those are offered and marked, rather than
    hidden. Only the two limits Hetzner really enforces are applied —
    architecture cannot change (the disk image would not boot) and a
    deprecated plan cannot be moved to.
    """
    types, dc = await asyncio.gather(
        hetzner_api.get_server_types(),
        _server_datacenter(server),
    )
    st = (dc or {}).get("server_types", {})
    listed = set(st.get("available_for_migration") or st.get("available") or [])
    cur = server.get("server_type", {})
    candidates = [
        t for t in types
        if t.get("architecture") == cur.get("architecture")
        and not t.get("deprecation")
        and t.get("name") != cur.get("name")
    ]
    for t in candidates:
        t["_listed"] = t.get("id") in listed
    return candidates


def _resize_flags(t, cur):
    """(marker, warning) for a plan the datacenter would not normally offer."""
    marks = ""
    if not t.get("_listed"):
        marks += " 🔓"
    if (t.get("disk", 0) or 0) < (cur.get("disk", 0) or 0):
        marks += " 💾"
    return marks


async def resize_pick_family(query, context, server_id):
    server = await hetzner_api.get_server(server_id)
    if not server:
        await _edit(query, "⚠️ Server not found or API error.")
        return
    candidates = await _resize_candidates(server)
    if not candidates:
        await _edit(query,
            "⚠️ No other plans exist for this server's architecture.",
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
    await _edit(query, 
        f"⚖️ *Change Plan*\n\n"
        f"Server: `{server.get('name')}`\n"
        f"Current: `{cur.get('name')}` ({cur.get('cores')}C / {cur.get('memory'):.0f}GB / {cur.get('disk')}GB)\n\n"
        f"Choose a CPU family — every plan is offered, including the ones this "
        f"datacenter does not normally list:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown",
    )


async def resize_pick_type(query, context, server_id, family):
    server = await hetzner_api.get_server(server_id)
    if not server:
        await _edit(query, "⚠️ Server not found or API error.")
        return
    loc = location_name(server)
    candidates = [t for t in await _resize_candidates(server) if _type_family(t["name"]) == family]
    candidates.sort(key=lambda t: (t.get("cores", 0), t.get("memory", 0)))
    cur = server.get("server_type", {})
    keyboard = []
    any_unlisted = any_small_disk = False
    for t in candidates:
        arrow = "🔼" if t.get("memory", 0) > cur.get("memory", 0) else "🔽"
        marks = _resize_flags(t, cur)
        any_unlisted = any_unlisted or "🔓" in marks
        any_small_disk = any_small_disk or "💾" in marks
        keyboard.append([InlineKeyboardButton(
            f"{arrow} {t['name']} | {t.get('cores')}C / {t.get('memory'):.0f}GB / {t.get('disk')}GB | €{_type_price(t, loc):.2f}{marks}",
            callback_data=f"resizet_{server_id}_{t['name']}",
        )])
    keyboard.append([InlineKeyboardButton("⬅️ Back", callback_data=f"resize_{server_id}")])
    legend = ""
    if any_unlisted:
        legend += "\n🔓 not listed for this datacenter — the API still accepts it"
    if any_small_disk:
        legend += "\n💾 smaller disk than the server has now — Hetzner will refuse this one"
    await _edit(query,
        f"⚖️ *Change Plan* — {_FAMILY_LABEL.get(family, family.upper())}\n\n"
        f"Current: `{cur.get('name')}` ({cur.get('cores')}C / {cur.get('memory'):.0f}GB / "
        f"{cur.get('disk')}GB)\n{legend}\n\nChoose the new plan:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown",
    )


async def resize_confirm(query, context, server_id, new_type_name):
    server = await hetzner_api.get_server(server_id)
    if not server:
        await _edit(query, "⚠️ Server not found or API error.")
        return
    # from the candidate list, so the datacenter-listing flag comes with it
    new_type = next(
        (t for t in await _resize_candidates(server) if t.get("name") == new_type_name), None
    )
    if not new_type:
        await _edit(query, "⚠️ Plan not found.")
        return
    cur = server.get("server_type", {})
    loc = location_name(server)
    text = (
        f"⚖️ *Confirm Plan Change*\n\n"
        f"Server: `{server.get('name')}`\n\n"
        f"Current: `{cur.get('name')}` — {cur.get('cores')}C / {cur.get('memory'):.0f}GB / "
        f"{cur.get('disk')}GB — €{_type_price(cur, loc):.2f}\n"
        f"New: `{new_type['name']}` — {new_type.get('cores')}C / {new_type.get('memory'):.0f}GB / "
        f"{new_type.get('disk')}GB — €{_type_price(new_type, loc):.2f}\n\n"
        f"⚠️ The server will be powered off during the change.\n"
    )
    if not new_type.get("_listed"):
        text += (
            "\n🔓 This datacenter does not list this plan for migration. The API "
            "takes it anyway — the same route the traffic reset uses.\n"
        )
    if (new_type.get("disk", 0) or 0) < (cur.get("disk", 0) or 0):
        text += (
            "\n💾 This plan's disk is smaller than the server's. Hetzner refuses "
            "to shrink a disk, so this change will most likely fail.\n"
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
    await _edit(query, text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")


async def resize_go(query, context, server_id, new_type_name, upgrade_disk):
    steps = []

    async def log(line):
        steps.append(line)
        try:
            await _edit(query, "⚖️ *Changing Plan*\n\n" + "\n".join(steps), parse_mode="Markdown")
        except Exception:
            pass

    server = await hetzner_api.get_server(server_id)
    if not server:
        await _edit(query, "⚠️ Server not found or API error.")
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
    await _edit(query, 
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
        await _edit(query, "⚠️ Server not found or API error.")
        return
    loc = location_name(server)
    per_gb = _net(pricing.get("volume", {}).get("price_per_gb_month", {}) or pricing.get("volume", {}))
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
    await _edit(query, text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")


async def volume_pick_size(query, context, server_id):
    keyboard = [
        [InlineKeyboardButton(f"{size} GB", callback_data=f"volnewc_{server_id}_{size}")
         for size in (10, 20, 50, 100)],
        [InlineKeyboardButton("⬅️ Back", callback_data=f"volmenu_{server_id}")],
    ]
    await _edit(query, 
        "💽 *New Volume*\n\nChoose the size (ext4, auto-mounted):",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown",
    )


async def volume_create(query, context, server_id, size):
    await _edit(query, f"💽 Creating {size} GB volume...")
    name = f"vol-{datetime.now().strftime('%y%m%d%H%M%S')}"
    result = await hetzner_api.create_volume(name, size, server_id)
    keyboard = [[InlineKeyboardButton("💽 Back to Volumes", callback_data=f"volmenu_{server_id}")]]
    if result:
        device = result.get("volume", {}).get("linux_device", "?")
        await _edit(query, 
            f"✅ *Volume created & attached*\n\n"
            f"🏷 Name: `{name}`\n💾 Size: `{size} GB`\n📁 Device: `{device}`\n\n"
            f"It is formatted as ext4 and auto-mounted.",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown",
        )
    else:
        await _edit(query, 
            "❌ Volume creation failed. Check the logs and try again.",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )


async def volume_attach(query, context, volume_id, server_id):
    await _edit(query, "🔗 Attaching volume...")
    await hetzner_api.attach_volume(volume_id, server_id)
    await asyncio.sleep(3)
    await show_volumes(query, context, server_id)


async def volume_detach(query, context, volume_id, server_id):
    await _edit(query, "🔌 Detaching volume...")
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
    await _edit(query, 
        "🗑 *Delete Volume*\n\n"
        "⚠️ All data on the volume will be lost. If it is attached, it will "
        "be detached first.\n\nAre you sure?",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown",
    )


async def volume_delete(query, context, volume_id, server_id):
    await _edit(query, "🗑 Deleting volume...")
    volumes = await hetzner_api.list_volumes()
    vol = next((v for v in volumes if v.get("id") == volume_id), None)
    if vol and vol.get("server"):
        await hetzner_api.detach_volume(volume_id)
        await asyncio.sleep(5)
    result = await hetzner_api.delete_volume(volume_id)
    keyboard = [[InlineKeyboardButton("💽 Back to Volumes", callback_data=f"volmenu_{server_id}")]]
    if result is not None:
        await _edit(query, "✅ Volume deleted.", reply_markup=InlineKeyboardMarkup(keyboard))
    else:
        await _edit(query, 
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
        await _edit(query, "⚠️ Server not found or API error.")
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
    await _edit(query, text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")


async def server_fip_assign(query, context, fip_id, server_id):
    await _edit(query, "🔗 Attaching floating IP...")
    await hetzner_api.assign_floating_ip(fip_id, server_id)
    await asyncio.sleep(2)
    await show_server_fips(query, context, server_id)


async def server_fip_unassign(query, context, fip_id, server_id):
    await _edit(query, "🔌 Detaching floating IP...")
    await hetzner_api.unassign_floating_ip(fip_id)
    await asyncio.sleep(2)
    await show_server_fips(query, context, server_id)


async def backup_toggle_confirm(query, context, server_id):
    server = await hetzner_api.get_server(server_id)
    if not server:
        await _edit(query, "⚠️ Server not found or API error.")
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
    await _edit(query, text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")


async def backup_toggle_go(query, context, server_id, mode):
    await _edit(query, "💾 Updating backup settings...")
    if mode == "on":
        result = await hetzner_api.enable_backup(server_id)
    else:
        result = await hetzner_api.disable_backup(server_id)
    if result:
        await asyncio.sleep(2)
        await show_server_detail(query, context, server_id, refresh=True)
    else:
        await _edit(query, 
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
    return location_name(ip)


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
    await _edit(query, text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")


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
    await _edit(query, 
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
    await _edit(query, 
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
    await _edit(query, 
        f"{emoji} *Create {label}s* ({ip_type} @ {place})\n\nHow many?",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown",
    )


async def ip_create(query, context, kind, ip_type, place, count):
    emoji, label = _IP_LABEL[kind]
    await _edit(query, f"{emoji} Creating {count} {label.lower()}(s)...")
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
    await _edit(query, text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")


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
    await _edit(query, 
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
    await _edit(query, f"🗑 Deleting {len(sel)} {label.lower()}(s)...")
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
    await _edit(query, text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")


async def show_start_menu(query):
    keyboard = _main_menu_keyboard()
    await _edit(query, 
        "🚀 *Hetzner Server Manager Bot*\n\n"
        "Manage your Hetzner Cloud servers with ease.\n"
        "Monitor traffic, reset limits, and control server states.\n\n"
        "Click below to access your server panel.",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown",
    )
