"""Build the Telegram application, register handlers and start polling."""
import os
import asyncio
import logging

from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler

from telegram_bot.handlers.callbacks import callback_handler
from telegram_bot.handlers.commands import start, admin_command

logger = logging.getLogger(__name__)

# Settings the bot itself needs; Django's own required ones are checked in config/settings.py.
VARIAVEIS_OBRIGATORIAS = ("TELEGRAM_TOKEN", "STRIPE_SECRET_KEY", "STRIPE_SUCCESS_URL", "STRIPE_CANCEL_URL")


def variaveis_em_falta(ambiente=os.environ):
    return [nome for nome in VARIAVEIS_OBRIGATORIAS if not ambiente.get(nome)]


def main():
    em_falta = variaveis_em_falta()
    if em_falta:
        raise SystemExit(f"Faltam variáveis de ambiente: {', '.join(em_falta)}. Vê .env.example.")
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
