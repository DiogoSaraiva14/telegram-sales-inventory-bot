import stripe
import logging
from django.conf import settings
from django.http import HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from .models import Pagamento
from . import services
from .notificacoes import enviar_da_encomenda

logger = logging.getLogger(__name__)
stripe.api_key = settings.STRIPE_SECRET_KEY


@csrf_exempt
@require_POST
def stripe_webhook(request):
    payload = request.body
    sig_header = request.META.get('HTTP_STRIPE_SIGNATURE', '')

    try:
        event = stripe.Webhook.construct_event(
            payload, sig_header, settings.STRIPE_WEBHOOK_SECRET
        )
    except ValueError:
        logger.error("Webhook: payload inválido")
        return HttpResponse(status=400)
    except stripe.error.SignatureVerificationError:
        logger.error("Webhook: assinatura inválida")
        return HttpResponse(status=400)

    event_type = event['type']
    session = event['data']['object']

    if event_type == 'checkout.session.completed':
        _handle_payment_success(session)
    elif event_type == 'checkout.session.expired':
        _handle_payment_expired(session)
    else:
        logger.info("Evento ignorado: %s", event_type)

    return HttpResponse(status=200)


def _handle_payment_success(session):
    session_id = session['id']

    try:
        pagamento = Pagamento.objects.select_related(
            'encomenda__utilizador'
        ).get(stripe_session_id=session_id)
    except Pagamento.DoesNotExist:
        logger.error("Pagamento não encontrado: session_id=%s", session_id)
        return

    # SEC-05: the state check and every write happen under row locks in one transaction.
    resultado = (None if pagamento.estado_pagamento in ('pago', 'reembolsado')
                 else services.confirmar_pagamento(pagamento.pk, session_id))
    if resultado is None:
        logger.info("Evento duplicado ignorado: session_id=%s", session_id)
        return

    if resultado == 'reembolsada':
        logger.warning("Pagamento de encomenda cancelada reembolsado: encomenda_id=%s", pagamento.encomenda_id)
    else:
        logger.info("Pagamento confirmado: encomenda_id=%s", pagamento.encomenda_id)
    # The service queued the message with the change; only the delivery that changed something sends it.
    enviar_da_encomenda(pagamento.encomenda_id)


def _handle_payment_expired(session):
    session_id = session['id']

    try:
        pagamento = Pagamento.objects.select_related(
            'encomenda__utilizador'
        ).get(stripe_session_id=session_id)
    except Pagamento.DoesNotExist:
        logger.error("Pagamento não encontrado: session_id=%s", session_id)
        return

    if pagamento.estado_pagamento != 'pendente':
        return

    # SEC-04 (owner decision): an expired session cancels its unpaid order and returns the stock.
    cancelada = services.cancelar_por_expiracao(pagamento.pk, session_id)
    logger.info("Pagamento expirado: encomenda_id=%s cancelada=%s", pagamento.encomenda_id, cancelada)
    if cancelada:  # the message was queued with the cancellation; send it now
        enviar_da_encomenda(pagamento.encomenda_id)
