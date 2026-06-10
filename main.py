import logging
import sys
import urllib.error
import urllib.request
import warnings
from telegram.warnings import PTBUserWarning
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, filters, ConversationHandler,
)
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from config import Config
from handlers import start_handler, button_handler, _start_console
from monitor import traffic_monitor
from shell_handler import (
    recv_port, recv_user, recv_auth_type,
    recv_password, recv_key, recv_command,
    console_cancel, console_disconnect,
    console_back_panel, console_back_port, console_back_user,
    WAIT_PORT, WAIT_USER, WAIT_AUTH_TYPE,
    WAIT_PASSWORD, WAIT_KEY, WAIT_COMMAND,
)


def setup_logging():
    level = logging.INFO if Config.DEBUG_MODE else logging.WARNING
    logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=level)


def check_hetzner_token():
    req = urllib.request.Request(
        f"{Config.HETZNER_API_BASE}/servers",
        headers={"Authorization": f"Bearer {Config.HETZNER_API_TOKEN}"},
    )
    try:
        urllib.request.urlopen(req, timeout=15)
    except urllib.error.HTTPError as e:
        if e.code == 401:
            sys.exit(
                "❌ HETZNER_API_TOKEN is invalid (Hetzner API returned 401).\n"
                "Create a token in Hetzner Cloud Console → Security → API Tokens "
                "and put it in .env, then restart the bot."
            )
        logging.warning(f"Hetzner API returned HTTP {e.code} during token check")
    except Exception as e:
        logging.warning(f"Could not verify Hetzner token (network issue?): {e}")


def main():
    setup_logging()
    check_hetzner_token()
    warnings.filterwarnings("ignore", category=PTBUserWarning)
    app = Application.builder().token(Config.TELEGRAM_TOKEN).build()

    console_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(_start_console, pattern=r"^console_\d+$"),
        ],
        states={
            WAIT_PORT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, recv_port),
                CallbackQueryHandler(console_back_panel, pattern="^console_back_panel$"),
            ],
            WAIT_USER: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, recv_user),
                CallbackQueryHandler(console_back_port, pattern="^console_back_port$"),
            ],
            WAIT_AUTH_TYPE: [
                CallbackQueryHandler(recv_auth_type, pattern="^auth_"),
                CallbackQueryHandler(console_back_user, pattern="^console_back_user$"),
            ],
            WAIT_PASSWORD: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, recv_password),
                CallbackQueryHandler(console_back_user, pattern="^console_back_user$"),
            ],
            WAIT_KEY: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, recv_key),
                CallbackQueryHandler(console_back_user, pattern="^console_back_user$"),
            ],
            WAIT_COMMAND: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, recv_command),
            ],
        },
        fallbacks=[
            CallbackQueryHandler(console_cancel,      pattern="^console_cancel$"),
            CallbackQueryHandler(console_disconnect,  pattern="^console_disconnect$"),
            CallbackQueryHandler(console_back_panel,  pattern="^console_back_panel$"),
            CallbackQueryHandler(console_back_port,   pattern="^console_back_port$"),
            CallbackQueryHandler(console_back_user,   pattern="^console_back_user$"),
        ],
        per_message=False,
        per_chat=True,
        per_user=True,
    )

    app.add_handler(CommandHandler("start", start_handler))
    app.add_handler(console_conv)
    app.add_handler(CallbackQueryHandler(button_handler))

    scheduler = AsyncIOScheduler()
    scheduler.add_job(traffic_monitor, "interval", hours=1, args=[app.bot])
    scheduler.start()

    logging.info("🚀 Bot started successfully")
    app.run_polling(allowed_updates=["message", "callback_query"])


if __name__ == "__main__":
    main()
