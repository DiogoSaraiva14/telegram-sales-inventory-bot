"""Customer callback screens: menu, catalog, product, orders, checkout and help.

Each handler takes (query, context, data, user_id) and is selected by
telegram_bot.handlers.callbacks through EXACT_ROUTES / PREFIX_ROUTES.
"""
import logging

from core.models import Produto, Encomenda
from core import services
from security.sanitization import sanitizar_id, EntradaInvalida
from telegram_bot.constants import CATEGORIAS, ESTADO_EMOJI
from telegram_bot.ui import responder
from telegram_bot import keyboards as kb

logger = logging.getLogger(__name__)


async def menu_inicio(query, context, data, user_id):
    await responder(query, context, "O que pretendes fazer?",
                    kb.teclado_menu_principal(user_id), parse_mode=None)


async def menu_catalogo(query, context, data, user_id):
    await responder(query, context, "🛍️ *Catálogo*\nEscolhe uma categoria:",
                    kb.teclado_catalogo())


async def categoria(query, context, data, user_id):
    categoria = data[4:]
    nome_cat = CATEGORIAS.get(categoria, "Categoria")
    produtos = await services.get_produtos_por_categoria(categoria)
    if not produtos:
        await responder(query, context,
                        f"😔 Sem produtos disponíveis em *{nome_cat}*.",
                        kb.teclado_voltar_catalogo())
        return
    await responder(query, context, f"{nome_cat}\nEscolhe um produto:",
                    kb.teclado_produtos_categoria(produtos), parse_mode=None)


async def produto_detalhe(query, context, data, user_id):
    try:
        p = await services.get_produto(sanitizar_id(data.split("_")[1], campo="produto_id"))
    except (EntradaInvalida, Produto.DoesNotExist, ValueError):
        await responder(query, context, "❌ Produto não encontrado.",
                        kb.teclado_voltar_menu(), parse_mode=None)
        return
    descricao = f"\n_{p.descricao}_" if p.descricao else ""
    stock_aviso = "\n⚠️ _Último em stock!_" if p.stock_atual == 1 else ""
    caption = (
        f"📦 *{p.nome}*\n\n"
        f"💶 Preço: *{p.preco:.2f}€*\n"
        f"📦 Stock: {p.stock_atual} unidades"
        f"{descricao}{stock_aviso}"
    )
    keyboard = kb.teclado_produto(p)
    await query.answer()  # before the photo upload
    if p.imagem:
        if query.message.photo:
            await query.message.edit_caption(
                caption=caption, parse_mode="Markdown", reply_markup=keyboard)
        else:
            await query.message.delete()
            with open(p.imagem.path, 'rb') as foto:
                await context.bot.send_photo(
                    chat_id=query.message.chat_id,
                    photo=foto,
                    caption=caption,
                    parse_mode="Markdown",
                    reply_markup=keyboard
                )
    else:
        await responder(query, context, caption, keyboard)


async def sem_stock(query, context, data, user_id):
    await query.answer("❌ Este produto não tem stock disponível.", show_alert=True)


async def menu_encomendas(query, context, data, user_id):
    encomendas = await services.get_encomendas_utilizador(user_id)
    if not encomendas:
        await responder(query, context, "📦 Ainda não tens encomendas.",
                        kb.teclado_voltar_menu(), parse_mode=None)
        return
    await responder(query, context,
                    "🛒 *As Minhas Encomendas*\n\nEscolhe uma encomenda para ver detalhes:",
                    kb.teclado_minhas_encomendas(encomendas))


async def encomenda_detalhe(query, context, data, user_id):
    # Unreachable today: validar_callback rejects user_encomenda_ (SECURITY.md, SEC-12).
    try:
        eid = sanitizar_id(data.split("_")[-1], campo="encomenda_id")
        encomenda = await services.get_encomenda_detalhe(eid)
        if str(encomenda.utilizador.telegram_id) != str(user_id):
            await query.answer("⛔ Acesso negado.", show_alert=True)
            return
    except (EntradaInvalida, Encomenda.DoesNotExist, ValueError):
        await responder(query, context, "❌ Encomenda não encontrada.",
                        kb.teclado_voltar_encomendas(), parse_mode=None)
        return
    emoji = ESTADO_EMOJI.get(encomenda.estado, "📦")
    linhas_enc = await services.get_linhas_encomenda(encomenda)
    produtos_txt = "\n".join(
        f"  • {l.produto.nome} x{l.quantidade} — {l.subtotal:.2f}€"
        for l in linhas_enc
    )
    await responder(
        query, context,
        f"📋 *Encomenda #{encomenda.id}*\n\n"
        f"{emoji} Estado: *{encomenda.estado}*\n"
        f"💶 Total: *{encomenda.total:.2f}€*\n"
        f"📅 Data: {encomenda.data_encomenda.strftime('%d/%m/%Y %H:%M')}\n\n"
        f"*Produtos:*\n{produtos_txt}",
        kb.teclado_voltar_encomendas()
    )


async def encomendar(query, context, data, user_id):
    try:
        p = await services.get_produto(sanitizar_id(data.split("_")[1], campo="produto_id"))
    except (EntradaInvalida, Produto.DoesNotExist, ValueError):
        await responder(query, context, "❌ Produto não encontrado.",
                        kb.teclado_voltar_menu(), parse_mode=None)
        return
    if p.stock_atual <= 0:
        await query.answer("❌ Produto sem stock disponível.", show_alert=True)
        return
    await responder(
        query, context,
        f"🛒 *Confirmar Encomenda*\n\n"
        f"Produto: *{p.nome}*\n"
        f"Preço: *{p.preco:.2f}€*\n\n"
        f"Desejas confirmar esta encomenda?",
        kb.teclado_confirmar_encomenda(p)
    )


async def confirmar_encomenda(query, context, data, user_id):
    try:
        p = await services.get_produto(sanitizar_id(data.split("_")[-1], campo="produto_id"))
    except (EntradaInvalida, Produto.DoesNotExist, ValueError):
        await responder(query, context, "❌ Produto não encontrado.",
                        kb.teclado_voltar_menu(), parse_mode=None)
        return
    if p.stock_atual <= 0:
        await responder(query, context,
                        "😔 Lamentamos, este produto ficou sem stock entretanto.",
                        kb.teclado_voltar_catalogo(), parse_mode=None)
        return
    await query.answer()  # stop the button spinner before the Stripe request
    utilizador, _ = await services.get_or_create_utilizador(query.from_user)
    encomenda = await services.criar_encomenda(utilizador, p)
    await services.registar_auditoria(
        user_id, "CRIAR_ENCOMENDA", "Encomenda",
        f"#{encomenda.id} — {p.nome} — {p.preco:.2f}€"
    )
    try:
        payment_url = await services.criar_sessao_stripe(encomenda, p)
        await responder(
            query, context,
            f"✅ *Encomenda #{encomenda.id} criada!*\n\n"
            f"Produto: *{p.nome}*\n"
            f"Total: *{encomenda.total:.2f}€*\n\n"
            f"Clica no botão abaixo para completar o pagamento.\n"
            f"A encomenda é confirmada automaticamente após o pagamento. 💳",
            kb.teclado_pagamento(payment_url)
        )
    except Exception as exc:
        logger.error("Erro ao criar sessão Stripe: %s", exc)
        await responder(
            query, context,
            f"✅ *Encomenda #{encomenda.id} criada!*\n\n"
            f"⚠️ Não foi possível gerar o link de pagamento.\n"
            f"Contacta o suporte.",
            kb.teclado_ver_encomendas()
        )


async def menu_ajuda(query, context, data, user_id):
    await responder(
        query, context,
        "ℹ️ *Ajuda*\n\nNavega usando os botões apresentados.\n"
        "Usa /start a qualquer momento para voltar ao menu principal.",
        kb.teclado_voltar_menu()
    )


EXACT_ROUTES = {
    "menu_inicio": menu_inicio,
    "menu_catalogo": menu_catalogo,
    "sem_stock": sem_stock,
    "menu_encomendas": menu_encomendas,
    "menu_ajuda": menu_ajuda,
}

PREFIX_ROUTES = (
    ("cat_", categoria),
    ("produto_", produto_detalhe),
    ("user_encomenda_", encomenda_detalhe),
    ("encomendar_", encomendar),
    ("confirmar_encomenda_", confirmar_encomenda),
)
