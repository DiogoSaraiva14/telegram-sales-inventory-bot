import stripe
import logging
import httpx
from django.conf import settings
from django.http import HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from django.utils import timezone
from .models import Pagamento, Encomenda, RegistoAuditoria

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

    # Prevent duplicate processing
    if pagamento.estado_pagamento == 'pago':
        logger.info("Evento duplicado ignorado: session_id=%s", session_id)
        return

    pagamento.estado_pagamento = 'pago'
    pagamento.data_pagamento = timezone.now()
    pagamento.save()

    encomenda = pagamento.encomenda
    encomenda.estado = 'confirmada'
    encomenda.save()

    RegistoAuditoria.objects.create(
        utilizador=encomenda.utilizador,
        acao='PAGAMENTO_CONFIRMADO',
        entidade='Pagamento',
        descricao=f'Encomenda #{encomenda.id} paga via Stripe (session={session_id})'
    )
    logger.info("Pagamento confirmado: encomenda_id=%s", encomenda.id)

    # Notify customer via Telegram Bot API directly
    _notificar_pagamento_telegram(encomenda)


def _notificar_pagamento_telegram(encomenda):
    token = settings.TELEGRAM_TOKEN
    if not token:
        logger.warning("TELEGRAM_TOKEN não configurado — notificação ignorada")
        return

    chat_id = encomenda.utilizador.telegram_id
    mensagem = (
        f"✅ *Pagamento confirmado!*\n\n"
        f"📦 Encomenda #{encomenda.id}\n"
        f"💶 Total: {encomenda.total:.2f}€\n\n"
        f"O teu pagamento foi recebido. Estamos a preparar o teu pedido! 🙏"
    )

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        response = httpx.post(url, json={
            "chat_id": chat_id,
            "text": mensagem,
            "parse_mode": "Markdown",
        }, timeout=10)
        if response.status_code == 200:
            logger.info("Notificação Telegram enviada: telegram_id=%s encomenda=#%s",
                        chat_id, encomenda.id)
        else:
            logger.error("Falha na notificação Telegram: status=%s body=%s",
                         response.status_code, response.text)
    except Exception as exc:
        logger.error("Erro ao enviar notificação Telegram: %s", exc)


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

    pagamento.estado_pagamento = 'falhado'
    pagamento.save()

    RegistoAuditoria.objects.create(
        utilizador=pagamento.encomenda.utilizador,
        acao='PAGAMENTO_EXPIRADO',
        entidade='Pagamento',
        descricao=f'Encomenda #{pagamento.encomenda.id} expirou sem pagamento'
    )
    logger.info("Pagamento expirado: encomenda_id=%s", pagamento.encomenda.id)
