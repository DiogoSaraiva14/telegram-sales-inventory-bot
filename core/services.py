"""ORM operations used by the Telegram bot.

Checkout (`reservar_encomenda`, `garantir_sessao_pagamento`) is synchronous and
transactional; the handlers call it through sync_to_async. The other helpers were
moved unchanged from bot.py and are not yet transactional (SECURITY.md SEC-06).
"""
import hashlib
from datetime import timedelta

import stripe
from asgiref.sync import sync_to_async
from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import F
from django.utils import timezone

from core.models import (
    Utilizador, Produto, Encomenda, LinhaEncomenda,
    MovimentoStock, RegistoAuditoria, Pagamento,
)
from security.sanitization import validar_telegram_id, sanitizar_nome_utilizador

stripe.api_key = settings.STRIPE_SECRET_KEY


# ── Utilizadores ───────────────────────────────────────────────────────────

def _obter_ou_criar_utilizador(user):
    telegram_id_limpo = validar_telegram_id(user.id)
    nome_limpo = sanitizar_nome_utilizador(user.full_name)
    return Utilizador.objects.get_or_create(
        telegram_id=telegram_id_limpo,
        defaults={"nome": nome_limpo, "username": user.username or ""}
    )

async def get_or_create_utilizador(user):
    return await sync_to_async(_obter_ou_criar_utilizador)(user)

async def utilizador_bloqueado(user_id: int) -> bool:
    try:
        u = await sync_to_async(Utilizador.objects.get)(telegram_id=str(user_id))
        return not u.ativo
    except Utilizador.DoesNotExist:
        return False

async def get_todos_utilizadores():
    return await sync_to_async(list)(Utilizador.objects.all().order_by('nome'))

async def toggle_utilizador_ativo(telegram_id: str) -> tuple:
    utilizador = await sync_to_async(Utilizador.objects.get)(telegram_id=telegram_id)
    utilizador.ativo = not utilizador.ativo
    await sync_to_async(utilizador.save)()
    return utilizador, utilizador.ativo


# ── Produtos e stock ───────────────────────────────────────────────────────

async def get_produtos_por_categoria(categoria):
    return await sync_to_async(list)(
        Produto.objects.filter(ativo=True, categoria=categoria).order_by('nome')
    )

async def get_produto(produto_id: int):
    return await sync_to_async(Produto.objects.get)(id=produto_id, ativo=True)

async def get_produto_admin(produto_id: int):
    return await sync_to_async(Produto.objects.get)(id=produto_id)

async def get_todos_produtos():
    return await sync_to_async(list)(
        Produto.objects.all().order_by('categoria', 'nome')
    )

async def alternar_produto_ativo(produto):
    produto.ativo = not produto.ativo
    # update(), not save(): a full save would write back a stale stock_atual (SEC-10).
    await sync_to_async(Produto.objects.filter(pk=produto.pk).update)(ativo=produto.ativo)

def ajustar_stock(produto_id: int, delta: int, observacoes: str) -> int:
    """Add delta to a product's stock under its row lock, never below zero (SEC-10).

    Writes one stock movement for the amount actually applied and returns the new stock.
    The lock is the one checkout takes, so neither change is lost.
    """
    with transaction.atomic():
        atual = Produto.objects.select_for_update().values_list('stock_atual', flat=True).get(pk=produto_id)
        novo = max(0, atual + delta)
        if novo != atual:
            Produto.objects.filter(pk=produto_id).update(stock_atual=novo)
            MovimentoStock.objects.create(
                produto_id=produto_id, tipo_movimento='entrada' if novo > atual else 'saida',
                quantidade=abs(novo - atual), observacoes=observacoes,
            )
    return novo

async def ajustar_stock_manual(produto, delta: int):
    produto.stock_atual = await sync_to_async(ajustar_stock)(
        produto.id, delta, f"Ajuste manual via bot admin (delta={delta})")


# ── Encomendas ─────────────────────────────────────────────────────────────

async def get_encomendas_utilizador(telegram_id):
    return await sync_to_async(list)(
        Encomenda.objects.filter(utilizador__telegram_id=str(telegram_id))
        .order_by('-data_encomenda')
    )

async def get_encomendas_ativas():
    return await sync_to_async(list)(
        Encomenda.objects.filter(estado__in=['pendente', 'confirmada', 'enviada'])
        .select_related('utilizador')
        .order_by('data_encomenda')
    )

async def get_encomenda_detalhe(encomenda_id: int):
    return await sync_to_async(
        Encomenda.objects.select_related('utilizador').get
    )(id=encomenda_id)

async def get_linhas_encomenda(encomenda):
    return await sync_to_async(list)(
        encomenda.linhas.select_related("produto").all()
    )

async def cancelar_encomenda_db(encomenda_id: int, origem: str = 'admin Telegram'):
    return await sync_to_async(cancelar_encomenda)(encomenda_id, origem)

# Admin status steps (SEC-10, owner decision 2026-11-24): forward only. Orders are confirmed
# only by the Stripe webhook, and cancelled only by cancelar_encomenda.
ESTADO_ANTERIOR = {'enviada': 'confirmada', 'entregue': 'enviada'}

def avancar_estado_encomenda(encomenda_id: int, novo_estado: str, origem: str = ''):
    """Move an order one step forward; returns it, or None if missing or not in the required state."""
    anterior = ESTADO_ANTERIOR.get(novo_estado)
    if anterior is None:
        return None
    with transaction.atomic():
        encomenda = (Encomenda.objects.select_for_update(of=('self',)).select_related('utilizador')
                     .filter(pk=encomenda_id, estado=anterior).first())
        if encomenda is None:
            return None
        encomenda.estado = novo_estado
        encomenda.save(update_fields=['estado', 'data_atualizacao'])
        RegistoAuditoria.objects.create(
            utilizador=encomenda.utilizador, acao=f'ENCOMENDA_{novo_estado.upper()}', entidade='Encomenda',
            descricao=f'#{encomenda.id} {anterior} -> {novo_estado} ({origem or "admin"})',
        )
    return encomenda

async def atualizar_estado_encomenda(encomenda_id: int, novo_estado: str):
    return await sync_to_async(avancar_estado_encomenda)(encomenda_id, novo_estado, 'admin Telegram')


# ── Auditoria ──────────────────────────────────────────────────────────────

async def get_ultimos_registos_auditoria(n=10):
    return await sync_to_async(list)(
        RegistoAuditoria.objects.select_related('utilizador')
        .order_by('-data_hora')[:n]
    )

async def registar_auditoria(utilizador_id, acao, entidade, descricao=""):
    utilizador = await sync_to_async(Utilizador.objects.get)(
        telegram_id=str(utilizador_id)
    )
    await sync_to_async(RegistoAuditoria.objects.create)(
        utilizador=utilizador,
        acao=acao,
        entidade=entidade,
        descricao=descricao
    )


# ── Checkout: uma encomenda por ecrã de confirmação (SEC-13) ───────────────

def _encomenda_por_chave(chave):
    """Step-3 lookup; tests patch it to force the race paths."""
    return Encomenda.objects.select_related('utilizador').filter(chave_confirmacao=chave).first()

def _resultado_existente(encomenda, utilizador):
    return ("existente" if encomenda.utilizador_id == utilizador.id else "de_outro_utilizador", encomenda)

def reservar_encomenda(telegram_user, produto_id: int, chave: str):
    """Create the order for one confirmation screen, all or nothing.

    Returns (resultado, encomenda); resultado is "criada", "existente",
    "de_outro_utilizador" or "sem_stock" (encomenda is None for "sem_stock").
    """
    with transaction.atomic():
        utilizador, _ = _obter_ou_criar_utilizador(telegram_user)
        # Row lock: one checkout per product at a time, so stock cannot be oversold.
        produto = Produto.objects.select_for_update().get(id=produto_id, ativo=True)
        existente = _encomenda_por_chave(chave)
        if existente:
            return _resultado_existente(existente, utilizador)
        if produto.stock_atual < 1:
            return ("sem_stock", None)
        try:
            with transaction.atomic():  # savepoint: a unique violation must not break the outer transaction
                encomenda = Encomenda.objects.create(
                    utilizador=utilizador, estado='pendente', total=produto.preco,
                    chave_confirmacao=chave,
                )
        except IntegrityError:
            return _resultado_existente(_encomenda_por_chave(chave), utilizador)
        LinhaEncomenda.objects.create(
            encomenda=encomenda, produto=produto, quantidade=1,
            preco_unitario=produto.preco, subtotal=produto.preco, nome_produto=produto.nome,
        )
        # update() instead of Produto.save(): save() reopens and resizes the image (B3).
        Produto.objects.filter(pk=produto.pk).update(stock_atual=F('stock_atual') - 1)
        MovimentoStock.objects.create(
            produto=produto, tipo_movimento='saida', quantidade=1,
            observacoes=f"Encomenda #{encomenda.id} via bot",
        )
        RegistoAuditoria.objects.create(
            utilizador=utilizador, acao="CRIAR_ENCOMENDA", entidade="Encomenda",
            descricao=f"#{encomenda.id} — {produto.nome} — {produto.preco:.2f}€",
        )
    return ("criada", encomenda)

async def get_encomenda_por_chave(chave):
    return await sync_to_async(_encomenda_por_chave)(chave)

async def reservar_encomenda_async(telegram_user, produto_id: int, chave: str):
    return await sync_to_async(reservar_encomenda)(telegram_user, produto_id, chave)


# ── Checkout: sessão Stripe recuperável (D17–D19, P15) ─────────────────────

def _impressao_urls():
    urls = f"{settings.STRIPE_SUCCESS_URL}\n{settings.STRIPE_CANCEL_URL}"
    return hashlib.sha256(urls.encode("utf-8")).hexdigest()[:12]

def chave_primeira_tentativa(encomenda):
    return f"checkout-encomenda-{encomenda.id}-{encomenda.chave_confirmacao}-u{_impressao_urls()}"

def chave_substituicao(encomenda, sessao_expirada_id):
    resumo = hashlib.sha256(sessao_expirada_id.encode("utf-8")).hexdigest()
    return f"checkout-encomenda-{encomenda.id}-substitui-{resumo}-u{_impressao_urls()}"

def parametros_checkout(encomenda):
    """Stripe request built only from saved order data, so a reused key always matches."""
    linha = encomenda.linhas.get()
    if not linha.nome_produto:
        raise ValueError(f"Encomenda #{encomenda.id} sem nome de produto guardado")
    return dict(
        payment_method_types=['card'],
        line_items=[{
            'price_data': {
                'currency': 'eur',
                'product_data': {'name': linha.nome_produto},
                'unit_amount': int(linha.preco_unitario * 100),
            },
            'quantity': linha.quantidade,
        }],
        mode='payment',
        success_url=settings.STRIPE_SUCCESS_URL,
        cancel_url=settings.STRIPE_CANCEL_URL,
        metadata={'encomenda_id': str(encomenda.id)},
        client_reference_id=str(encomenda.id),
    )

def _bloquear_se_pendente(encomenda):
    """Inside a transaction: lock the order row and report whether it is still pending.

    A cancellation (Telegram admin save() or Django admin update()) must write this row,
    so it either committed before this check or waits until the session is recorded.
    """
    estado = Encomenda.objects.select_for_update().values_list('estado', flat=True).get(pk=encomenda.pk)
    return estado == 'pendente'

def _ainda_pendente(encomenda):
    return Encomenda.objects.filter(pk=encomenda.pk, estado='pendente').exists()

def _pagamento_de(encomenda):
    """Existing Pagamento or None; tests patch it to force the concurrent first attempt."""
    return Pagamento.objects.filter(encomenda=encomenda).first()

def garantir_sessao_pagamento(encomenda):
    """Return (estado, url) for the order's Stripe Checkout Session.

    estado: "aberta" (url to show), "paga", "a_confirmar" (completed, webhook pending)
    or "nao_ativa". The session id is saved, under a lock on a still-pending order,
    before a url is returned; Stripe is never called while a lock is held. Raises on
    Stripe or database errors; the caller then shows the payment-link failure screen.
    """
    encomenda.refresh_from_db(fields=['estado'])
    if encomenda.estado != 'pendente':
        return ("nao_ativa", None)

    pagamento = _pagamento_de(encomenda)
    if pagamento is None:
        sessao = stripe.checkout.Session.create(
            **parametros_checkout(encomenda), idempotency_key=chave_primeira_tentativa(encomenda),
        )
        with transaction.atomic():
            if not _bloquear_se_pendente(encomenda):
                return ("nao_ativa", None)  # cancelled during the Stripe call; the unrecorded session is never shown
            pagamento, criado = Pagamento.objects.get_or_create(
                encomenda=encomenda,
                defaults=dict(metodo='stripe', valor=encomenda.total,
                              estado_pagamento='pendente', stripe_session_id=sessao.id),
            )
        if criado or pagamento.stripe_session_id == sessao.id:
            return ("aberta", sessao.url)
        # Another attempt recorded its session first: continue with that one.

    for _ in range(2):  # a second pass only after losing a replacement race
        if pagamento.estado_pagamento == 'pago':
            return ("paga", None)
        if pagamento.estado_pagamento == 'reembolsado':
            return ("nao_ativa", None)
        if not pagamento.stripe_session_id:
            raise ValueError(f"Pagamento da encomenda #{encomenda.id} sem sessão Stripe")
        sessao = stripe.checkout.Session.retrieve(pagamento.stripe_session_id)
        if sessao.status == 'open':
            return ("aberta", sessao.url) if _ainda_pendente(encomenda) else ("nao_ativa", None)
        if sessao.status == 'complete':
            # Paid or finishing: the webhook confirms it. Never replace a completed session.
            return ("a_confirmar", None)
        # expired: replacement attempt with a new key (D19)
        antiga = pagamento.stripe_session_id
        nova = stripe.checkout.Session.create(
            **parametros_checkout(encomenda), idempotency_key=chave_substituicao(encomenda, antiga),
        )
        with transaction.atomic():
            if not _bloquear_se_pendente(encomenda):
                return ("nao_ativa", None)
            atualizados = Pagamento.objects.filter(
                pk=pagamento.pk, stripe_session_id=antiga, estado_pagamento__in=['pendente', 'falhado'],
            ).update(stripe_session_id=nova.id, estado_pagamento='pendente', valor=encomenda.total)
        if atualizados:
            return ("aberta", nova.url)
        pagamento.refresh_from_db()  # replaced or paid by someone else meanwhile
    raise RuntimeError(f"Sessão de pagamento da encomenda #{encomenda.id} mudou durante a substituição")

async def garantir_sessao_pagamento_async(encomenda):
    return await sync_to_async(garantir_sessao_pagamento)(encomenda)


# ── Confirmação do pagamento (SEC-05) ─────────────────────────────────────

def confirmar_pagamento(pagamento_id: int, sessao_id: str):
    """A Stripe Checkout Session completed: mark the payment paid and the order confirmed.

    One transaction under the order row lock, then the payment row lock (the same order
    as the other payment code), so concurrent duplicate deliveries run one after the
    other and only the first changes anything. Returns "confirmada" or "reembolsada"
    (SEC-06: the order was cancelled, so the payment is refunded in full) for that first
    one, else None. A Stripe refund error propagates, rolls everything back, and the
    webhook answers 500 so Stripe redelivers; the refund's idempotency key makes that safe.
    """
    with transaction.atomic():
        encomenda_id = Pagamento.objects.values_list('encomenda_id', flat=True).get(pk=pagamento_id)
        encomenda = Encomenda.objects.select_for_update().get(pk=encomenda_id)
        pagamento = Pagamento.objects.select_for_update().get(pk=pagamento_id)
        if pagamento.stripe_session_id != sessao_id or pagamento.estado_pagamento in ('pago', 'reembolsado'):
            return None  # duplicate delivery, or the session was replaced meanwhile
        pagamento.data_pagamento = timezone.now()
        if encomenda.estado == 'cancelada':  # SEC-06 owner decision: refund in full, keep it cancelled
            _reembolsar(encomenda, pagamento)
            pagamento.estado_pagamento = 'reembolsado'
            pagamento.save(update_fields=['estado_pagamento', 'data_pagamento'])
            RegistoAuditoria.objects.create(
                utilizador=encomenda.utilizador, acao='PAGAMENTO_REEMBOLSADO', entidade='Pagamento',
                descricao=f'Encomenda #{encomenda.id} já cancelada: pagamento reembolsado (session={sessao_id})',
            )
            return 'reembolsada'
        pagamento.estado_pagamento = 'pago'
        pagamento.save(update_fields=['estado_pagamento', 'data_pagamento'])
        encomenda.estado = 'confirmada'
        encomenda.save(update_fields=['estado', 'data_atualizacao'])
        RegistoAuditoria.objects.create(
            utilizador=encomenda.utilizador, acao='PAGAMENTO_CONFIRMADO', entidade='Pagamento',
            descricao=f'Encomenda #{encomenda.id} paga via Stripe (session={sessao_id})',
        )
    return 'confirmada'


# ── Cancelamento (SEC-06, owner decisions 2026-11-24) ──────────────────────

class PagamentoRecebido(Exception):
    """The order's Stripe session is complete: the payment is arriving, so nothing is cancelled."""


def _reembolsar(encomenda, pagamento):
    """Full Stripe refund of the order's payment; one key per order and session, so retries are safe."""
    payment_intent = stripe.checkout.Session.retrieve(pagamento.stripe_session_id).payment_intent
    stripe.Refund.create(
        payment_intent=payment_intent,
        idempotency_key=f"reembolso-encomenda-{encomenda.id}-{hashlib.sha256(pagamento.stripe_session_id.encode()).hexdigest()}",
    )


def _devolver_stock(encomenda, observacoes):
    for linha in encomenda.linhas.all():
        # update() instead of Produto.save(): no image access (B3) and no lost update.
        Produto.objects.filter(pk=linha.produto_id).update(stock_atual=F('stock_atual') + linha.quantidade)
        MovimentoStock.objects.create(
            produto_id=linha.produto_id, tipo_movimento='entrada', quantidade=linha.quantidade,
            observacoes=observacoes,
        )


def cancelar_encomenda(encomenda_id: int, origem: str = ''):
    """Cancel a pending or confirmed order, for every admin surface.

    Settles the Stripe payment first: an open session is expired, a paid one is refunded
    in full. Then returns the stock and writes the audit row. Returns (resultado, encomenda)
    with resultado "cancelada", "reembolsada" or "invalida" (missing order, or a state that
    cannot be cancelled). Raises PagamentoRecebido if the session already completed, and
    stripe.error.StripeError if Stripe fails; nothing changes in either case.

    The order row lock is held across the Stripe calls, so this order's webhooks wait and
    then see the final state. A refund retried after a failed commit reuses its key.
    """
    with transaction.atomic():
        encomenda = (Encomenda.objects.select_for_update(of=('self',)).select_related('utilizador')
                     .filter(pk=encomenda_id).first())
        if encomenda is None or encomenda.estado not in ('pendente', 'confirmada'):
            return ('invalida', encomenda)
        pagamento = Pagamento.objects.select_for_update().filter(encomenda=encomenda).first()
        resultado = 'cancelada'
        if pagamento and pagamento.estado_pagamento == 'pago':
            if not pagamento.stripe_session_id:
                return ('invalida', encomenda)  # paid outside Stripe: no refund path here
            _reembolsar(encomenda, pagamento)
            pagamento.estado_pagamento = 'reembolsado'
            pagamento.save(update_fields=['estado_pagamento'])
            resultado = 'reembolsada'
        elif pagamento and pagamento.stripe_session_id and pagamento.estado_pagamento in ('pendente', 'falhado'):
            estado_sessao = stripe.checkout.Session.retrieve(pagamento.stripe_session_id).status
            if estado_sessao == 'complete':
                raise PagamentoRecebido(encomenda.id)
            if estado_sessao == 'open':
                stripe.checkout.Session.expire(pagamento.stripe_session_id)
            pagamento.estado_pagamento = 'falhado'
            pagamento.save(update_fields=['estado_pagamento'])
        _devolver_stock(encomenda, f"Cancelamento Encomenda #{encomenda.id}")
        encomenda.estado = 'cancelada'
        encomenda.save(update_fields=['estado', 'data_atualizacao'])
        RegistoAuditoria.objects.create(
            utilizador=encomenda.utilizador, acao='CANCELAR_ENCOMENDA', entidade='Encomenda',
            descricao=f'#{encomenda.id} cancelada ({origem or "admin"}); stock devolvido'
                      + ('; pagamento reembolsado' if resultado == 'reembolsada' else ''),
        )
    return (resultado, encomenda)


# ── Encomendas abandonadas sem pagamento (SEC-03, owner decision 2026-11-24) ──

def _limite_abandono(horas):
    return timezone.now() - timedelta(hours=horas)

def encomendas_abandonadas(horas: int = 24):
    """Ids of pending orders older than `horas` that never got a payment session."""
    return list(Encomenda.objects.filter(
        estado='pendente', data_encomenda__lt=_limite_abandono(horas), pagamento__isnull=True,
    ).values_list('id', flat=True))

def cancelar_se_abandonada(encomenda_id: int, horas: int = 24):
    """Cancel the order if it is still pending, old enough and without a payment; else None.

    The order row lock is the one payment-session code takes before saving a Pagamento,
    so a retry that is creating a session either finished first (and the order is kept)
    or finds the order cancelled. No Stripe call is made: there is no session.
    """
    with transaction.atomic():
        encomenda = (Encomenda.objects.select_for_update()
                     .filter(pk=encomenda_id, estado='pendente', data_encomenda__lt=_limite_abandono(horas))
                     .first())
        if encomenda is None or Pagamento.objects.filter(encomenda_id=encomenda_id).exists():
            return None
        return cancelar_encomenda(encomenda_id, f'limpeza: sem pagamento há mais de {horas} h')[1]


# ── Expiração do pagamento (SEC-04) ────────────────────────────────────────

def cancelar_por_expiracao(pagamento_id: int, sessao_id: str) -> bool:
    """A Stripe Checkout Session expired: cancel its order and return the stock.

    All in one transaction, under the order row lock that payment-session code also
    takes (so a concurrent replacement either finished first, and this session is no
    longer the stored one, or waits and then sees the order cancelled). If this session is
    still the stored one and its payment is pending, marks the payment "falhado"; otherwise
    changes nothing. Returns True only if the order was cancelled now.
    """
    with transaction.atomic():
        encomenda_id = Pagamento.objects.values_list('encomenda_id', flat=True).get(pk=pagamento_id)
        encomenda = Encomenda.objects.select_for_update().get(pk=encomenda_id)
        pagamento = Pagamento.objects.select_for_update().get(pk=pagamento_id)
        if pagamento.stripe_session_id != sessao_id or pagamento.estado_pagamento != 'pendente':
            return False  # replaced meanwhile, or already paid/failed: nothing to do
        pagamento.estado_pagamento = 'falhado'
        pagamento.save(update_fields=['estado_pagamento'])
        RegistoAuditoria.objects.create(
            utilizador=encomenda.utilizador, acao='PAGAMENTO_EXPIRADO', entidade='Pagamento',
            descricao=f'Encomenda #{encomenda.id} expirou sem pagamento',
        )
        if encomenda.estado != 'pendente':
            return False  # e.g. already cancelled by an admin, who returned the stock then
        _devolver_stock(encomenda, f"Pagamento expirado — Encomenda #{encomenda.id}")
        encomenda.estado = 'cancelada'
        encomenda.save(update_fields=['estado', 'data_atualizacao'])
        RegistoAuditoria.objects.create(
            utilizador=encomenda.utilizador, acao='CANCELAR_ENCOMENDA_EXPIRADA', entidade='Encomenda',
            descricao=f'#{encomenda.id} cancelada e stock devolvido após expiração do pagamento',
        )
    return True
