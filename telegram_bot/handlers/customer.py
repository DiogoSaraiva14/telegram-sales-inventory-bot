"""Customer callback screens: menu, catalog, product, orders, checkout and help.

Each handler takes (query, context, data, user_id) and is selected by
telegram_bot.handlers.callbacks through EXACT_ROUTES / PREFIX_ROUTES.
"""
import logging

from telegram.error import BadRequest, TelegramError

from core.models import Produto, Encomenda
from core import services
from security.sanitization import sanitizar_id, EntradaInvalida
from telegram_bot import confirmacao
from telegram_bot.constants import (
    ESTADO_EMOJI, MENSAGEM_ALTERADO, MENSAGEM_ECRA_EXPIRADO, MENSAGEM_ENCOMENDA_PENDENTE,
    MENSAGEM_FALHA_REGISTO, MENSAGEM_JA_REGISTADA, MENSAGEM_MENU_EXPIRADO,
    MENSAGEM_PAGAMENTO_A_CONFIRMAR, MENSAGEM_PEDIDO_INVALIDO, MENSAGEM_PRAZO_TERMINADO,
    ROTULO_ALTERACAO,
)
from telegram_bot.ui import md, responder
from telegram_bot import keyboards as kb

logger = logging.getLogger(__name__)


async def menu_inicio(query, context, data, user_id):
    await responder(query, context, "O que pretendes fazer?",
                    kb.teclado_menu_principal(user_id), parse_mode=None)


async def menu_catalogo(query, context, data, user_id):
    await responder(query, context, "🛍️ *Catálogo*\nEscolhe uma categoria:",
                    kb.teclado_catalogo(await services.get_categorias_ativas()))


async def categoria(query, context, data, user_id):
    valor = data[4:]
    # Old code buttons (cat_ssd) and deactivated categories get a clear message, not an error.
    cat = await services.get_categoria_ativa(int(valor)) if valor.isdecimal() and valor[0] != "0" else None
    if cat is None:
        await responder(query, context, MENSAGEM_MENU_EXPIRADO, kb.teclado_voltar_catalogo(), parse_mode=None)
        return
    produtos = await services.get_produtos_por_categoria(cat.id)
    if not produtos:
        await responder(query, context,
                        f"😔 Sem produtos disponíveis em *{md(cat.nome, '*')}*.",
                        kb.teclado_voltar_catalogo())
        return
    await responder(query, context, f"{cat.nome}\nEscolhe um produto:",
                    kb.teclado_produtos_categoria(produtos), parse_mode=None)


async def produto_detalhe(query, context, data, user_id):
    try:
        p = await services.get_produto(sanitizar_id(data.split("_")[1], campo="produto_id"))
    except (EntradaInvalida, Produto.DoesNotExist, ValueError):
        await responder(query, context, "❌ Produto não encontrado.",
                        kb.teclado_voltar_menu(), parse_mode=None)
        return
    descricao = f"\n_{md(p.descricao, '_')}_" if p.descricao else ""
    stock_aviso = "\n⚠️ _Último em stock!_" if p.stock_atual == 1 else ""
    caption = (
        f"📦 *{md(p.nome, '*')}*\n\n"
        f"💶 Preço: *{p.preco:.2f}€*\n"
        f"📦 Stock: {p.stock_atual} unidades"
        f"{descricao}{stock_aviso}"
    )
    keyboard = kb.teclado_produto(p)
    await query.answer()  # before the photo upload
    if not p.imagem:
        await responder(query, context, caption, keyboard)
        return
    if query.message.photo:
        await query.message.edit_caption(
            caption=caption, parse_mode="Markdown", reply_markup=keyboard)
        return
    # Send the photo before deleting the old screen, so a failure still leaves a usable message.
    try:
        with p.imagem.open('rb') as foto:  # through the storage API (SEC-11)
            await context.bot.send_photo(
                chat_id=query.message.chat_id,
                photo=foto,
                caption=caption,
                parse_mode="Markdown",
                reply_markup=keyboard
            )
    except (OSError, TelegramError):
        # Includes TimedOut, where the photo may still have arrived (owner choice D2).
        logger.warning("Imagem indisponível: produto_id=%s", p.id, exc_info=True)
        await responder(query, context, caption + "\n(Imagem indisponível)", keyboard)
        return
    try:
        await query.message.delete()
    except TelegramError:
        logger.debug("Mensagem anterior não apagada: produto_id=%s", p.id)


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
        f"  • {md(l.produto.nome)} x{l.quantidade} — {l.subtotal:.2f}€"
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
    """Stage 2 one-product checkout: save the screen as a one-line snapshot, then show it."""
    try:
        p = await services.get_produto(sanitizar_id(data.split("_")[1], campo="produto_id"))
    except (EntradaInvalida, Produto.DoesNotExist, ValueError):
        await responder(query, context, "❌ Produto não encontrado.",
                        kb.teclado_voltar_menu(), parse_mode=None)
        return
    if p.stock_atual <= 0:
        await query.answer("❌ Produto sem stock disponível.", show_alert=True)
        return
    resultado, snap = await services.criar_confirmacao_async(query.from_user, [(p.id, 1)])
    if resultado == "bloqueado":  # sold out or deactivated since the product screen was drawn
        await _mostrar_alterado(query, context, snap, f"produto_{p.id}")
        return
    linha = snap.linhas[0]
    await responder(
        query, context,
        f"🛒 *Confirmar Encomenda*\n\n"
        f"Produto: *{md(linha['nome'], '*')}*\n"
        f"Preço: *{services._euros(linha['preco_unitario_centimos']):.2f}€*\n\n"
        f"Desejas confirmar esta encomenda?",
        kb.teclado_confirmar_encomenda(confirmacao.callback_confirmar(snap.nonce), f"produto_{p.id}")
    )


async def _mostrar(query, context, texto, teclado, parse_mode="Markdown"):
    """responder(), treating Telegram's "message is not modified" as success."""
    try:
        await responder(query, context, texto, teclado, parse_mode=parse_mode)
    except BadRequest as exc:
        if "not modified" not in str(exc).lower():
            raise


async def _mostrar_alterado(query, context, alteracoes, voltar):
    """Confirm found a difference: nothing was ordered; list what changed (plain text)."""
    linhas = [MENSAGEM_ALTERADO, ""]
    for a in alteracoes:
        rotulo = ROTULO_ALTERACAO[a.tipo].format(antes=a.antes, depois=a.depois)
        linhas.append(f"• {a.nome[:60] or 'Produto'}: {rotulo}")
    await _mostrar(query, context, "\n".join(linhas), kb.teclado_alterado(voltar), parse_mode=None)


def _texto_linhas(linhas):
    return "\n".join(
        f"• {l.quantidade}× {md((l.nome_produto or l.produto.nome)[:60])} — {l.subtotal:.2f}€" for l in linhas
    )


async def _mostrar_pagamento(query, context, data, encomenda):
    """Recover or create the order's Stripe session and draw the matching screen (§3.9)."""
    try:
        estado, url = await services.garantir_sessao_pagamento_async(encomenda)
    except Exception:
        logger.exception("Falha ao preparar pagamento: encomenda=#%s", encomenda.id)
        await _mostrar(
            query, context,
            f"✅ *Encomenda #{encomenda.id} criada!*\n\n"
            f"⚠️ Não foi possível gerar o link de pagamento.\n"
            f"Contacta o suporte.",
            kb.teclado_falha_pagamento(data)
        )
        return
    if estado == "aberta":
        linhas = await services.get_linhas_encomenda(encomenda)
        await _mostrar(
            query, context,
            f"✅ *Encomenda #{encomenda.id} criada!*\n\n"
            f"{_texto_linhas(linhas)}\n\n"
            f"Total: *{encomenda.total:.2f}€*\n\n"
            f"Clica no botão abaixo para completar o pagamento.\n"
            f"A encomenda é confirmada automaticamente após o pagamento. 💳",
            kb.teclado_pagamento(url)
        )
    elif estado == "a_confirmar":
        await _mostrar(query, context, MENSAGEM_PAGAMENTO_A_CONFIRMAR.format(id=encomenda.id),
                       kb.teclado_ver_encomendas(), parse_mode=None)
    elif estado == "expirada":
        await _mostrar(query, context, MENSAGEM_PRAZO_TERMINADO,
                       kb.teclado_ver_encomenda(encomenda.id), parse_mode=None)
    # "paga" / "nao_ativa": the alert already said it; leave the message as it is.


async def _mostrar_invalido(query, context):
    await _mostrar(query, context, MENSAGEM_PEDIDO_INVALIDO, kb.teclado_voltar_menu(), parse_mode=None)


async def confirmar_encomenda(query, context, data, user_id):
    tipo, nonce = confirmacao.ler_callback(data)
    if tipo == "invalido":  # includes the pre-token confirmar_encomenda_<produto_id> buttons
        await _mostrar(query, context, MENSAGEM_ECRA_EXPIRADO, kb.teclado_ecra_expirado(), parse_mode=None)
        return
    if tipo == "antigo":  # a button from before Stage 2: it can only reopen its existing order
        existente = await services.get_encomenda_por_chave(nonce)
        if existente is None:
            await _mostrar(query, context, MENSAGEM_ECRA_EXPIRADO, kb.teclado_ecra_expirado(), parse_mode=None)
        elif str(existente.utilizador.telegram_id) != str(user_id):
            await query.answer(MENSAGEM_PEDIDO_INVALIDO, show_alert=True)
        else:
            await query.answer(MENSAGEM_JA_REGISTADA, show_alert=True)
            await _mostrar_pagamento(query, context, data, existente)
        return

    try:
        resultado, valor = await services.criar_encomenda_de_confirmacao_async(query.from_user, nonce)
    except Exception:
        logger.exception("Falha ao registar encomenda: etapa=criar_encomenda")
        await _mostrar(query, context, MENSAGEM_FALHA_REGISTO, kb.teclado_falha_registo(data), parse_mode=None)
        return
    if resultado == "existente":
        # Tapped again, or lost a race with the same screen. Recover too: the first tap may
        # have stopped before creating the payment session. Same stable key, so no second session.
        await query.answer(MENSAGEM_JA_REGISTADA, show_alert=True)
        await _mostrar_pagamento(query, context, data, valor)
    elif resultado == "criada":
        await query.answer()  # stop the button spinner before Stripe
        await _mostrar_pagamento(query, context, data, valor)
    elif resultado == "alterado":
        await _mostrar_alterado(query, context, valor, f"produto_{valor[0].produto_id}")
    elif resultado == "pendente":
        await _mostrar(query, context, MENSAGEM_ENCOMENDA_PENDENTE.format(id=valor.id),
                       kb.teclado_ver_encomenda(valor.id), parse_mode=None)
    elif resultado == "expirada":
        await _mostrar(query, context, MENSAGEM_ECRA_EXPIRADO, kb.teclado_ecra_expirado(), parse_mode=None)
    else:  # "de_outro_utilizador", "invalida"
        await _mostrar_invalido(query, context)


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
    ("ver_encomenda_", encomenda_detalhe),
    ("encomendar_", encomendar),
    ("confirmar_encomenda_", confirmar_encomenda),
)
