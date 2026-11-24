"""Shared helpers for replying to a callback query and for Markdown text."""
from telegram.helpers import escape_markdown


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
