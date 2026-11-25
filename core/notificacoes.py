"""Customer order messages: queued, sent at once, and resent by the cleanup run for 24 hours.

Every message is saved before it is sent, so a Telegram failure leaves it queued instead of lost.
A row is locked while it is being sent, so the immediate attempt and a cleanup run never both send it.
"""
import logging
from datetime import timedelta

import httpx
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from core.models import Notificacao, RegistoAuditoria
from telegram_bot.constants import MENSAGENS_NOTIFICACAO

logger = logging.getLogger(__name__)

JANELA_REENVIO = timedelta(hours=24)  # owner decision (2026-11-25): retry for 24 h, then give up

ENVIADA, PERMANENTE, TEMPORARIA = 'enviada', 'permanente', 'temporaria'


def notificar_estado(encomenda, chave) -> bool:
    """Customer message for an order state; chave is a MENSAGENS_NOTIFICACAO key. True if delivered now."""
    template = MENSAGENS_NOTIFICACAO.get(chave)
    if not template:
        logger.error("Sem mensagem para o estado %s: encomenda=#%s", chave, encomenda.id)
        return False
    return notificar(encomenda, template.format(id=encomenda.id, total=float(encomenda.total)))


def notificar(encomenda, texto) -> bool:
    """Queue a message for the order's customer and try to send it now. True if delivered now."""
    n = Notificacao.objects.create(encomenda=encomenda, chat_id=encomenda.utilizador.telegram_id, texto=texto)
    return _tentar(n.pk)


def reenviar_pendentes() -> tuple:
    """Retry every queued message once (oldest first): (delivered, given_up)."""
    enviadas = falhadas = 0
    for pk in Notificacao.objects.filter(estado='pendente').order_by('id').values_list('pk', flat=True):
        if _tentar(pk):
            enviadas += 1
        elif Notificacao.objects.filter(pk=pk, estado='falhada').exists():
            falhadas += 1
    return enviadas, falhadas


def _tentar(pk) -> bool:
    with transaction.atomic():
        n = Notificacao.objects.select_for_update(skip_locked=True).filter(pk=pk, estado='pendente').first()
        if n is None:  # already sent, given up, or being sent by another process right now
            return False
        resultado, motivo = _enviar_telegram(n.chat_id, n.texto, n.encomenda_id)
        n.tentativas += 1
        if resultado == ENVIADA:
            n.estado, n.enviada_em = 'enviada', timezone.now()
        elif resultado == PERMANENTE or timezone.now() - n.criada >= JANELA_REENVIO:
            n.estado = 'falhada'
            if resultado == TEMPORARIA:
                motivo = f"não entregue em 24 h ({motivo})"
            RegistoAuditoria.objects.create(
                acao='NOTIFICACAO_FALHADA', entidade='Encomenda',
                descricao=f"#{n.encomenda_id}: {motivo}; {n.tentativas} tentativa(s)")
            logger.error("Notificação abandonada: encomenda=#%s motivo=%s", n.encomenda_id, motivo)
        n.save(update_fields=['estado', 'tentativas', 'enviada_em'])
        return resultado == ENVIADA


def _enviar_telegram(chat_id, texto, encomenda_id) -> tuple:
    """One send attempt: (ENVIADA | PERMANENTE | TEMPORARIA, reason)."""
    token = settings.TELEGRAM_TOKEN
    if not token:
        logger.warning("TELEGRAM_TOKEN não configurado — notificação fica em fila")
        return TEMPORARIA, "TELEGRAM_TOKEN não configurado"
    try:
        response = httpx.post(f"https://api.telegram.org/bot{token}/sendMessage", json={
            "chat_id": chat_id,
            "text": texto,
            "parse_mode": "Markdown",
        }, timeout=10)
    except Exception as exc:
        # Type only: an httpx error message can carry the request URL, which holds the bot token.
        logger.error("Erro ao enviar notificação Telegram: encomenda=#%s erro=%s", encomenda_id, type(exc).__name__)
        return TEMPORARIA, type(exc).__name__
    if response.status_code == 200:
        logger.info("Notificação Telegram enviada: encomenda=#%s", encomenda_id)
        return ENVIADA, ""
    logger.error("Falha na notificação Telegram: encomenda=#%s status=%s", encomenda_id, response.status_code)
    # 400 and 403 won't change on a retry (chat not found, bot blocked, bad text); 429 and 5xx may.
    if response.status_code in (400, 403):
        return PERMANENTE, f"Telegram recusou ({response.status_code})"
    return TEMPORARIA, f"Telegram respondeu {response.status_code}"
