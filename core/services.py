"""Async ORM operations used by the Telegram bot.

Moved unchanged from bot.py. These are not yet transactional; see
SECURITY.md (SEC-02, SEC-03, SEC-06) and ROADMAP.md phase 2.
"""
import stripe
from asgiref.sync import sync_to_async
from django.conf import settings

from core.models import (
    Utilizador, Produto, Encomenda, LinhaEncomenda,
    MovimentoStock, RegistoAuditoria, Pagamento,
)
from security.sanitization import validar_telegram_id, sanitizar_nome_utilizador

stripe.api_key = settings.STRIPE_SECRET_KEY


# ── Utilizadores ───────────────────────────────────────────────────────────

async def get_or_create_utilizador(user):
    telegram_id_limpo = validar_telegram_id(user.id)
    nome_limpo = sanitizar_nome_utilizador(user.full_name)
    return await sync_to_async(Utilizador.objects.get_or_create)(
        telegram_id=telegram_id_limpo,
        defaults={"nome": nome_limpo, "username": user.username or ""}
    )

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

async def criar_encomenda(utilizador, produto):
    encomenda = await sync_to_async(Encomenda.objects.create)(
        utilizador=utilizador,
        estado='pendente',
        total=produto.preco
    )
    await sync_to_async(LinhaEncomenda.objects.create)(
        encomenda=encomenda,
        produto=produto,
        quantidade=1,
        preco_unitario=produto.preco,
        subtotal=produto.preco
    )
    produto.stock_atual = max(0, produto.stock_atual - 1)
    await sync_to_async(produto.save)()
    await sync_to_async(MovimentoStock.objects.create)(
        produto=produto,
        tipo_movimento='saida',
        quantidade=1,
        observacoes=f"Encomenda #{encomenda.id} via bot"
    )
    return encomenda

async def criar_sessao_stripe(encomenda, produto):
    session = await sync_to_async(stripe.checkout.Session.create)(
        payment_method_types=['card'],
        line_items=[{
            'price_data': {
                'currency': 'eur',
                'product_data': {
                    'name': produto.nome,
                    'description': produto.descricao or '',
                },
                'unit_amount': int(produto.preco * 100),
            },
            'quantity': 1,
        }],
        mode='payment',
        success_url=settings.STRIPE_SUCCESS_URL,
        cancel_url=settings.STRIPE_CANCEL_URL,
        metadata={'encomenda_id': str(encomenda.id)},
    )
    await sync_to_async(Pagamento.objects.create)(
        encomenda=encomenda,
        metodo='stripe',
        valor=encomenda.total,
        estado_pagamento='pendente',
        stripe_session_id=session.id,
    )
    return session.url

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
