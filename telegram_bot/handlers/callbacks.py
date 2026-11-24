"""Single entry point for inline-button callbacks.

Validates callback_data against the allowlist, finds the customer or admin
handler for it, and blocks admin routes for non-admin users.
"""
import logging

from telegram import Update
from telegram.ext import ContextTypes

from security.decorators import rate_limit, log_acao
from security.sanitization import validar_callback, EntradaInvalida, pseudonimo
from telegram_bot.guards import is_admin, verificar_bloqueio, verificar_sessao
from telegram_bot.handlers import admin, customer

logger = logging.getLogger(__name__)

# (exact routes, prefix routes, admin only) — checked in this order.
_ROUTE_TABLES = (
    (customer.EXACT_ROUTES, customer.PREFIX_ROUTES, False),
    (admin.EXACT_ROUTES, admin.PREFIX_ROUTES, True),
)


def encontrar_rota(data: str):
    """Return (handler, admin_only) for callback data, or (None, False)."""
    for exact, prefixes, admin_only in _ROUTE_TABLES:
        if data in exact:
            return exact[data], admin_only
        for prefix, handler in prefixes:
            if data.startswith(prefix):
                return handler, admin_only
    return None, False


class _RespostaUnica:
    """CallbackQuery wrapper that sends answer() at most once.

    Telegram accepts one answer per callback query, so an alert sent after an
    earlier plain answer is rejected. Every other attribute goes to the query.
    """

    def __init__(self, query):
        self._query = query
        self.respondida = False

    def __getattr__(self, nome):
        return getattr(self._query, nome)

    async def answer(self, *args, **kwargs):
        if self.respondida:
            return
        self.respondida = True
        await self._query.answer(*args, **kwargs)


@rate_limit
@verificar_bloqueio
@verificar_sessao
@log_acao("CALLBACK")
async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = _RespostaUnica(update.callback_query)
    user_id = query.from_user.id
    try:
        try:
            data = validar_callback(query.data)
        except EntradaInvalida as exc:
            logger.warning("callback_data inválido de utilizador=%s: %s", pseudonimo(user_id), exc)
            await query.answer("❌ Pedido inválido.", show_alert=True)
            return

        handler, admin_only = encontrar_rota(data)
        if handler is None:
            return
        if admin_only and not is_admin(user_id):
            await query.answer("⛔ Acesso negado.", show_alert=True)
            return
        await handler(query, context, data, user_id)
    finally:
        # Fallback for fast routes. Handlers with slow work (Stripe, photo upload,
        # customer notification) answer before it; later answers are no-ops.
        await query.answer()
