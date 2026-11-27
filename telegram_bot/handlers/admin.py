"""Admin callback screens: products, stock, orders, users and audit log.

Each handler takes (query, context, data, user_id). The admin permission
check runs in telegram_bot.handlers.callbacks before any handler here.
Screens follow the admin's own bot language; stored data (names, audit
descriptions) is shown as it was written.
"""
import logging

import stripe
from asgiref.sync import sync_to_async

from core.models import Utilizador, Produto, Encomenda
from core import services
from core.notificacoes import enviar_da_encomenda
from security.sanitization import sanitizar_id, sanitizar_delta_stock, EntradaInvalida
from django.utils import timezone

from telegram_bot.constants import ESTADO_EMOJI, ROTULO_AUDITORIA
from telegram_bot.traducoes import estado, tr
from telegram_bot.ui import idioma, md, responder
from telegram_bot import keyboards as kb

logger = logging.getLogger(__name__)


def _texto_produto(p, i="pt"):
    return tr(i, "📦 *{nome}*\n\n💶 Preço: *{preco:.2f}€*\n📦 Stock: *{stock}* unidades\n"
                 "📁 Categoria: {categoria}\nEstado: {estado}").format(
        nome=md(p.nome, '*'), preco=p.preco, stock=p.stock_atual, categoria=md(p.categoria.nome),
        estado=tr(i, "✅ Ativo") if p.ativo else tr(i, "❌ Inativo"))

def _texto_stock(p, i="pt"):
    return tr(i, "📊 *Stock — {nome}*\n\nStock atual: *{stock}* unidades\n\nAjusta o stock:").format(
        nome=md(p.nome, '*'), stock=p.stock_atual)


def _numero_pagina(data, prefixo):
    """Page number from <prefixo><n>; 0 for the plain list button or anything malformed."""
    valor = data[len(prefixo):] if data.startswith(prefixo) else "0"
    return int(valor) if valor.isdecimal() and len(valor) <= 4 else 0


async def _lista(query, context, data, prefixo, obter, teclado, titulo, legenda, vazio):
    """One page of an admin list; a page past the end shows the first page."""
    i = await idioma(context, query.from_user)
    pagina = _numero_pagina(data, prefixo)
    linhas, ha_mais = await obter(pagina)
    if not linhas and pagina:
        pagina = 0
        linhas, ha_mais = await obter(0)
    if not linhas:
        await responder(query, context, tr(i, vazio), kb.teclado_voltar_admin(i))
        return
    titulo = tr(i, titulo)
    if pagina or ha_mais:
        titulo += tr(i, " · página {n}").format(n=pagina + 1)
    await responder(query, context, f"{titulo}\n{tr(i, legenda)}", teclado(linhas, pagina, ha_mais, i))


# ── Menu ───────────────────────────────────────────────────────────────────

def titulo_admin(loja, i="pt"):
    return tr(i, "🔐 *Painel Admin — {loja}*\n\nO que pretendes gerir?").format(loja=md(loja.nome, '*'))


async def admin_menu(query, context, data, user_id):
    i = await idioma(context, query.from_user)
    await responder(query, context, titulo_admin(await services.obter_loja_async(), i), kb.teclado_admin_menu(i))


# ── Produtos ───────────────────────────────────────────────────────────────

async def produtos(query, context, data, user_id):
    await _lista(query, context, data, "admin_produtos_p_", services.get_todos_produtos, kb.teclado_produtos_admin,
                 "📦 *Gestão de Produtos*", "✅ = ativo | ❌ = inativo\n\nEscolhe um produto:",
                 "📦 Sem produtos na base de dados.")

async def produto_detalhe(query, context, data, user_id):
    i = await idioma(context, query.from_user)
    try:
        p = await services.get_produto_admin(sanitizar_id(data.split("_")[-1], campo="produto_id"))
    except (EntradaInvalida, Produto.DoesNotExist, ValueError):
        await responder(query, context, tr(i, "❌ Produto não encontrado."), kb.teclado_voltar_admin(i))
        return
    await responder(query, context, _texto_produto(p, i), kb.teclado_produto_acao(p, i))

async def produto_toggle(query, context, data, user_id):
    i = await idioma(context, query.from_user)
    try:
        p = await services.alternar_produto_ativo_async(sanitizar_id(data.split("_")[-1], campo="produto_id"), user_id)
    except (EntradaInvalida, Produto.DoesNotExist, ValueError):
        await responder(query, context, tr(i, "❌ Produto não encontrado."), kb.teclado_voltar_admin(i))
        return
    await query.answer(tr(i, "Produto ✅ ativado.") if p.ativo else tr(i, "Produto ❌ desativado."), show_alert=False)
    await responder(query, context, _texto_produto(p, i), kb.teclado_produto_acao(p, i))


# ── Stock ──────────────────────────────────────────────────────────────────

async def stock(query, context, data, user_id):
    await _lista(query, context, data, "admin_stock_p_", services.get_todos_produtos, kb.teclado_stock_lista,
                 "📊 *Gestão de Stock*", "Escolhe um produto para ajustar:",
                 "📦 Sem produtos na base de dados.")

async def stock_produto(query, context, data, user_id):
    i = await idioma(context, query.from_user)
    try:
        p = await services.get_produto_admin(sanitizar_id(data.split("_")[-1], campo="produto_id"))
    except (EntradaInvalida, Produto.DoesNotExist, ValueError):
        await responder(query, context, tr(i, "❌ Produto não encontrado."), kb.teclado_voltar_admin(i))
        return
    await responder(query, context, _texto_stock(p, i), kb.teclado_stock_ajuste(p.id, i))

async def stock_ajuste(query, context, data, user_id):
    i = await idioma(context, query.from_user)
    parts = data.split("_")
    try:
        produto_id = sanitizar_id(parts[-2], campo="produto_id")
        delta = sanitizar_delta_stock(parts[-1])
        p = await services.ajustar_stock_admin_async(produto_id, delta, user_id)
    except (EntradaInvalida, ValueError, Produto.DoesNotExist):
        await responder(query, context, tr(i, "❌ Erro ao ajustar stock."), kb.teclado_voltar_admin(i))
        return
    await query.answer(tr(i, "Stock atualizado: {stock} unidades.").format(stock=p.stock_atual), show_alert=False)
    await responder(query, context, _texto_stock(p, i), kb.teclado_stock_ajuste(p.id, i))


# ── Encomendas ─────────────────────────────────────────────────────────────

async def encomendas(query, context, data, user_id):
    await _lista(query, context, data, "admin_encomendas_p_", services.get_encomendas_ativas,
                 kb.teclado_encomendas_admin, "📋 *Encomendas Activas*",
                 "Mais antigas primeiro.\n\nEscolhe uma encomenda para gerir:",
                 "📋 Sem encomendas activas de momento.")

async def encomenda_detalhe(query, context, data, user_id):
    i = await idioma(context, query.from_user)
    try:
        eid = sanitizar_id(data.split("_")[-1], campo="encomenda_id")
        encomenda = await services.get_encomenda_detalhe(eid)
    except (EntradaInvalida, Encomenda.DoesNotExist, ValueError):
        await responder(query, context, tr(i, "❌ Encomenda não encontrada."), kb.teclado_voltar_admin(i))
        return
    emoji = ESTADO_EMOJI.get(encomenda.estado, '📦')
    linhas = await services.get_linhas_encomenda(encomenda)  # what to pack and ship
    texto_linhas = "\n".join(
        f"• {l.quantidade}× {md(l.nome_produto or l.produto.nome)} — {l.preco_unitario * l.quantidade:.2f}€"
        for l in linhas
    )
    await responder(
        query, context,
        tr(i, "📋 *Encomenda #{id}*\n\n👤 Cliente: *{cliente}*\n📱 Telegram ID: `{telegram_id}`\n"
              "{emoji} Estado: *{estado}*\n📅 Data: {data}\n\n{linhas}\n\n💶 Total: *{total:.2f}€*").format(
            id=encomenda.id, cliente=md(encomenda.utilizador.nome, '*'),
            telegram_id=encomenda.utilizador.telegram_id, emoji=emoji, estado=estado(i, encomenda.estado),
            data=timezone.localtime(encomenda.data_encomenda).strftime('%d/%m/%Y %H:%M'),
            linhas=texto_linhas, total=encomenda.total),
        kb.teclado_acoes_encomenda(encomenda, i)
    )

async def encomenda_pedir_cancelamento(query, context, data, user_id):
    """Ask before cancelling: a paid order is refunded in full, and that can't be undone."""
    i = await idioma(context, query.from_user)
    try:
        eid = sanitizar_id(data.split("_")[-1], campo="encomenda_id")
        encomenda = await services.get_encomenda_detalhe(eid)
    except (EntradaInvalida, Encomenda.DoesNotExist, ValueError):
        await responder(query, context, tr(i, "❌ Encomenda não encontrada."), kb.teclado_voltar_admin(i))
        return
    aviso = (tr(i, "O pagamento é reembolsado na totalidade e o stock volta ao inventário.")
             if encomenda.estado == 'confirmada' else tr(i, "O stock volta ao inventário."))
    await responder(
        query, context,
        tr(i, "⚠️ Cancelar a encomenda #{id} ({total:.2f}€)?\n\n{aviso} "
              "O cliente recebe uma mensagem. Não é possível desfazer.").format(
            id=encomenda.id, total=encomenda.total, aviso=aviso),
        kb.teclado_confirmar_cancelamento(encomenda.id, i), parse_mode=None,
    )

async def _mudar_estado_e_notificar(
    query, context, user_id: int,
    encomenda_id: int, novo_estado: str,
    label_acao: str
):
    await query.answer()  # before the DB update and the customer notification
    i = await idioma(context, query.from_user)
    mensagem_cliente = novo_estado
    try:
        if novo_estado == 'cancelada':
            # SEC-06: expires an open Stripe session or refunds a paid one before cancelling.
            mensagem_cliente, encomenda = await services.cancelar_encomenda_db(encomenda_id)
            if mensagem_cliente == 'invalida':
                raise ValueError(encomenda_id)
        else:
            encomenda = await services.atualizar_estado_encomenda(encomenda_id, novo_estado)
            if encomenda is None:  # not the required previous state (SEC-10)
                raise ValueError(encomenda_id)
    except services.PagamentoRecebido:
        await responder(
            query, context,
            tr(i, "⚠️ O cliente acabou de pagar esta encomenda, por isso não foi cancelada. "
                  "Aguarda a confirmação do pagamento e cancela de novo para a reembolsar."),
            kb.teclado_voltar_admin(i), parse_mode=None
        )
        return
    except stripe.error.StripeError:
        logger.exception("Stripe falhou ao cancelar a encomenda #%s", encomenda_id)
        await responder(
            query, context,
            tr(i, "❌ Não foi possível contactar a Stripe. A encomenda não foi alterada. Tenta novamente."),
            kb.teclado_voltar_admin(i), parse_mode=None
        )
        return
    except (Encomenda.DoesNotExist, ValueError):
        await responder(
            query, context,
            tr(i, "❌ Encomenda não encontrada ou estado inválido."),
            kb.teclado_voltar_admin(i), parse_mode=None
        )
        return

    notificada = await sync_to_async(enviar_da_encomenda)(encomenda.id)  # queued by the service
    await services.registar_auditoria(
        user_id,
        f"ADMIN_{label_acao}_ENCOMENDA",
        "Encomenda",
        f"#{encomenda.id} -> {novo_estado} | notificado={notificada}"
    )
    emoji = ESTADO_EMOJI.get(novo_estado, '📦')
    extra = tr(i, "\n\n⚠️ _A notificação ainda não chegou ao cliente. É reenviada automaticamente durante 24 h; "
                  "se falhar de vez, fica no registo de auditoria._") if not notificada else ""
    await responder(
        query, context,
        tr(i, "{emoji} *Encomenda #{id}* marcada como *{estado}*.\n👤 Cliente: {cliente}\n💶 Total: {total:.2f}€"
              "\n📩 Notificação enviada: {enviada}").format(
            emoji=emoji, id=encomenda.id, estado=estado(i, novo_estado), cliente=md(encomenda.utilizador.nome),
            total=encomenda.total, enviada=tr(i, "✅ sim") if notificada else tr(i, "⏳ em fila"))
        + extra,
        kb.teclado_voltar_encomendas_admin(i)
    )

def _mudar_estado(novo_estado: str, label_acao: str):
    """Build the handler for one admin_enc_<acao>_<id> button."""
    async def handler(query, context, data, user_id):
        try:
            eid = sanitizar_id(data.split("_")[-1], campo="encomenda_id")
        except EntradaInvalida:
            await query.answer(tr(await idioma(context, query.from_user), "❌ ID inválido."), show_alert=True)
            return
        await _mudar_estado_e_notificar(query, context, user_id, eid, novo_estado, label_acao)
    return handler


# ── Utilizadores ───────────────────────────────────────────────────────────

async def utilizadores(query, context, data, user_id):
    await _lista(query, context, data, "admin_utilizadores_p_", services.get_todos_utilizadores,
                 kb.teclado_utilizadores_admin, "👥 *Gestão de Utilizadores*",
                 "✅ = ativo | 🚫 = bloqueado\n\nEscolhe um utilizador:",
                 "👥 Sem utilizadores registados.")

async def utilizador_toggle(query, context, data, user_id):
    i = await idioma(context, query.from_user)
    tid = data[len("admin_utilizador_"):]
    # Blocking yourself would lock you out, and blocking an admin silently removes their rights.
    if tid == str(user_id) or (tid.isdecimal() and await services.e_admin(tid)):
        await responder(
            query, context,
            tr(i, "⚠️ Não é possível bloquear um administrador (nem a ti próprio) a partir do Telegram. "
                  "Retira primeiro os direitos de administrador no Django admin."),
            kb.teclado_voltar_utilizadores(i), parse_mode=None,
        )
        return
    try:
        u, novo_ativo = await services.alternar_utilizador_ativo_async(tid, user_id)
    except Utilizador.DoesNotExist:
        await responder(query, context, tr(i, "❌ Utilizador não encontrado."), kb.teclado_voltar_admin(i))
        return
    texto = tr(i, "👤 {nome}\n\nO utilizador foi ✅ desbloqueado.") if novo_ativo else \
        tr(i, "👤 {nome}\n\nO utilizador foi 🚫 bloqueado.")
    # Plain text: no Markdown, no escaping.
    await responder(query, context, texto.format(nome=u.nome), kb.teclado_voltar_utilizadores(i), parse_mode=None)


# ── Auditoria ──────────────────────────────────────────────────────────────

def _texto_registo(r, i="pt"):
    """One audit entry: when (shop time), what happened, to whom, and the details as stored."""
    quando = timezone.localtime(r.data_hora).strftime('%d/%m/%Y %H:%M')
    rotulo = tr(i, ROTULO_AUDITORIA[r.acao]) if r.acao in ROTULO_AUDITORIA else r.acao
    linhas = [f"{quando} · {rotulo}"]
    if r.utilizador_id:
        linhas.append(f"👤 {r.utilizador.nome}")
    if r.descricao:
        linhas.append(r.descricao if len(r.descricao) <= 200 else r.descricao[:199] + "…")
    return "\n".join(linhas)


async def auditoria(query, context, data, user_id):
    """Newest first, 10 per page; admin_auditoria_p_<n> opens page n (0 is the newest)."""
    i = await idioma(context, query.from_user)
    pagina = _numero_pagina(data, "admin_auditoria_p_")
    registos, ha_mais_antigos = await services.get_registos_auditoria(pagina)
    if not registos:
        texto = tr(i, "📜 Sem registos de auditoria.") if pagina == 0 else tr(i, "📜 Não há registos mais antigos.")
        await responder(query, context, texto, kb.teclado_auditoria(0, False, i), parse_mode=None)
        return
    # Plain text: names and descriptions need no Markdown escaping.
    await responder(
        query, context,
        tr(i, "📜 Registos de auditoria · página {n}\n\n").format(n=pagina + 1)
        + "\n\n".join(_texto_registo(r, i) for r in registos),
        kb.teclado_auditoria(pagina, ha_mais_antigos, i), parse_mode=None,
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
    ("admin_enc_enviada_", _mudar_estado('enviada', 'ENVIAR')),
    ("admin_enc_entregue_", _mudar_estado('entregue', 'ENTREGAR')),
    ("admin_enc_cancelar_", _mudar_estado('cancelada', 'CANCELAR')),
    ("admin_enc_pedir_cancelar_", encomenda_pedir_cancelamento),
    ("admin_utilizador_", utilizador_toggle),
    ("admin_auditoria_p_", auditoria),
    ("admin_produtos_p_", produtos),
    ("admin_stock_p_", stock),
    ("admin_encomendas_p_", encomendas),
    ("admin_utilizadores_p_", utilizadores),
)
