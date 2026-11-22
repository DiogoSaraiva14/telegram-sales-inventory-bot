"""Build the Telegram application, register handlers and start polling."""
import os
import asyncio
import logging

from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler

from telegram_bot.handlers.callbacks import callback_handler
from telegram_bot.handlers.commands import start, admin_command

logger = logging.getLogger(__name__)


def main():
    if hasattr(asyncio, "WindowsSelectorEventLoopPolicy"):
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    app = ApplicationBuilder().token(os.getenv("TELEGRAM_TOKEN")).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("admin", admin_command))
    app.add_handler(CallbackQueryHandler(callback_handler))
    logger.info("🤖 Bot a correr... Pressiona Ctrl+C para parar.")
    try:
        app.run_polling(stop_signals=None)
    except KeyboardInterrupt:
        logger.info("Bot parado pelo utilizador (Ctrl+C).")
