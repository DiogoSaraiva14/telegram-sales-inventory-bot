"""ORM operations used by the Telegram bot.

Checkout (`reservar_encomenda`, `garantir_sessao_pagamento`) is synchronous and
transactional; the handlers call it through sync_to_async. The other helpers were
moved unchanged from bot.py and are not yet transactional (SECURITY.md SEC-06).
"""
import hashlib

import stripe
from asgiref.sync import sync_to_async
from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import F

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
    await sync_to_async(produto.save)()

async def ajustar_stock_manual(produto, delta: int):
    produto.stock_atual = max(0, produto.stock_atual + delta)
    await sync_to_async(produto.save)()
    tipo = "entrada" if delta > 0 else "saida"
    await sync_to_async(MovimentoStock.objects.create)(
        produto=produto,
        tipo_movimento=tipo,
        quantidade=abs(delta),
        observacoes=f"Ajuste manual via bot admin (delta={delta})"
    )


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

async def cancelar_encomenda_db(encomenda_id: int, utilizador_id_telegram: str = None):
    if utilizador_id_telegram:
        encomenda = await sync_to_async(
            Encomenda.objects.select_related('utilizador').get
        )(
            id=encomenda_id,
            utilizador__telegram_id=str(utilizador_id_telegram),
            estado='pendente'
        )
    else:
        encomenda = await sync_to_async(Encomenda.objects.get)(
            id=encomenda_id,
            estado__in=['pendente', 'confirmada']
        )
    linhas = await sync_to_async(list)(encomenda.linhas.select_related('produto').all())
    for linha in linhas:
        linha.produto.stock_atual += linha.quantidade
        await sync_to_async(linha.produto.save)()
        await sync_to_async(MovimentoStock.objects.create)(
            produto=linha.produto,
            tipo_movimento='entrada',
            quantidade=linha.quantidade,
            observacoes=f"Cancelamento Encomenda #{encomenda.id}"
        )
    encomenda.estado = 'cancelada'
    await sync_to_async(encomenda.save)()
    return encomenda

async def atualizar_estado_encomenda(encomenda_id: int, novo_estado: str):
    encomenda = await sync_to_async(
        Encomenda.objects.select_related('utilizador').get
    )(id=encomenda_id)
    encomenda.estado = novo_estado
    await sync_to_async(encomenda.save)()
    return encomenda


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
