"""Build the Telegram application, register handlers and start polling."""
import os
import asyncio
import logging
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler

from telegram_bot.handlers.callbacks import callback_handler
from telegram_bot.handlers.commands import start, admin_command

logger = logging.getLogger(__name__)

# Settings the bot itself needs; Django's own required ones are checked in config/settings.py.
VARIAVEIS_OBRIGATORIAS = ("TELEGRAM_TOKEN", "STRIPE_SECRET_KEY", "STRIPE_SUCCESS_URL", "STRIPE_CANCEL_URL")


LOG_DIAS = 14  # owner decision, SEC-08 (2026-11-24)


def configurar_logs(pasta: Path) -> None:
    """Log to the console and to <pasta>/bot.log, rotated at midnight and kept LOG_DIAS days (SEC-08)."""
    pasta.mkdir(exist_ok=True)
    ficheiro = TimedRotatingFileHandler(pasta / "bot.log", when="midnight", backupCount=LOG_DIAS, encoding="utf-8")
    logging.basicConfig(
        format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
        level=logging.INFO,
        handlers=[logging.StreamHandler(), ficheiro],
        force=True,
    )
    # httpx logs every request URL at INFO, and Telegram URLs contain the bot token.
    logging.getLogger("httpx").setLevel(logging.WARNING)


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
