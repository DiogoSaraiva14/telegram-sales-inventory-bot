"""Admin identity, session timeout and blocked-account checks.

Session state is process-local (see SECURITY.md, SEC-09).
"""
import os
import time
import logging
from functools import wraps

from telegram import Update
from telegram.ext import ContextTypes

from core.services import utilizador_bloqueado

logger = logging.getLogger(__name__)

# ── Admin IDs ──────────────────────────────────────────────────────────────

ADMIN_IDS = set(
    int(x.strip())
    for x in os.getenv("ADMIN_TELEGRAM_IDS", "").split(",")
    if x.strip().isdigit()
)

def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS

# ── Session timeout ────────────────────────────────────────────────────────

SESSION_TIMEOUT = 30 * 60
_ultima_atividade: dict[int, float] = {}

def _registar_atividade(user_id: int) -> None:
    _ultima_atividade[user_id] = time.monotonic()

def _sessao_expirada(user_id: int) -> bool:
    ultimo = _ultima_atividade.get(user_id)
    if ultimo is None:
        return True
    return (time.monotonic() - ultimo) > SESSION_TIMEOUT

def verificar_sessao(func):
    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        if update.message and update.message.text and update.message.text.startswith("/start"):
            _registar_atividade(user_id)
            return await func(update, context)
        if update.callback_query:
            if _sessao_expirada(user_id):
                logger.info("Sessão expirada: user_id=%s", user_id)
                await update.callback_query.answer(
                    "⏱️ A tua sessão expirou. Usa /start para recomeçar.",
                    show_alert=True
                )
                try:
                    await update.callback_query.edit_message_text(
                        "⏱️ *Sessão expirada.*\n\nUsa /start para voltar ao menu principal.",
                        parse_mode="Markdown"
                    )
                except Exception:
                    pass
                return
            _registar_atividade(user_id)
            return await func(update, context)
    return wrapper

# ── Conta bloqueada ────────────────────────────────────────────────────────

def verificar_bloqueio(func):
    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        if await utilizador_bloqueado(user_id):
            logger.warning("Utilizador bloqueado tentou aceder: user_id=%s", user_id)
            if update.message:
                await update.message.reply_text(
                    "🚫 A tua conta foi suspensa.\n"
                    "Contacta o suporte se acreditas que isto é um erro."
                )
            elif update.callback_query:
                await update.callback_query.answer("🚫 A tua conta está suspensa.", show_alert=True)
                try:
                    await update.callback_query.edit_message_text(
                        "🚫 *Conta suspensa.*\n\nContacta o suporte se acreditas que isto é um erro.",
                        parse_mode="Markdown"
                    )
                except Exception:
                    pass
            return
        return await func(update, context)
    return wrapper
