"""Customer order messages: queued with the order change, sent after it, resent for 24 hours.

Each order and payment service queues its message (enfileirar) inside its own transaction, so the
change and its message commit together or not at all. The caller then sends it (enviar_da_encomenda);
anything still queued is resent by the cleanup run. A row is locked while it is being sent, so the
immediate attempt and a cleanup run never both send it.
"""
import logging
from datetime import timedelta

import httpx
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from core.models import Notificacao, RegistoAuditoria
from telegram_bot.constants import MENSAGENS_NOTIFICACAO
from telegram_bot.traducoes import tr

logger = logging.getLogger(__name__)

JANELA_REENVIO = timedelta(hours=24)  # owner decision (2026-11-25): retry for 24 h, then give up
RETENCAO = timedelta(days=30)  # owner decision (2026-11-27): sent or given-up messages are deleted after 30 days

ENVIADA, PERMANENTE, TEMPORARIA = 'enviada', 'permanente', 'temporaria'


def enfileirar(encomenda, chave):
    """Queue the customer message chave (a MENSAGENS_NOTIFICACAO key); call it inside the change's transaction."""
    texto = tr(encomenda.utilizador.idioma, MENSAGENS_NOTIFICACAO[chave]).format(id=encomenda.id, total=float(encomenda.total))
    Notificacao.objects.create(encomenda=encomenda, chat_id=encomenda.utilizador.telegram_id, texto=texto)


def enviar_da_encomenda(encomenda_id) -> bool:
    """Send this order's queued messages now, after the change committed. True if all of them were delivered."""
    pks = list(Notificacao.objects.filter(encomenda_id=encomenda_id, estado='pendente')
               .order_by('id').values_list('pk', flat=True))
    for pk in pks:
        _tentar(pk)
    return not Notificacao.objects.filter(pk__in=pks).exclude(estado='enviada').exists()


def reenviar_pendentes() -> tuple:
    """Retry every queued message once (oldest first): (delivered, given_up)."""
    enviadas = falhadas = 0
    for pk in Notificacao.objects.filter(estado='pendente').order_by('id').values_list('pk', flat=True):
        if _tentar(pk):
            enviadas += 1
        elif Notificacao.objects.filter(pk=pk, estado='falhada').exists():
            falhadas += 1
    return enviadas, falhadas


def apagar_antigas() -> int:
    """Delete sent or given-up messages older than 30 days (they hold the customer's chat id); returns how many."""
    apagadas, _ = Notificacao.objects.filter(
        estado__in=('enviada', 'falhada'), criada__lt=timezone.now() - RETENCAO).delete()
    return apagadas


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
