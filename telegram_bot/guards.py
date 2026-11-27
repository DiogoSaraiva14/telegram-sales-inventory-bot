"""Session timeout and blocked-account checks (Telegram admins: core.services.e_admin).

Session state is process-local (see SECURITY.md, SEC-09).
"""
import time
import logging
from functools import wraps

from telegram import Update
from telegram.ext import ContextTypes

from core.services import utilizador_bloqueado
from security.sanitization import pseudonimo
from telegram_bot.traducoes import tr
from telegram_bot.ui import idioma_rapido

logger = logging.getLogger(__name__)

# ── Session timeout ────────────────────────────────────────────────────────

# In-process state, like the rate limiter (SEC-09): one polling process, reset on restart.
SESSION_TIMEOUT = 30 * 60
LIMPEZA_INTERVALO = 60  # segundos entre limpezas de sessões expiradas
_ultima_atividade: dict[int, float] = {}
_ultima_limpeza = 0.0

def _registar_atividade(user_id: int) -> None:
    global _ultima_limpeza
    agora = time.monotonic()
    if agora - _ultima_limpeza >= LIMPEZA_INTERVALO:
        # A missing entry already counts as expired, so dropping expired ones changes nothing.
        _ultima_limpeza = agora
        for uid in [u for u, t in _ultima_atividade.items() if agora - t > SESSION_TIMEOUT]:
            del _ultima_atividade[uid]
    _ultima_atividade[user_id] = agora

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
                logger.info("Sessão expirada: utilizador=%s", pseudonimo(user_id))
                i = idioma_rapido(context, update.effective_user)
                await update.callback_query.answer(
                    tr(i, "⏱️ A tua sessão expirou. Usa /start para recomeçar."),
                    show_alert=True
                )
                try:
                    await update.callback_query.edit_message_text(
                        tr(i, "⏱️ *Sessão expirada.*\n\nUsa /start para voltar ao menu principal."),
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
            logger.warning("Utilizador bloqueado tentou aceder: utilizador=%s", pseudonimo(user_id))
            i = idioma_rapido(context, update.effective_user)
            if update.message:
                await update.message.reply_text(
                    tr(i, "🚫 A tua conta foi suspensa.\nContacta o suporte se acreditas que isto é um erro.")
                )
            elif update.callback_query:
                await update.callback_query.answer(tr(i, "🚫 A tua conta está suspensa."), show_alert=True)
                try:
                    await update.callback_query.edit_message_text(
                        tr(i, "🚫 *Conta suspensa.*\n\nContacta o suporte se acreditas que isto é um erro."),
                        parse_mode="Markdown"
                    )
                except Exception:
                    pass
            return
        return await func(update, context)
    return wrapper
