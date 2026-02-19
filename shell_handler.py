import asyncio
import logging
import re
import time
import paramiko
from io import StringIO
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes, ConversationHandler, MessageHandler, CallbackQueryHandler, filters
from config import Config

logger = logging.getLogger(__name__)

WAIT_PORT, WAIT_USER, WAIT_AUTH_TYPE, WAIT_PASSWORD, WAIT_KEY, WAIT_COMMAND = range(6)

active_sessions: dict = {}

SESSION_TIMEOUT = 600
MAX_OUTPUT_CHARS = 3800
SENTINEL = "__CMD_DONE__"

CLEAN_EXEC_PREFIXES = ("apt ", "apt-get ", "dpkg ", "pip ", "pip3 ", "npm ", "yarn ", "systemctl ", "service ")


def _kb_port():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⬅️ Back", callback_data="console_back_panel")],
        [InlineKeyboardButton("❌ Cancel", callback_data="console_cancel")],
    ])

def _kb_user():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⬅️ Back", callback_data="console_back_port")],
        [InlineKeyboardButton("❌ Cancel", callback_data="console_cancel")],
    ])

def _kb_auth():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🔑 Password", callback_data="auth_password"),
            InlineKeyboardButton("🗝 Private Key", callback_data="auth_key"),
        ],
        [InlineKeyboardButton("⬅️ Back", callback_data="console_back_user")],
        [InlineKeyboardButton("❌ Cancel", callback_data="console_cancel")],
    ])

def _kb_pass():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⬅️ Back", callback_data="console_back_user")],
        [InlineKeyboardButton("❌ Cancel", callback_data="console_cancel")],
    ])

DISCONNECT_KB = InlineKeyboardMarkup([
    [InlineKeyboardButton("🔌 Disconnect", callback_data="console_disconnect")],
])


def _needs_clean_exec(cmd: str) -> bool:
    return any(cmd.strip().lower().startswith(p) for p in CLEAN_EXEC_PREFIXES)


def _truncate(text: str) -> str:
    if len(text) <= MAX_OUTPUT_CHARS:
        return text
    half = MAX_OUTPUT_CHARS // 2
    return text[:half] + "\n\n... [output trimmed] ...\n\n" + text[-half:]


def _clean(text: str) -> str:
    text = re.sub(r"\x1B\[[0-9;]*[mGKHFJA-Za-z]", "", text)
    text = re.sub(r"\x1B\][^\x07]*\x07", "", text)
    text = re.sub(r"\r\n?", "\n", text)
    text = re.sub(r"[^\S\n]*\r[^\S\n]*", "", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _run_clean_exec(client: paramiko.SSHClient, cmd: str, timeout: int = 120) -> str:
    _, stdout, stderr = client.exec_command(
        f"DEBIAN_FRONTEND=noninteractive TERM=dumb {cmd}",
        timeout=timeout, get_pty=False,
    )
    out = _clean(stdout.read().decode(errors="replace")).strip()
    err = "\n".join(
        ln for ln in stderr.read().decode(errors="replace").splitlines()
        if "apt does not have a stable cli" not in ln.lower()
        and "warning: apt" not in ln.lower()
    ).strip()
    err = _clean(err)
    return out + (f"\n\nstderr:\n{err}" if err else "")


def _run_command(shell: paramiko.Channel, cmd: str, timeout: int = 120) -> str:
    shell.send(f"{cmd} ; echo '{SENTINEL}'\n")
    output = ""
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(0.2)
        if shell.recv_ready():
            output += shell.recv(65535).decode(errors="replace")
            if SENTINEL in output:
                break
    lines = [
        ln for ln in output.splitlines()
        if SENTINEL not in ln
        and not (re.search(r"(\$|#)\s*$", ln) and cmd.strip() in ln)
    ]
    result = _clean("\n".join(lines))
    return re.sub(rf"^{re.escape(cmd.strip())}\s*", "", result, count=1).strip()


async def _safe_edit(bot, chat_id, msg_id, text, reply_markup=None):
    try:
        await bot.edit_message_text(
            chat_id=chat_id, message_id=msg_id,
            text=text, parse_mode="Markdown", reply_markup=reply_markup,
        )
    except Exception:
        pass


async def _delete_msg(msg):
    try:
        await msg.delete()
    except Exception:
        pass


def _header(d: dict) -> str:
    return f"💻 *SSH Console — {d['server_name']}*\n\n"


async def _session_timeout_watcher(uid: int, bot):
    while uid in active_sessions:
        await asyncio.sleep(30)
        s = active_sessions.get(uid)
        if not s:
            break
        if time.time() - s["last_active"] > SESSION_TIMEOUT:
            try:
                s["shell"].close()
                s["client"].close()
            except Exception:
                pass
            active_sessions.pop(uid, None)
            try:
                await bot.send_message(
                    chat_id=s["chat_id"],
                    text="⏱ *Session timed out* after 10 minutes of inactivity.",
                    parse_mode="Markdown",
                )
            except Exception:
                pass
            break


async def console_entry(query, context, server_id: int, server_ip: str, server_name: str):
    context.user_data["console"] = {
        "server_id": server_id,
        "server_ip": server_ip,
        "server_name": server_name,
    }
    context.user_data["console_msg_id"] = query.message.message_id
    context.user_data["console_chat_id"] = query.message.chat_id

    await query.edit_message_text(
        f"💻 *SSH Console — {server_name}*\n\n"
        f"🌐 IP: `{server_ip}`\n\n"
        f"Enter SSH port (default is 22):",
        reply_markup=_kb_port(),
        parse_mode="Markdown",
    )
    return WAIT_PORT


async def recv_port(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    port = int(text) if text.isdigit() else 22
    context.user_data["console"]["port"] = port
    await _delete_msg(update.message)
    d = context.user_data["console"]
    await _safe_edit(
        context.bot,
        context.user_data["console_chat_id"],
        context.user_data["console_msg_id"],
        _header(d) + f"✅ Port: `{port}`\n\nEnter SSH username:",
        _kb_user(),
    )
    return WAIT_USER


async def recv_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    username = update.message.text.strip()
    context.user_data["console"]["username"] = username
    await _delete_msg(update.message)
    d = context.user_data["console"]
    await _safe_edit(
        context.bot,
        context.user_data["console_chat_id"],
        context.user_data["console_msg_id"],
        _header(d) + f"✅ Port: `{d['port']}`\n✅ Username: `{username}`\n\nSelect authentication method:",
        _kb_auth(),
    )
    return WAIT_AUTH_TYPE


async def recv_auth_type(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    d = context.user_data["console"]
    if query.data == "auth_password":
        context.user_data["console"]["auth_type"] = "password"
        await query.edit_message_text(
            _header(d) + f"✅ Port: `{d['port']}`\n✅ Username: `{d['username']}`\n\n"
            f"🔒 Send your SSH password:\n_(message will be deleted immediately)_",
            reply_markup=_kb_pass(),
            parse_mode="Markdown",
        )
        return WAIT_PASSWORD
    else:
        context.user_data["console"]["auth_type"] = "key"
        await query.edit_message_text(
            _header(d) + f"✅ Port: `{d['port']}`\n✅ Username: `{d['username']}`\n\n"
            f"🗝 Paste your private key content (id\\_rsa):",
            reply_markup=_kb_pass(),
            parse_mode="Markdown",
        )
        return WAIT_KEY


async def recv_password(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["console"]["password"] = update.message.text
    await _delete_msg(update.message)
    return await _do_connect(update, context)


async def recv_key(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["console"]["key_text"] = update.message.text.strip()
    await _delete_msg(update.message)
    return await _do_connect(update, context)


async def _do_connect(update: Update, context: ContextTypes.DEFAULT_TYPE):
    d = context.user_data["console"]
    uid = update.effective_user.id
    bot = context.bot
    chat_id = context.user_data["console_chat_id"]
    msg_id = context.user_data["console_msg_id"]

    await _safe_edit(bot, chat_id, msg_id, f"🔌 Connecting to `{d['server_ip']}:{d['port']}`...")

    try:
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        kwargs = dict(hostname=d["server_ip"], port=d["port"], username=d["username"], timeout=15)
        if d["auth_type"] == "password":
            kwargs["password"] = d["password"]
        else:
            kwargs["pkey"] = paramiko.RSAKey.from_private_key(StringIO(d["key_text"]))

        await asyncio.get_event_loop().run_in_executor(None, lambda: client.connect(**kwargs))

        shell = client.invoke_shell(term="dumb", width=220, height=50)
        await asyncio.sleep(1.5)
        if shell.recv_ready():
            shell.recv(65535)
        shell.send("export TERM=dumb; export DEBIAN_FRONTEND=noninteractive\n")
        await asyncio.sleep(0.5)
        if shell.recv_ready():
            shell.recv(65535)

        active_sessions[uid] = {
            "client": client, "shell": shell,
            "server_name": d["server_name"], "server_id": d["server_id"],
            "chat_id": chat_id, "last_active": time.time(),
        }
        asyncio.create_task(_session_timeout_watcher(uid, bot))

        await _safe_edit(
            bot, chat_id, msg_id,
            f"✅ *Connected to* `{d['server_name']}`\n\n"
            f"Send any command and I'll run it.\n"
            f"Example: `ls -la` or `cd /var/log && ls`\n\n"
            f"⏱ Session closes after 10 min of inactivity.",
            DISCONNECT_KB,
        )
        return WAIT_COMMAND

    except paramiko.AuthenticationException:
        await _safe_edit(bot, chat_id, msg_id, "❌ *Authentication failed.* Wrong password or key.")
        return ConversationHandler.END
    except Exception as e:
        await _safe_edit(bot, chat_id, msg_id, f"❌ *Connection error:*\n`{e}`")
        return ConversationHandler.END


async def recv_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    cmd = update.message.text.strip()
    await _delete_msg(update.message)

    if uid not in active_sessions:
        await update.effective_chat.send_message("⚠️ No active session. Connect again from the panel.")
        return ConversationHandler.END

    session = active_sessions[uid]
    session["last_active"] = time.time()
    bot = context.bot
    chat_id = context.user_data["console_chat_id"]
    msg_id = context.user_data["console_msg_id"]

    await _safe_edit(bot, chat_id, msg_id, f"⚙️ Running: `{cmd}`\n\n_Please wait..._", DISCONNECT_KB)

    try:
        output = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: _run_clean_exec(session["client"], cmd) if _needs_clean_exec(cmd)
                    else _run_command(session["shell"], cmd)
        )
        result = f"```\n{_truncate(output)}\n```" if output else "_(no output)_"
        await _safe_edit(bot, chat_id, msg_id, f"✅ `{cmd}`\n\n{result}\n\n_Send next command:_", DISCONNECT_KB)
    except Exception as e:
        await _safe_edit(bot, chat_id, msg_id, f"❌ Error: `{e}`\n\n_Send next command:_", DISCONNECT_KB)

    return WAIT_COMMAND


async def console_back_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    server_id = context.user_data["console"]["server_id"]
    from handlers import show_server_detail
    await show_server_detail(query, context, server_id)
    return ConversationHandler.END


async def console_back_port(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    d = context.user_data["console"]
    await query.edit_message_text(
        f"💻 *SSH Console — {d['server_name']}*\n\n"
        f"🌐 IP: `{d['server_ip']}`\n\nEnter SSH port (default is 22):",
        reply_markup=_kb_port(),
        parse_mode="Markdown",
    )
    return WAIT_PORT


async def console_back_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    d = context.user_data["console"]
    await query.edit_message_text(
        _header(d) + f"✅ Port: `{d['port']}`\n\nEnter SSH username:",
        reply_markup=_kb_user(),
        parse_mode="Markdown",
    )
    return WAIT_USER


async def console_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    _close_session(query.from_user.id)
    await query.edit_message_text("🔌 *Disconnected.*", parse_mode="Markdown")
    return ConversationHandler.END


async def console_disconnect(update: Update, context: ContextTypes.DEFAULT_TYPE):
    return await console_cancel(update, context)


def _close_session(uid: int):
    s = active_sessions.pop(uid, None)
    if s:
        try:
            s["shell"].close()
            s["client"].close()
        except Exception:
            pass
