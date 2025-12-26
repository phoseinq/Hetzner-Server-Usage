import logging
from telegram.ext import Application, CommandHandler, CallbackQueryHandler
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from config import Config
from handlers import start_handler, button_handler
from monitor import traffic_monitor

def setup_logging():
    if Config.DEBUG_MODE:
        logging.basicConfig(
            format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            level=logging.INFO
        )
    else:
        logging.basicConfig(level=logging.WARNING)

def main():
    setup_logging()
    
    app = Application.builder().token(Config.TELEGRAM_TOKEN).build()
    
    app.add_handler(CommandHandler("start", start_handler))
    app.add_handler(CallbackQueryHandler(button_handler))
    
    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        traffic_monitor,
        'cron',
        hour=12,
        minute=0,
        args=[app.bot]
    )
    scheduler.start()
    
    logging.info("🚀 Bot started successfully")
    app.run_polling(allowed_updates=['message', 'callback_query'])

if __name__ == '__main__':
    main()
