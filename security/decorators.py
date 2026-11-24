import os
import time
import asyncio
import logging
from functools import wraps
from collections import defaultdict
from telegram import Update
from telegram.ext import ContextTypes

from security.sanitization import pseudonimo

logger = logging.getLogger(__name__)

# ── Rate limiting ──────────────────────────────────────────────────
_rate_data: dict[int, list[float]] = defaultdict(list)
_rate_lock = asyncio.Lock()

RATE_LIMIT = 5   # máximo de ações
RATE_WINDOW = 10  # por janela de 10 segundos

def rate_limit(func):
    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        agora = time.monotonic()

        async with _rate_lock:
            _rate_data[user_id] = [
                t for t in _rate_data[user_id] if agora - t < RATE_WINDOW
            ]
            if len(_rate_data[user_id]) >= RATE_LIMIT:
                logger.warning("Rate limit atingido: utilizador=%s", pseudonimo(user_id))
                if update.message:
                    await update.message.reply_text(
                        "⚠️ Demasiados pedidos. Aguarda alguns segundos."
                    )
                elif update.callback_query:
                    await update.callback_query.answer(
                        "⚠️ Demasiados pedidos. Aguarda.", show_alert=True
                    )
                return
            _rate_data[user_id].append(agora)

        return await func(update, context)
    return wrapper

# ── Admin only ─────────────────────────────────────────────────────
ADMIN_IDS = set(
    int(i) for i in os.getenv("ADMIN_TELEGRAM_IDS", "").split(",") if i.strip()
)

def admin_only(func):
    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        if user_id not in ADMIN_IDS:
            logger.warning("Acesso não autorizado: utilizador=%s", pseudonimo(user_id))
            if update.message:
                await update.message.reply_text("🚫 Acesso restrito.")
            elif update.callback_query:
                await update.callback_query.answer("🚫 Acesso restrito.", show_alert=True)
            return
        return await func(update, context)
    return wrapper

# ── Logging de ações ───────────────────────────────────────────────
def log_acao(acao: str):
    def decorator(func):
        @wraps(func)
        async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
            user = update.effective_user
            logger.info("[%s] utilizador=%s", acao, pseudonimo(user.id))  # no username (SEC-08)
            return await func(update, context)
        return wrapper
    return decorator
