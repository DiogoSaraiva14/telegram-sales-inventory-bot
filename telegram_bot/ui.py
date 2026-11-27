"""Shared helpers for replying to a callback query, for Markdown text and for the customer's language."""
from telegram.helpers import escape_markdown

from core import services
from telegram_bot.traducoes import IDIOMAS, idioma_de_codigo

_CHAVE_IDIOMA = "idioma"


def idioma_rapido(context, user) -> str:
    """The cached language, else Telegram's; no database read (for the rate limiter and guards)."""
    dados = getattr(context, "user_data", None)
    valor = dados.get(_CHAVE_IDIOMA) if isinstance(dados, dict) else None
    return valor if valor in IDIOMAS else idioma_de_codigo(getattr(user, "language_code", None))


async def idioma(context, user) -> str:
    """The customer's bot language: cached per user, else saved in the database, else Telegram's."""
    dados = getattr(context, "user_data", None)
    valor = dados.get(_CHAVE_IDIOMA) if isinstance(dados, dict) else None
    if valor not in IDIOMAS:
        valor = await services.idioma_guardado(user.id) or idioma_de_codigo(getattr(user, "language_code", None))
        guardar_idioma(context, valor)
    return valor


def guardar_idioma(context, valor) -> None:
    dados = getattr(context, "user_data", None)
    if isinstance(dados, dict):
        dados[_CHAVE_IDIOMA] = valor


def md(valor, entidade=None):
    """Dynamic text for a legacy-Markdown message (SEC-07).

    Outside an entity, Markdown characters are escaped. Inside a *bold* or _italic_
    entity, Telegram allows no escaping: the delimiter closes the entity, is escaped and
    reopens it (the Bot API's own example: *2*\\**2=4*). Entities do not nest, so other
    characters are literal there.
    """
    texto = str(valor)
    if entidade:
        return texto.replace(entidade, f"{entidade}\\{entidade}{entidade}")
    return escape_markdown(texto, version=1)


async def responder(query, context, texto, reply_markup, parse_mode="Markdown"):
    """Edit the current message, or replace it when it is a photo/document."""
    if query.message.photo or query.message.document:
        await query.message.delete()
        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text=texto,
            parse_mode=parse_mode,
            reply_markup=reply_markup
        )
    else:
        await query.edit_message_text(
            text=texto,
            parse_mode=parse_mode,
            reply_markup=reply_markup
        )
