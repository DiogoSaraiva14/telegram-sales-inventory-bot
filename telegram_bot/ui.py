"""Shared helper for replying to a callback query."""


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
