"""Entry point for the Telegram bot: `python bot.py`.

Loads .env and sets up Django before importing telegram_bot, whose modules
import Django models at import time.
"""
import os
from pathlib import Path

import django
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / '.env')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from telegram_bot.app import configurar_logs, main  # noqa: E402  (needs django.setup() first)

configurar_logs(Path(__file__).resolve().parent / 'logs')


if __name__ == "__main__":
    main()
