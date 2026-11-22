"""Entry point for the Telegram bot: `python bot.py`.

Loads .env and sets up Django before importing telegram_bot, whose modules
import Django models at import time.
"""
import os
import logging
from pathlib import Path

import django
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / '.env')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

logging.basicConfig(
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    level=logging.INFO
)

from telegram_bot.app import main  # noqa: E402  (needs django.setup() first)


if __name__ == "__main__":
    main()
