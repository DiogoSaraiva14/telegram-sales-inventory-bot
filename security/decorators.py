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
# In-process state (SEC-09): the polling bot runs as exactly one process, because
# Telegram refuses a second getUpdates consumer for the same token. A restart resets it.
_rate_data: dict[int, list[float]] = defaultdict(list)
_rate_lock = asyncio.Lock()
_ultima_limpeza = 0.0

# 20 per 10 s: about four times a person tapping fast (5 per 10 s blocked normal browsing
# in the 2026-11-25 Telegram test), still far below a scripted flood.
RATE_LIMIT = 20   # máximo de ações
RATE_WINDOW = 10  # por janela de 10 segundos
LIMPEZA_INTERVALO = 60  # segundos entre limpezas de utilizadores inativos


def _limpar_inativos(agora: float) -> None:
    """Drop users with no request inside the window, at most once per interval, so memory stays bounded."""
    global _ultima_limpeza
    if agora - _ultima_limpeza < LIMPEZA_INTERVALO:
        return
    _ultima_limpeza = agora
    for user_id in [u for u, tempos in _rate_data.items() if not tempos or agora - tempos[-1] >= RATE_WINDOW]:
        del _rate_data[user_id]

def rate_limit(func):
    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        agora = time.monotonic()

        async with _rate_lock:
            _limpar_inativos(agora)
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
                    # A toast, not a pop-up the customer has to dismiss.
                    await update.callback_query.answer("⚠️ Demasiados pedidos. Aguarda um momento.")
                return
            _rate_data[user_id].append(agora)

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
