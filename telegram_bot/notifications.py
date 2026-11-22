"""Order-status notifications sent to customers through the running bot."""
import logging

from telegram.error import TelegramError
from telegram.ext import ContextTypes

from telegram_bot.constants import MENSAGENS_NOTIFICACAO

logger = logging.getLogger(__name__)


async def notificar_cliente(context: ContextTypes.DEFAULT_TYPE, encomenda, novo_estado: str) -> bool:
    template = MENSAGENS_NOTIFICACAO.get(novo_estado)
    if not template:
        return False
    mensagem = template.format(id=encomenda.id, total=float(encomenda.total))
    try:
        await context.bot.send_message(
            chat_id=int(encomenda.utilizador.telegram_id),
            text=mensagem,
            parse_mode="Markdown"
        )
        logger.info(
            "Notificação enviada: encomenda=#%s estado=%s telegram_id=%s",
            encomenda.id, novo_estado, encomenda.utilizador.telegram_id
        )
        return True
    except TelegramError as exc:
        logger.error(
            "Falha ao notificar cliente: encomenda=#%s telegram_id=%s erro=%s",
            encomenda.id, encomenda.utilizador.telegram_id, exc
        )
        return False
