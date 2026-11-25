"""ORM operations used by the Telegram bot.

Checkout (`criar_confirmacao`, `criar_encomenda_de_confirmacao`, `garantir_sessao_pagamento`)
is synchronous and transactional; the handlers call it through sync_to_async.

Lock order (cart design): checkout locks the confirmation snapshot, then products, and
never an existing order or the customer row. Cancellation, expiry, the cleanup and the
webhook lock the order, then its payment, then its products. Products are always locked
in one query ordered by id (`_bloquear_produtos`).
"""
import hashlib
import logging
import os
import secrets
from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import NamedTuple

import stripe
from asgiref.sync import sync_to_async
from django.conf import settings
from django.db import DatabaseError, IntegrityError, transaction
from django.db.models import F
from django.utils import timezone

from core.models import (
    Utilizador, Categoria, Carrinho, Loja, ConfirmacaoCarrinho, LinhaCarrinho, Produto, Encomenda, LinhaEncomenda,
    MovimentoStock, RegistoAuditoria, Pagamento,
)
from security.sanitization import validar_telegram_id, sanitizar_nome_utilizador

stripe.api_key = settings.STRIPE_SECRET_KEY
logger = logging.getLogger(__name__)

# Cart design, Stage 2. Stripe needs expires_at at least 30 minutes after a session is
# created, so sessions are created only while the order is at most 28 minutes old.
JANELA_PAGAMENTO = timedelta(minutes=60)
CORTE_SESSAO = timedelta(minutes=28)
VALIDADE_SEGUNDOS = 30 * 60  # how long a confirmation screen can be confirmed
MAX_LINHAS = 20
MAX_QUANTIDADE = 99


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

# Bootstrap Telegram admins from .env (cart design, Stage 4). After handover this holds only
# the reseller's id, or nothing; everyone else is an admin through Utilizador.e_admin.
ADMINS_ARRANQUE = frozenset(
    int(x.strip()) for x in os.getenv("ADMIN_TELEGRAM_IDS", "").split(",") if x.strip().isdigit()
)

async def e_admin(user_id) -> bool:
    """Telegram admin check, read from the database on every call so changes apply at once."""
    if int(user_id) in ADMINS_ARRANQUE:
        return True
    return await Utilizador.objects.filter(telegram_id=str(user_id), e_admin=True, ativo=True).aexists()

def obter_loja():
    """The shop settings row, created with neutral defaults on an empty database."""
    return Loja.objects.get_or_create(pk=1)[0]

async def obter_loja_async():
    return await sync_to_async(obter_loja)()

async def get_todos_utilizadores():
    return await sync_to_async(list)(Utilizador.objects.all().order_by('nome'))

async def toggle_utilizador_ativo(telegram_id: str) -> tuple:
    utilizador = await sync_to_async(Utilizador.objects.get)(telegram_id=telegram_id)
    utilizador.ativo = not utilizador.ativo
    await sync_to_async(utilizador.save)()
    return utilizador, utilizador.ativo


# ── Produtos e stock ───────────────────────────────────────────────────────

async def get_categorias_ativas():
    return await sync_to_async(list)(Categoria.objects.filter(ativo=True))

async def get_categoria_ativa(categoria_id: int):
    """The active category with this id, or None (old or deactivated buttons)."""
    return await Categoria.objects.filter(pk=categoria_id, ativo=True).afirst()

async def get_produtos_por_categoria(categoria_id: int):
    return await sync_to_async(list)(
        Produto.objects.filter(ativo=True, categoria_id=categoria_id, categoria__ativo=True).order_by('nome')
    )

async def get_produto(produto_id: int):
    return await sync_to_async(Produto.objects.get)(id=produto_id, ativo=True)

async def get_produto_admin(produto_id: int):
    return await sync_to_async(Produto.objects.select_related('categoria').get)(id=produto_id)

async def get_todos_produtos():
    return await sync_to_async(list)(
        Produto.objects.select_related('categoria').order_by('categoria__ordem', 'categoria__nome', 'nome')
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


# ── Checkout: uma encomenda por ecrã de confirmação (SEC-13, Stage 2) ──────

def _centimos(valor) -> int:
    return int((Decimal(valor) * 100).quantize(Decimal('1'), ROUND_HALF_UP))

def _euros(centimos: int) -> Decimal:
    return Decimal(centimos) / 100

def _bloquear_produtos(ids):
    """Lock these products in id order (no deadlocks); returns {id: Produto}. Tests pause here."""
    return {p.pk: p for p in Produto.objects.select_for_update().filter(pk__in=ids).order_by('pk')}


class Alteracao(NamedTuple):
    """One difference between a confirmation screen and live data."""
    produto_id: int
    nome: str
    tipo: str  # "preco", "stock", "inativo" or "removido"
    antes: object = None
    depois: object = None


def criar_confirmacao(telegram_user, linhas, revisao_carrinho=None):
    """Save what the confirmation screen shows; linhas is [(produto_id, quantidade)].

    Reads live product data without locks. Returns ("ok", snapshot), or
    ("bloqueado", [Alteracao]) when a product is missing, inactive or short of stock.
    """
    utilizador, _ = _obter_ou_criar_utilizador(telegram_user)
    produtos = Produto.objects.in_bulk([pid for pid, _ in linhas])
    problemas = []
    for pid, quantidade in linhas:
        p = produtos.get(pid)
        if p is None:
            problemas.append(Alteracao(pid, "", "removido"))
        elif not p.ativo:
            problemas.append(Alteracao(pid, p.nome, "inativo"))
        elif p.stock_atual < quantidade:
            problemas.append(Alteracao(pid, p.nome, "stock", quantidade, p.stock_atual))
    if problemas:
        return ("bloqueado", problemas)
    snap_linhas = [
        {"produto_id": pid, "nome": produtos[pid].nome, "quantidade": quantidade,
         "preco_unitario_centimos": _centimos(produtos[pid].preco)}
        for pid, quantidade in sorted(linhas)
    ]
    snapshot = ConfirmacaoCarrinho.objects.create(
        nonce=secrets.token_hex(8), utilizador=utilizador, revisao_carrinho=revisao_carrinho,
        total_centimos=sum(l["quantidade"] * l["preco_unitario_centimos"] for l in snap_linhas),
        linhas=snap_linhas,
    )
    return ("ok", snapshot)

def _alteracoes(snap_linhas, produtos):
    alteracoes = []
    for linha in snap_linhas:
        pid, nome, quantidade = linha["produto_id"], linha["nome"], linha["quantidade"]
        p = produtos.get(pid)
        if p is None:
            alteracoes.append(Alteracao(pid, nome, "removido"))
            continue
        if not p.ativo:
            alteracoes.append(Alteracao(pid, nome, "inativo"))
        if _centimos(p.preco) != linha["preco_unitario_centimos"]:
            alteracoes.append(Alteracao(pid, nome, "preco", _euros(linha["preco_unitario_centimos"]), p.preco))
        if p.stock_atual < quantidade:
            alteracoes.append(Alteracao(pid, nome, "stock", quantidade, p.stock_atual))
    return alteracoes

def _pendente_de(utilizador_id):
    return (Encomenda.objects.select_related('utilizador')
            .filter(utilizador_id=utilizador_id, estado='pendente').order_by('pk').first())

def criar_encomenda_de_confirmacao(telegram_user, nonce: str):
    """Create the order for one confirmation screen, all or nothing.

    Returns (resultado, valor):
      "criada" / "existente" (this screen's order) / "pendente" (the customer's pending order): the order;
      "alterado": [Alteracao], with nothing written;
      "de_outro_utilizador", "invalida", "expirada": None.
    """
    telegram_id = validar_telegram_id(telegram_user.id)
    with transaction.atomic():
        snap = (ConfirmacaoCarrinho.objects.select_for_update(of=('self',)).select_related('utilizador')
                .filter(nonce=nonce).first())
        if snap is None:
            return ("invalida", None)
        dono = snap.utilizador.telegram_id == telegram_id
        existente = Encomenda.objects.select_related('utilizador').filter(confirmacao=snap).first()
        if existente is not None:  # an existing order always wins, whatever the screen's age
            return ("existente", existente) if dono else ("de_outro_utilizador", None)
        if not dono:
            return ("de_outro_utilizador", None)
        if timezone.now() - snap.criada_em > timedelta(seconds=VALIDADE_SEGUNDOS):
            return ("expirada", None)
        # A plain read is enough: legacy orders can't be created any more, and the partial
        # unique constraint below covers two new orders racing each other.
        pendente = _pendente_de(snap.utilizador_id)
        if pendente is not None:
            return ("pendente", pendente)
        carrinho = None
        if snap.revisao_carrinho is not None:  # a cart screen: lock the cart before the products
            carrinho = Carrinho.objects.select_for_update().filter(utilizador_id=snap.utilizador_id).first()
        produtos = _bloquear_produtos([l["produto_id"] for l in snap.linhas])
        alteracoes = _alteracoes(snap.linhas, produtos)
        if snap.revisao_carrinho is not None and (carrinho is None or carrinho.revisao != snap.revisao_carrinho):
            alteracoes.insert(0, Alteracao(0, "", "carrinho"))  # the cart changed after the screen was drawn
        if alteracoes:
            return ("alterado", alteracoes)  # nothing has been written
        try:
            with transaction.atomic():  # savepoint: a unique violation must not break the outer transaction
                encomenda = Encomenda.objects.create(
                    utilizador_id=snap.utilizador_id, estado='pendente', total=_euros(snap.total_centimos),
                    confirmacao=snap, chave_confirmacao=snap.nonce,
                )
        except IntegrityError:
            pendente = _pendente_de(snap.utilizador_id)
            if pendente is None:
                raise
            return ("pendente", pendente)
        encomenda.utilizador = snap.utilizador
        descricao = []
        for linha in snap.linhas:  # already ordered by produto_id
            pid, quantidade = linha["produto_id"], linha["quantidade"]
            preco = _euros(linha["preco_unitario_centimos"])
            LinhaEncomenda.objects.create(
                encomenda=encomenda, produto_id=pid, quantidade=quantidade,
                preco_unitario=preco, subtotal=preco * quantidade, nome_produto=linha["nome"],
            )
            # update() instead of Produto.save(): save() reopens and resizes the image (B3).
            Produto.objects.filter(pk=pid).update(stock_atual=F('stock_atual') - quantidade)
            MovimentoStock.objects.create(
                produto_id=pid, tipo_movimento='saida', quantidade=quantidade,
                observacoes=f"Encomenda #{encomenda.id} via bot",
            )
            descricao.append(f"{quantidade}× {linha['nome']} @ {preco:.2f}€")
        RegistoAuditoria.objects.create(
            utilizador_id=snap.utilizador_id, acao="CRIAR_ENCOMENDA", entidade="Encomenda",
            descricao=f"#{encomenda.id} — " + "; ".join(descricao) + f" — total {encomenda.total:.2f}€",
        )
        if carrinho is not None:  # the cart became this order
            carrinho.linhas.all().delete()
            _marcar_alteracao(carrinho)
    return ("criada", encomenda)

def _encomenda_por_chave(chave):
    """Order created by a pre-Stage-2 Confirm button with this nonce, or None."""
    return Encomenda.objects.select_related('utilizador').filter(chave_confirmacao=chave).first()

async def get_encomenda_por_chave(chave):
    return await sync_to_async(_encomenda_por_chave)(chave)

async def criar_confirmacao_async(telegram_user, linhas, revisao_carrinho=None):
    return await sync_to_async(criar_confirmacao)(telegram_user, linhas, revisao_carrinho)

async def criar_encomenda_de_confirmacao_async(telegram_user, nonce: str):
    return await sync_to_async(criar_encomenda_de_confirmacao)(telegram_user, nonce)


# ── Carrinho (cart design, Stage 3) ────────────────────────────────────────
# Every change locks the cart row, increases revisao and sets atualizado_em; viewing does neither.

RETENCAO_CONFIRMACOES = timedelta(minutes=45)  # unused screens; screens with an order are kept
RETENCAO_CARRINHOS = timedelta(days=30)


class LinhaVista(NamedTuple):
    """A cart line with live product data, for drawing the cart."""
    produto_id: int
    nome: str
    quantidade: int
    preco: Decimal  # live price
    preco_ao_adicionar: Decimal
    stock: int
    ativo: bool

    @property
    def subtotal(self):
        return self.preco * self.quantidade

    @property
    def preco_alterado(self):  # only marks the line
        return self.preco != self.preco_ao_adicionar

    @property
    def indisponivel(self):
        return not self.ativo

    @property
    def stock_insuficiente(self):
        return self.ativo and self.stock < self.quantidade

    @property
    def bloqueia(self):  # hides "Finalizar compra"
        return self.indisponivel or self.stock_insuficiente


def _marcar_alteracao(carrinho):
    carrinho.revisao += 1
    carrinho.atualizado_em = timezone.now()
    carrinho.save(update_fields=['revisao', 'atualizado_em'])

def _carrinho_bloqueado(telegram_user):
    """The customer's cart, created if needed, locked until the transaction ends."""
    utilizador, _ = _obter_ou_criar_utilizador(telegram_user)
    Carrinho.objects.get_or_create(utilizador=utilizador)
    return Carrinho.objects.select_for_update().get(utilizador=utilizador)

def _mudar_quantidade(telegram_user, produto_id, delta, pode_criar):
    with transaction.atomic():
        carrinho = _carrinho_bloqueado(telegram_user)
        linha = carrinho.linhas.filter(produto_id=produto_id).first()
        if linha is None and not pode_criar:
            return ("inexistente", 0)
        atual = linha.quantidade if linha else 0
        novo = atual + delta
        if novo <= 0:
            linha.delete()
            _marcar_alteracao(carrinho)
            return ("removido", 0)
        produto = Produto.objects.filter(pk=produto_id).first()
        if delta > 0:
            if produto is None or not produto.ativo or produto.stock_atual <= 0:
                return ("indisponivel", atual)
            if linha is None and carrinho.linhas.count() >= MAX_LINHAS:
                return ("limite_linhas", 0)
            if novo > MAX_QUANTIDADE:
                return ("limite_quantidade", atual)
            if novo > produto.stock_atual:
                return ("limite_stock", atual)
        if linha is None:
            LinhaCarrinho.objects.create(carrinho=carrinho, produto=produto, quantidade=novo,
                                         preco_ao_adicionar=produto.preco)
        else:
            linha.quantidade, linha.preco_ao_adicionar = novo, produto.preco
            linha.save(update_fields=['quantidade', 'preco_ao_adicionar'])
        _marcar_alteracao(carrinho)
        return ("adicionado", novo)

def adicionar_ao_carrinho(telegram_user, produto_id: int):
    """Add one unit. Returns (resultado, quantidade): "adicionado", "limite_stock",
    "limite_quantidade", "limite_linhas" or "indisponivel"."""
    return _mudar_quantidade(telegram_user, produto_id, 1, pode_criar=True)

def alterar_quantidade(telegram_user, produto_id: int, delta: int):
    """Like adicionar_ao_carrinho for an existing line; reaching 0 gives ("removido", 0)."""
    return _mudar_quantidade(telegram_user, produto_id, delta, pode_criar=False)

def remover_do_carrinho(telegram_user, produto_id: int) -> str:
    with transaction.atomic():
        carrinho = _carrinho_bloqueado(telegram_user)
        apagadas, _ = carrinho.linhas.filter(produto_id=produto_id).delete()
        if not apagadas:
            return "inexistente"
        _marcar_alteracao(carrinho)
        return "removido"

def ver_carrinho(telegram_user):
    """([LinhaVista], total at live prices). No lock and no write."""
    linhas = [
        LinhaVista(l.produto_id, l.produto.nome, l.quantidade, l.produto.preco, l.preco_ao_adicionar,
                   l.produto.stock_atual, l.produto.ativo)
        for l in LinhaCarrinho.objects.select_related('produto')
        .filter(carrinho__utilizador__telegram_id=validar_telegram_id(telegram_user.id)).order_by('pk')
    ]
    return linhas, sum((l.subtotal for l in linhas), Decimal('0.00'))

def preparar_confirmacao_carrinho(telegram_user):
    """Save the confirmation screen for the whole cart.

    Returns (resultado, valor, precos_alterados): ("ok", snapshot, {produto_id whose price
    changed since it was added}), ("vazio", None, set()) or ("bloqueado", [Alteracao], set()).
    """
    with transaction.atomic():
        carrinho = _carrinho_bloqueado(telegram_user)
        linhas = list(carrinho.linhas.order_by('pk'))
        if not linhas:
            return ("vazio", None, set())
        resultado, valor = criar_confirmacao(
            telegram_user, [(l.produto_id, l.quantidade) for l in linhas], carrinho.revisao)
        if resultado != "ok":
            return (resultado, valor, set())
        precos = {l["produto_id"]: l["preco_unitario_centimos"] for l in valor.linhas}
        alterados = {l.produto_id for l in linhas if _centimos(l.preco_ao_adicionar) != precos[l.produto_id]}
        return ("ok", valor, alterados)

def _pode_repetir(encomenda, telegram_id: str) -> bool:
    return (encomenda.estado == 'cancelada' and encomenda.utilizador.telegram_id == telegram_id
            and not Pagamento.objects.filter(encomenda=encomenda,
                                             estado_pagamento__in=('pago', 'reembolsado')).exists())

def repetir_encomenda(telegram_user, encomenda_id: int):
    """Merge a cancelled unpaid order's lines into the cart; nothing is ordered.

    Returns (adicionados, reduzidos, ignorados) as product names, or None if the order
    can't be repeated by this customer. Lines are capped by stock and MAX_QUANTIDADE;
    inactive or sold-out products, and new lines beyond MAX_LINHAS, are skipped.
    """
    with transaction.atomic():
        encomenda = Encomenda.objects.select_related('utilizador').filter(pk=encomenda_id).first()
        if encomenda is None or not _pode_repetir(encomenda, validar_telegram_id(telegram_user.id)):
            return None
        carrinho = _carrinho_bloqueado(telegram_user)
        existentes = {l.produto_id: l for l in carrinho.linhas.all()}
        adicionados, reduzidos, ignorados = [], [], []
        mudou = False
        for linha in encomenda.linhas.select_related('produto').order_by('pk'):
            produto, nome = linha.produto, linha.nome_produto or linha.produto.nome
            atual = existentes.get(produto.pk)
            if not produto.ativo or produto.stock_atual <= 0 or (atual is None and len(existentes) >= MAX_LINHAS):
                ignorados.append(nome)
                continue
            antes = atual.quantidade if atual else 0
            desejado = antes + linha.quantidade
            novo = min(desejado, MAX_QUANTIDADE, produto.stock_atual)
            if atual is None:
                existentes[produto.pk] = LinhaCarrinho.objects.create(
                    carrinho=carrinho, produto=produto, quantidade=novo, preco_ao_adicionar=produto.preco)
                mudou = True
            elif novo > antes:
                atual.quantidade, atual.preco_ao_adicionar = novo, produto.preco
                atual.save(update_fields=['quantidade', 'preco_ao_adicionar'])
                mudou = True
            (adicionados if novo >= desejado else reduzidos).append(nome)
        if mudou:
            _marcar_alteracao(carrinho)
        return adicionados, reduzidos, ignorados

def limpar_rascunhos():
    """Delete unused confirmation screens after 45 minutes and idle carts after 30 days.

    Returns (screens, carts) deleted. A screen linked to an order is never deleted.
    """
    agora = timezone.now()
    _, ecras = ConfirmacaoCarrinho.objects.filter(
        encomenda__isnull=True, criada_em__lt=agora - RETENCAO_CONFIRMACOES).delete()
    _, carrinhos = Carrinho.objects.filter(atualizado_em__lt=agora - RETENCAO_CARRINHOS).delete()
    return ecras.get('core.ConfirmacaoCarrinho', 0), carrinhos.get('core.Carrinho', 0)

async def adicionar_ao_carrinho_async(telegram_user, produto_id):
    return await sync_to_async(adicionar_ao_carrinho)(telegram_user, produto_id)

async def alterar_quantidade_async(telegram_user, produto_id, delta):
    return await sync_to_async(alterar_quantidade)(telegram_user, produto_id, delta)

async def remover_do_carrinho_async(telegram_user, produto_id):
    return await sync_to_async(remover_do_carrinho)(telegram_user, produto_id)

async def ver_carrinho_async(telegram_user):
    return await sync_to_async(ver_carrinho)(telegram_user)

async def preparar_confirmacao_carrinho_async(telegram_user):
    return await sync_to_async(preparar_confirmacao_carrinho)(telegram_user)

async def pode_repetir(encomenda, user_id) -> bool:
    return await sync_to_async(_pode_repetir)(encomenda, validar_telegram_id(user_id))

async def repetir_encomenda_async(telegram_user, encomenda_id):
    return await sync_to_async(repetir_encomenda)(telegram_user, encomenda_id)


# ── Checkout: sessão Stripe recuperável (P15; D19 reversed in Stage 2) ─────

def _impressao_urls():
    urls = f"{settings.STRIPE_SUCCESS_URL}\n{settings.STRIPE_CANCEL_URL}"
    return hashlib.sha256(urls.encode("utf-8")).hexdigest()[:12]

def chave_primeira_tentativa(encomenda):
    return f"checkout-encomenda-{encomenda.id}-{encomenda.chave_confirmacao}-u{_impressao_urls()}"

def parametros_checkout(encomenda):
    """Stripe request built only from saved order data, so a reused key always matches."""
    line_items = [{
        'price_data': {
            'currency': 'eur',
            # Legacy lines may lack the saved name; they fall back to the current one.
            'product_data': {'name': linha.nome_produto or linha.produto.nome},
            'unit_amount': _centimos(linha.preco_unitario),
        },
        'quantity': linha.quantidade,
    } for linha in encomenda.linhas.select_related('produto').order_by('pk')]
    return dict(
        payment_method_types=['card'],
        line_items=line_items,
        mode='payment',
        success_url=settings.STRIPE_SUCCESS_URL,
        cancel_url=settings.STRIPE_CANCEL_URL,
        metadata={'encomenda_id': str(encomenda.id)},
        client_reference_id=str(encomenda.id),
        expires_at=int((encomenda.data_encomenda + JANELA_PAGAMENTO).timestamp()),
    )

def _bloquear_se_pendente(encomenda):
    """Inside a transaction: lock the order row and report whether it is still pending.

    A cancellation must write this row, so it either committed before this check or
    waits until the session is recorded.
    """
    estado = Encomenda.objects.select_for_update().values_list('estado', flat=True).get(pk=encomenda.pk)
    return estado == 'pendente'

def _ainda_pendente(encomenda):
    return Encomenda.objects.filter(pk=encomenda.pk, estado='pendente').exists()

def _pagamento_de(encomenda):
    """Existing Pagamento or None; tests patch it to force the concurrent first attempt."""
    return Pagamento.objects.filter(encomenda=encomenda).first()

def _terminar_prazo(encomenda, motivo):
    """The payment window is over: cancel the order instead of creating a new session."""
    try:
        resultado, _ = cancelar_encomenda(encomenda.pk, motivo)
    except PagamentoRecebido:
        return ("a_confirmar", None)
    return ("expirada", None) if resultado == 'cancelada' else ("nao_ativa", None)

def garantir_sessao_pagamento(encomenda):
    """Return (estado, url) for the order's Stripe Checkout Session.

    estado: "aberta" (url to show), "paga", "a_confirmar" (completed, webhook pending),
    "expirada" (the payment window ended and the order is now cancelled) or "nao_ativa".
    The session id is saved, under a lock on a still-pending order, before a url is
    returned; Stripe is not called while this function holds a lock (cancelar_encomenda
    holds the order lock across its own Stripe calls, by design). Raises on Stripe or
    database errors; the caller then shows the payment-link failure screen.
    """
    encomenda.refresh_from_db(fields=['estado', 'data_encomenda'])
    if encomenda.estado != 'pendente':
        return ("nao_ativa", None)

    pagamento = _pagamento_de(encomenda)
    if pagamento is None:
        if timezone.now() - encomenda.data_encomenda > CORTE_SESSAO:
            return _terminar_prazo(encomenda, 'prazo de pagamento esgotado')
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
        # Paid or finishing: the webhook confirms it.
        return ("a_confirmar", None)
    # expired: the window is over; no replacement session (ends D19).
    return _terminar_prazo(encomenda, 'sessão expirada')

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
    linhas = list(encomenda.linhas.order_by('produto_id', 'pk'))
    _bloquear_produtos([linha.produto_id for linha in linhas])  # same id order as checkout
    for linha in linhas:
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


# ── Limpeza que reconcilia encomendas pendentes (SEC-03, cart design G12) ──

def _limite_abandono(horas):
    return timezone.now() - timedelta(hours=horas)

def encomendas_abandonadas(horas: int = 24):
    """Ids of pending orders older than `horas`, with or without a payment session."""
    return list(Encomenda.objects.filter(
        estado='pendente', data_encomenda__lt=_limite_abandono(horas),
    ).order_by('pk').values_list('id', flat=True))

def reconciliar_encomenda(encomenda_id: int, horas: int = 24):
    """Settle one old pending order; returns (resultado, encomenda).

    resultado: "cancelada" (stock returned, any open session expired), "confirmada" (the
    stored session was paid: confirmed as the webhook would), "erro" (logged, order
    unchanged, retried on the next run) or None (settled meanwhile, nothing to do).
    """
    try:
        with transaction.atomic():
            encomenda = (Encomenda.objects.select_for_update(of=('self',)).select_related('utilizador')
                         .filter(pk=encomenda_id, estado='pendente', data_encomenda__lt=_limite_abandono(horas))
                         .first())
            if encomenda is None:
                return (None, None)
            pagamento = Pagamento.objects.filter(encomenda=encomenda).first()
            if pagamento is not None and pagamento.estado_pagamento in ('pago', 'reembolsado'):
                # Can't happen through the bot: payment and order change together. Never refund here.
                logger.error("Limpeza: encomenda #%s pendente com pagamento %s", encomenda_id,
                             pagamento.estado_pagamento)
                return ("erro", encomenda)
            try:
                resultado, encomenda = cancelar_encomenda(
                    encomenda_id, f'limpeza: sem pagamento há mais de {horas} h')
            except PagamentoRecebido:  # the stored session is complete
                resultado = confirmar_pagamento(pagamento.pk, pagamento.stripe_session_id)
                encomenda.refresh_from_db(fields=['estado'])
                return (resultado, encomenda)
            return ("cancelada" if resultado == 'cancelada' else None, encomenda)
    except (stripe.error.StripeError, DatabaseError):
        logger.exception("Limpeza: encomenda #%s não reconciliada; nova tentativa na próxima execução", encomenda_id)
        return ("erro", None)


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
