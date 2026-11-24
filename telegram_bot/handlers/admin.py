"""Admin callback screens: products, stock, orders, users and audit log.

Each handler takes (query, context, data, user_id). The admin permission
check runs in telegram_bot.handlers.callbacks before any handler here.
"""
import logging

import stripe

from core.models import Utilizador, Produto, Encomenda
from core import services
from security.sanitization import sanitizar_id, sanitizar_delta_stock, EntradaInvalida
from telegram_bot.constants import ESTADO_EMOJI
from telegram_bot.notifications import notificar_cliente
from telegram_bot.ui import responder
from telegram_bot import keyboards as kb

logger = logging.getLogger(__name__)


def _texto_produto(p):
    estado = "✅ Ativo" if p.ativo else "❌ Inativo"
    return (
        f"📦 *{p.nome}*\n\n"
        f"💶 Preço: *{p.preco:.2f}€*\n"
        f"📦 Stock: *{p.stock_atual}* unidades\n"
        f"📁 Categoria: {p.categoria}\n"
        f"Estado: {estado}"
    )

def _texto_stock(p):
    return f"📊 *Stock — {p.nome}*\n\nStock atual: *{p.stock_atual}* unidades\n\nAjusta o stock:"


# ── Menu ───────────────────────────────────────────────────────────────────

async def admin_menu(query, context, data, user_id):
    await responder(query, context,
                    "🔐 *Painel Admin — Jarvis*\n\nO que pretendes gerir?",
                    kb.teclado_admin_menu())


# ── Produtos ───────────────────────────────────────────────────────────────

async def produtos(query, context, data, user_id):
    produtos = await services.get_todos_produtos()
    if not produtos:
        await responder(query, context, "📦 Sem produtos na base de dados.", kb.teclado_voltar_admin())
        return
    await responder(
        query, context,
        "📦 *Gestão de Produtos*\n✅ = ativo | ❌ = inativo\n\nEscolhe um produto:",
        kb.teclado_produtos_admin(produtos)
    )

async def produto_detalhe(query, context, data, user_id):
    try:
        p = await services.get_produto_admin(sanitizar_id(data.split("_")[-1], campo="produto_id"))
    except (EntradaInvalida, Produto.DoesNotExist, ValueError):
        await responder(query, context, "❌ Produto não encontrado.", kb.teclado_voltar_admin())
        return
    await responder(query, context, _texto_produto(p), kb.teclado_produto_acao(p))

async def produto_toggle(query, context, data, user_id):
    try:
        p = await services.get_produto_admin(sanitizar_id(data.split("_")[-1], campo="produto_id"))
    except (EntradaInvalida, Produto.DoesNotExist, ValueError):
        await responder(query, context, "❌ Produto não encontrado.", kb.teclado_voltar_admin())
        return
    await services.alternar_produto_ativo(p)
    acao = "ATIVAR_PRODUTO" if p.ativo else "DESATIVAR_PRODUTO"
    await services.registar_auditoria(user_id, acao, "Produto", f"{p.nome} (id={p.id})")
    await query.answer(f"Produto {'✅ ativado' if p.ativo else '❌ desativado'}.", show_alert=False)
    await responder(query, context, _texto_produto(p), kb.teclado_produto_acao(p))


# ── Stock ──────────────────────────────────────────────────────────────────

async def stock(query, context, data, user_id):
    produtos = await services.get_todos_produtos()
    if not produtos:
        await responder(query, context, "📦 Sem produtos na base de dados.", kb.teclado_voltar_admin())
        return
    await responder(query, context,
                    "📊 *Gestão de Stock*\nEscolhe um produto para ajustar:",
                    kb.teclado_stock_lista(produtos))

async def stock_produto(query, context, data, user_id):
    try:
        p = await services.get_produto_admin(sanitizar_id(data.split("_")[-1], campo="produto_id"))
    except (EntradaInvalida, Produto.DoesNotExist, ValueError):
        await responder(query, context, "❌ Produto não encontrado.", kb.teclado_voltar_admin())
        return
    await responder(query, context, _texto_stock(p), kb.teclado_stock_ajuste(p.id))

async def stock_ajuste(query, context, data, user_id):
    parts = data.split("_")
    try:
        produto_id = sanitizar_id(parts[-2], campo="produto_id")
        delta = sanitizar_delta_stock(parts[-1])
        p = await services.get_produto_admin(produto_id)
    except (EntradaInvalida, ValueError, Produto.DoesNotExist):
        await responder(query, context, "❌ Erro ao ajustar stock.", kb.teclado_voltar_admin())
        return
    await services.ajustar_stock_manual(p, delta)
    await services.registar_auditoria(
        user_id, "AJUSTE_STOCK", "Produto",
        f"{p.nome}: delta={delta}, novo stock={p.stock_atual}"
    )
    await query.answer(f"Stock atualizado: {p.stock_atual} unidades.", show_alert=False)
    await responder(query, context, _texto_stock(p), kb.teclado_stock_ajuste(p.id))


# ── Encomendas ─────────────────────────────────────────────────────────────

async def encomendas(query, context, data, user_id):
    encomendas = await services.get_encomendas_ativas()
    if not encomendas:
        await responder(query, context, "📋 Sem encomendas activas de momento.", kb.teclado_voltar_admin())
        return
    await responder(
        query, context,
        f"📋 *Encomendas Activas* ({len(encomendas)})\n\nEscolhe uma encomenda para gerir:",
        kb.teclado_encomendas_admin(encomendas)
    )

async def encomenda_detalhe(query, context, data, user_id):
    try:
        eid = sanitizar_id(data.split("_")[-1], campo="encomenda_id")
        encomenda = await services.get_encomenda_detalhe(eid)
    except (EntradaInvalida, Encomenda.DoesNotExist, ValueError):
        await responder(query, context, "❌ Encomenda não encontrada.", kb.teclado_voltar_admin())
        return
    emoji = ESTADO_EMOJI.get(encomenda.estado, '📦')
    await responder(
        query, context,
        f"📋 *Encomenda #{encomenda.id}*\n\n"
        f"👤 Cliente: *{encomenda.utilizador.nome}*\n"
        f"📱 Telegram ID: `{encomenda.utilizador.telegram_id}`\n"
        f"{emoji} Estado: *{encomenda.estado}*\n"
        f"💶 Total: *{encomenda.total:.2f}€*\n"
        f"📅 Data: {encomenda.data_encomenda.strftime('%d/%m/%Y %H:%M')}",
        kb.teclado_acoes_encomenda(encomenda)
    )

async def _mudar_estado_e_notificar(
    query, context, user_id: int,
    encomenda_id: int, novo_estado: str,
    label_acao: str
):
    await query.answer()  # before the DB update and the customer notification
    mensagem_cliente = novo_estado
    try:
        if novo_estado == 'cancelada':
            # SEC-06: expires an open Stripe session or refunds a paid one before cancelling.
            mensagem_cliente, encomenda = await services.cancelar_encomenda_db(encomenda_id)
            if mensagem_cliente == 'invalida':
                raise ValueError(encomenda_id)
        else:
            encomenda = await services.atualizar_estado_encomenda(encomenda_id, novo_estado)
    except services.PagamentoRecebido:
        await responder(
            query, context,
            "⚠️ O cliente acabou de pagar esta encomenda, por isso não foi cancelada. "
            "Aguarda a confirmação do pagamento e cancela de novo para a reembolsar.",
            kb.teclado_voltar_admin(), parse_mode=None
        )
        return
    except stripe.error.StripeError:
        logger.exception("Stripe falhou ao cancelar a encomenda #%s", encomenda_id)
        await responder(
            query, context,
            "❌ Não foi possível contactar a Stripe. A encomenda não foi alterada. Tenta novamente.",
            kb.teclado_voltar_admin(), parse_mode=None
        )
        return
    except (Encomenda.DoesNotExist, ValueError):
        await responder(
            query, context,
            "❌ Encomenda não encontrada ou estado inválido.",
            kb.teclado_voltar_admin(), parse_mode=None
        )
        return

    notificada = await notificar_cliente(context, encomenda, mensagem_cliente)
    await services.registar_auditoria(
        user_id,
        f"ADMIN_{label_acao}_ENCOMENDA",
        "Encomenda",
        f"#{encomenda.id} -> {novo_estado} | notificado={notificada}"
    )
    emoji = ESTADO_EMOJI.get(novo_estado, '📦')
    extra = "\n\n⚠️ _Notificação ao cliente falhou (bot bloqueado ou conta inexistente)._" if not notificada else ""
    await responder(
        query, context,
        f"{emoji} *Encomenda #{encomenda.id}* marcada como *{novo_estado}*."
        f"\n👤 Cliente: {encomenda.utilizador.nome}"
        f"\n💶 Total: {encomenda.total:.2f}€"
        f"\n📩 Notificação enviada: {'✅ sim' if notificada else '❌ não'}"
        + extra,
        kb.teclado_voltar_admin()
    )

def _mudar_estado(novo_estado: str, label_acao: str):
    """Build the handler for one admin_enc_<acao>_<id> button."""
    async def handler(query, context, data, user_id):
        try:
            eid = sanitizar_id(data.split("_")[-1], campo="encomenda_id")
        except EntradaInvalida:
            await query.answer("❌ ID inválido.", show_alert=True)
            return
        await _mudar_estado_e_notificar(query, context, user_id, eid, novo_estado, label_acao)
    return handler


# ── Utilizadores ───────────────────────────────────────────────────────────

async def utilizadores(query, context, data, user_id):
    utilizadores = await services.get_todos_utilizadores()
    if not utilizadores:
        await responder(query, context, "👥 Sem utilizadores registados.", kb.teclado_voltar_admin())
        return
    await responder(
        query, context,
        "👥 *Gestão de Utilizadores*\n✅ = ativo | 🚫 = bloqueado\n\nEscolhe um utilizador:",
        kb.teclado_utilizadores_admin(utilizadores)
    )

async def utilizador_toggle(query, context, data, user_id):
    tid = data[len("admin_utilizador_"):]
    try:
        u, novo_ativo = await services.toggle_utilizador_ativo(tid)
    except Utilizador.DoesNotExist:
        await responder(query, context, "❌ Utilizador não encontrado.", kb.teclado_voltar_admin())
        return
    acao = "DESBLOQUEAR_UTILIZADOR" if novo_ativo else "BLOQUEAR_UTILIZADOR"
    await services.registar_auditoria(user_id, acao, "Utilizador", f"{u.nome} (telegram_id={tid})")
    estado_label = "✅ desbloqueado" if novo_ativo else "🚫 bloqueado"
    await responder(
        query, context,
        f"👤 *{u.nome}*\n\nO utilizador foi {estado_label}.",
        kb.teclado_voltar_admin(), parse_mode=None
    )


# ── Auditoria ──────────────────────────────────────────────────────────────

async def auditoria(query, context, data, user_id):
    registos = await services.get_ultimos_registos_auditoria(10)
    if not registos:
        await responder(query, context, "📜 Sem registos de auditoria.", kb.teclado_voltar_admin())
        return
    linhas = [
        f"🔹 `{r.data_hora.strftime('%d/%m %H:%M')}` — *{r.acao}* — {r.entidade}"
        for r in registos
    ]
    await responder(
        query, context,
        "📜 *Últimos Registos de Auditoria*\n\n" + "\n".join(linhas),
        kb.teclado_voltar_admin()
    )


EXACT_ROUTES = {
    "admin_menu": admin_menu,
    "admin_produtos": produtos,
    "admin_stock": stock,
    "admin_encomendas": encomendas,
    "admin_utilizadores": utilizadores,
    "admin_auditoria": auditoria,
}

PREFIX_ROUTES = (
    ("admin_produto_detalhe_", produto_detalhe),
    ("admin_produto_toggle_", produto_toggle),
    ("admin_stock_produto_", stock_produto),
    ("admin_stock_adj_", stock_ajuste),
    ("admin_ver_encomenda_", encomenda_detalhe),
    ("admin_enc_confirmar_", _mudar_estado('confirmada', 'CONFIRMAR')),
    ("admin_enc_enviada_", _mudar_estado('enviada', 'ENVIAR')),
    ("admin_enc_entregue_", _mudar_estado('entregue', 'ENTREGAR')),
    ("admin_enc_cancelar_", _mudar_estado('cancelada', 'CANCELAR')),
    ("admin_utilizador_", utilizador_toggle),
)
