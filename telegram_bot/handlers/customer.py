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
    ESTADO_EMOJI, MENSAGEM_ALTERADO, MENSAGEM_CARRINHO_VAZIO, MENSAGEM_ECRA_EXPIRADO,
    MENSAGEM_ENCOMENDA_PENDENTE, MENSAGEM_FALHA_REGISTO, MENSAGEM_JA_REGISTADA, MENSAGEM_MENU_EXPIRADO,
    MENSAGEM_PAGAMENTO_A_CONFIRMAR, MENSAGEM_PEDIDO_INVALIDO, MENSAGEM_PRAZO_TERMINADO,
    MENSAGEM_REPETIDA, MENSAGEM_REPETIDA_IGNORADOS, MENSAGEM_REPETIDA_REDUZIDOS,
    RESULTADO_CARRINHO, ROTULO_ALTERACAO,
)
from telegram_bot.traducoes import estado, tr
from telegram_bot.ui import guardar_idioma, idioma, md, responder
from telegram_bot import keyboards as kb

logger = logging.getLogger(__name__)


async def menu_inicio(query, context, data, user_id):
    i = await idioma(context, query.from_user)
    await responder(query, context, tr(i, "O que pretendes fazer?"),
                    kb.teclado_menu_principal(await services.e_admin(user_id), i), parse_mode=None)


async def mudar_idioma(query, context, data, user_id):
    """idioma_pt / idioma_en: save the choice and redraw the main menu in that language."""
    novo = data.removeprefix("idioma_")
    await services.definir_idioma(user_id, novo)
    guardar_idioma(context, novo)
    await menu_inicio(query, context, data, user_id)


async def menu_catalogo(query, context, data, user_id):
    i = await idioma(context, query.from_user)
    await responder(query, context, tr(i, "🛍️ *Catálogo*\nEscolhe uma categoria:"),
                    kb.teclado_catalogo(await services.get_categorias_ativas(), i))


async def categoria(query, context, data, user_id):
    i = await idioma(context, query.from_user)
    valor = data[4:]
    # Old code buttons (cat_ssd) and deactivated categories get a clear message, not an error.
    cat = await services.get_categoria_ativa(int(valor)) if valor.isdecimal() and valor[0] != "0" else None
    if cat is None:
        await responder(query, context, tr(i, MENSAGEM_MENU_EXPIRADO), kb.teclado_voltar_catalogo(i), parse_mode=None)
        return
    produtos = await services.get_produtos_por_categoria(cat.id)
    if not produtos:
        await responder(query, context,
                        tr(i, "😔 Sem produtos disponíveis em *{nome}*.").format(nome=md(cat.nome, '*')),
                        kb.teclado_voltar_catalogo(i))
        return
    await responder(query, context, tr(i, "{nome}\nEscolhe um produto:").format(nome=cat.nome),
                    kb.teclado_produtos_categoria(produtos, i), parse_mode=None)


async def produto_detalhe(query, context, data, user_id):
    i = await idioma(context, query.from_user)
    try:
        p = await services.get_produto(sanitizar_id(data.split("_")[1], campo="produto_id"))
    except (EntradaInvalida, Produto.DoesNotExist, ValueError):
        await responder(query, context, tr(i, "❌ Produto não encontrado."),
                        kb.teclado_voltar_menu(i), parse_mode=None)
        return
    descricao = f"\n_{md(p.descricao, '_')}_" if p.descricao else ""
    stock_aviso = tr(i, "\n⚠️ _Último em stock!_") if p.stock_atual == 1 else ""
    caption = tr(i, "📦 *{nome}*\n\n💶 Preço: *{preco:.2f}€*\n📦 Stock: {stock} unidades{descricao}{aviso}").format(
        nome=md(p.nome, '*'), preco=p.preco, stock=p.stock_atual, descricao=descricao, aviso=stock_aviso)
    keyboard = kb.teclado_produto(p, i)
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
        await responder(query, context, caption + tr(i, "\n(Imagem indisponível)"), keyboard)
        return
    try:
        await query.message.delete()
    except TelegramError:
        logger.debug("Mensagem anterior não apagada: produto_id=%s", p.id)


async def sem_stock(query, context, data, user_id):
    await query.answer(tr(await idioma(context, query.from_user), "❌ Este produto não tem stock disponível."),
                       show_alert=True)


async def menu_encomendas(query, context, data, user_id):
    i = await idioma(context, query.from_user)
    encomendas = await services.get_encomendas_utilizador(user_id)
    if not encomendas:
        await responder(query, context, tr(i, "📦 Ainda não tens encomendas."),
                        kb.teclado_voltar_menu(i), parse_mode=None)
        return
    await responder(query, context,
                    tr(i, "🛒 *As Minhas Encomendas*\n\nEscolhe uma encomenda para ver detalhes:"),
                    kb.teclado_minhas_encomendas(encomendas, i))


async def encomenda_detalhe(query, context, data, user_id):
    i = await idioma(context, query.from_user)
    try:
        eid = sanitizar_id(data.split("_")[-1], campo="encomenda_id")
        encomenda = await services.get_encomenda_detalhe(eid)
        if str(encomenda.utilizador.telegram_id) != str(user_id):
            await query.answer(tr(i, "⛔ Acesso negado."), show_alert=True)
            return
    except (EntradaInvalida, Encomenda.DoesNotExist, ValueError):
        await responder(query, context, tr(i, "❌ Encomenda não encontrada."),
                        kb.teclado_voltar_encomendas(i), parse_mode=None)
        return
    emoji = ESTADO_EMOJI.get(encomenda.estado, "📦")
    linhas_enc = await services.get_linhas_encomenda(encomenda)
    produtos_txt = "\n".join(
        f"  • {md(l.produto.nome)} x{l.quantidade} — {l.subtotal:.2f}€"
        for l in linhas_enc
    )
    await responder(
        query, context,
        tr(i, "📋 *Encomenda #{id}*\n\n{emoji} Estado: *{estado}*\n💶 Total: *{total:.2f}€*\n📅 Data: {data}\n\n"
              "*Produtos:*\n{produtos}").format(
            id=encomenda.id, emoji=emoji, estado=estado(i, encomenda.estado), total=encomenda.total,
            data=encomenda.data_encomenda.strftime('%d/%m/%Y %H:%M'), produtos=produtos_txt),
        kb.teclado_detalhe_encomenda(encomenda.id, await services.pode_repetir(encomenda, user_id), i)
    )


async def _mostrar(query, context, texto, teclado, parse_mode="Markdown"):
    """responder(), treating Telegram's "message is not modified" as success."""
    try:
        await responder(query, context, texto, teclado, parse_mode=parse_mode)
    except BadRequest as exc:
        if "not modified" not in str(exc).lower():
            raise


def _texto_alteracoes(alteracoes, i="pt"):
    """What Confirm found changed, as plain text above the cart."""
    linhas = [tr(i, MENSAGEM_ALTERADO)]
    for a in alteracoes:
        rotulo = tr(i, ROTULO_ALTERACAO[a.tipo]).format(antes=a.antes, depois=a.depois)
        linhas.append(f"• {rotulo}" if a.tipo == "carrinho" else f"• {a.nome[:60] or tr(i, 'Produto')}: {rotulo}")
    return "\n".join(linhas)


def _texto_carrinho(linhas, total, aviso="", i="pt"):
    partes = [aviso, ""] if aviso else []
    if not linhas:
        return "\n".join(partes + [tr(i, MENSAGEM_CARRINHO_VAZIO)])
    partes.append(tr(i, "🛒 Carrinho\n"))
    for l in linhas:
        marca = ""
        if l.indisponivel:
            marca = " " + tr(i, ROTULO_ALTERACAO["inativo"])
        elif l.stock_insuficiente:
            marca = tr(i, " ⚠️ só há {stock} em stock").format(stock=l.stock)
        if l.preco_alterado:
            marca += " " + tr(i, ROTULO_ALTERACAO["preco"]).format(antes=l.preco_ao_adicionar, depois=l.preco)
        partes.append(f"• {l.quantidade}× {l.nome[:60]} — {l.subtotal:.2f}€{marca}")
    partes.append(tr(i, "\nTotal: {total:.2f}€").format(total=total))
    return "\n".join(partes)


async def _mostrar_carrinho(query, context, aviso=""):
    """The cart with live prices and flags (plain text, so names need no escaping)."""
    i = await idioma(context, query.from_user)
    linhas, total = await services.ver_carrinho_async(query.from_user)
    await _mostrar(query, context, _texto_carrinho(linhas, total, aviso, i), kb.teclado_carrinho(linhas, i),
                   parse_mode=None)


def _id_final(data, campo):
    """The id after the last underscore, or None if it isn't a positive integer."""
    try:
        return sanitizar_id(data.rsplit("_", 1)[-1], campo=campo)
    except EntradaInvalida:
        return None


async def carrinho_ver(query, context, data, user_id):
    await _mostrar_carrinho(query, context)


async def carrinho_add(query, context, data, user_id):
    """Product screen button (and old encomendar_<id> buttons): add one unit, keep the screen."""
    i = await idioma(context, query.from_user)
    produto_id = _id_final(data, "produto_id")
    if produto_id is None:
        await query.answer(tr(i, MENSAGEM_PEDIDO_INVALIDO), show_alert=True)
        return
    resultado, quantidade = await services.adicionar_ao_carrinho_async(query.from_user, produto_id)
    await query.answer(tr(i, RESULTADO_CARRINHO[resultado]).format(quantidade=quantidade),
                       show_alert=resultado != "adicionado")


async def _mudar_linha(query, context, data, mudanca):
    i = await idioma(context, query.from_user)
    produto_id = _id_final(data, "produto_id")
    if produto_id is None:
        await query.answer(tr(i, MENSAGEM_PEDIDO_INVALIDO), show_alert=True)
        return
    resultado = await mudanca(query.from_user, produto_id)
    if isinstance(resultado, tuple):  # alterar_quantidade also returns the quantity
        resultado = resultado[0]
    if resultado not in ("adicionado", "removido"):  # a limit, or the line is gone: say why
        await query.answer(tr(i, RESULTADO_CARRINHO[resultado]).format(quantidade=0))
    await _mostrar_carrinho(query, context)


async def carrinho_mais(query, context, data, user_id):
    await _mudar_linha(query, context, data, lambda u, pid: services.alterar_quantidade_async(u, pid, 1))


async def carrinho_menos(query, context, data, user_id):
    await _mudar_linha(query, context, data, lambda u, pid: services.alterar_quantidade_async(u, pid, -1))


async def carrinho_remover(query, context, data, user_id):
    await _mudar_linha(query, context, data, services.remover_do_carrinho_async)


async def carrinho_finalizar(query, context, data, user_id):
    """Save the whole cart as a confirmation screen and show it."""
    i = await idioma(context, query.from_user)
    resultado, snap, precos_alterados = await services.preparar_confirmacao_carrinho_async(query.from_user)
    if resultado != "ok":  # empty, or a line blocks checkout now: show the cart as it is
        await _mostrar_carrinho(query, context)
        return
    linhas = "\n".join(
        f"• {l['quantidade']}× {l['nome'][:60]} — "
        f"{services._euros(l['quantidade'] * l['preco_unitario_centimos']):.2f}€"
        + (tr(i, " ⚠️ preço alterado") if l["produto_id"] in precos_alterados else "")
        for l in snap.linhas
    )
    await _mostrar(
        query, context,
        tr(i, "🛒 Confirmar encomenda\n\n{linhas}\n\nTotal: {total:.2f}€\n\nDesejas confirmar esta encomenda?").format(
            linhas=linhas, total=services._euros(snap.total_centimos)),
        kb.teclado_confirmar_encomenda(confirmacao.callback_confirmar(snap.nonce), i), parse_mode=None,
    )


async def repetir_encomenda(query, context, data, user_id):
    """Merge a cancelled unpaid order back into the cart; the customer confirms again."""
    i = await idioma(context, query.from_user)
    encomenda_id = _id_final(data, "encomenda_id")
    resultado = None if encomenda_id is None else await services.repetir_encomenda_async(query.from_user, encomenda_id)
    if resultado is None:
        await query.answer(tr(i, MENSAGEM_PEDIDO_INVALIDO), show_alert=True)
        return
    _, reduzidos, ignorados = resultado
    aviso = [tr(i, MENSAGEM_REPETIDA)]
    if reduzidos:
        aviso.append(tr(i, MENSAGEM_REPETIDA_REDUZIDOS).format(nomes=", ".join(n[:60] for n in reduzidos)))
    if ignorados:
        aviso.append(tr(i, MENSAGEM_REPETIDA_IGNORADOS).format(nomes=", ".join(n[:60] for n in ignorados)))
    await _mostrar_carrinho(query, context, "\n".join(aviso))


def _texto_linhas(linhas):
    return "\n".join(
        f"• {l.quantidade}× {md((l.nome_produto or l.produto.nome)[:60])} — {l.subtotal:.2f}€" for l in linhas
    )


async def _mostrar_pagamento(query, context, data, encomenda):
    """Recover or create the order's Stripe session and draw the matching screen (§3.9)."""
    i = await idioma(context, query.from_user)
    try:
        estado, url = await services.garantir_sessao_pagamento_async(encomenda)
    except Exception:
        logger.exception("Falha ao preparar pagamento: encomenda=#%s", encomenda.id)
        await _mostrar(
            query, context,
            tr(i, "✅ *Encomenda #{id} criada!*\n\n⚠️ Não foi possível gerar o link de pagamento.\nContacta o suporte.")
            .format(id=encomenda.id),
            kb.teclado_falha_pagamento(data, i)
        )
        return
    if estado == "aberta":
        linhas = await services.get_linhas_encomenda(encomenda)
        await _mostrar(
            query, context,
            tr(i, "✅ *Encomenda #{id} criada!*\n\n{linhas}\n\nTotal: *{total:.2f}€*\n\n"
                  "Clica no botão abaixo para completar o pagamento.\n"
                  "A encomenda é confirmada automaticamente após o pagamento. 💳").format(
                id=encomenda.id, linhas=_texto_linhas(linhas), total=encomenda.total),
            kb.teclado_pagamento(url, i)
        )
    elif estado == "a_confirmar":
        await _mostrar(query, context, tr(i, MENSAGEM_PAGAMENTO_A_CONFIRMAR).format(id=encomenda.id),
                       kb.teclado_ver_encomendas(i), parse_mode=None)
    elif estado == "expirada":
        await _mostrar(query, context, tr(i, MENSAGEM_PRAZO_TERMINADO),
                       kb.teclado_ver_encomenda(encomenda.id, i), parse_mode=None)
    # "paga" / "nao_ativa": the alert already said it; leave the message as it is.


async def _mostrar_invalido(query, context, i):
    await _mostrar(query, context, tr(i, MENSAGEM_PEDIDO_INVALIDO), kb.teclado_voltar_menu(i), parse_mode=None)


async def confirmar_encomenda(query, context, data, user_id):
    i = await idioma(context, query.from_user)
    tipo, nonce = confirmacao.ler_callback(data)
    if tipo == "invalido":  # includes the pre-token confirmar_encomenda_<produto_id> buttons
        await _mostrar(query, context, tr(i, MENSAGEM_ECRA_EXPIRADO), kb.teclado_ecra_expirado(i), parse_mode=None)
        return
    if tipo == "antigo":  # a button from before Stage 2: it can only reopen its existing order
        existente = await services.get_encomenda_por_chave(nonce)
        if existente is None:
            await _mostrar(query, context, tr(i, MENSAGEM_ECRA_EXPIRADO), kb.teclado_ecra_expirado(i), parse_mode=None)
        elif str(existente.utilizador.telegram_id) != str(user_id):
            await query.answer(tr(i, MENSAGEM_PEDIDO_INVALIDO), show_alert=True)
        else:
            await query.answer(tr(i, MENSAGEM_JA_REGISTADA), show_alert=True)
            await _mostrar_pagamento(query, context, data, existente)
        return

    try:
        resultado, valor = await services.criar_encomenda_de_confirmacao_async(query.from_user, nonce)
    except Exception:
        logger.exception("Falha ao registar encomenda: etapa=criar_encomenda")
        await _mostrar(query, context, tr(i, MENSAGEM_FALHA_REGISTO), kb.teclado_falha_registo(data, i), parse_mode=None)
        return
    if resultado == "existente":
        # Tapped again, or lost a race with the same screen. Recover too: the first tap may
        # have stopped before creating the payment session. Same stable key, so no second session.
        await query.answer(tr(i, MENSAGEM_JA_REGISTADA), show_alert=True)
        await _mostrar_pagamento(query, context, data, valor)
    elif resultado == "criada":
        await query.answer()  # stop the button spinner before Stripe
        await _mostrar_pagamento(query, context, data, valor)
    elif resultado == "alterado":
        await _mostrar_carrinho(query, context, _texto_alteracoes(valor, i))
    elif resultado == "pendente":
        await _mostrar(query, context, tr(i, MENSAGEM_ENCOMENDA_PENDENTE).format(id=valor.id),
                       kb.teclado_ver_encomenda(valor.id, i), parse_mode=None)
    elif resultado == "expirada":
        await _mostrar(query, context, tr(i, MENSAGEM_ECRA_EXPIRADO), kb.teclado_ecra_expirado(i), parse_mode=None)
    else:  # "de_outro_utilizador", "invalida"
        await _mostrar_invalido(query, context, i)


async def menu_ajuda(query, context, data, user_id):
    i = await idioma(context, query.from_user)
    await responder(
        query, context,
        tr(i, "ℹ️ *Ajuda*\n\nNavega usando os botões apresentados.\n"
              "Usa /start a qualquer momento para voltar ao menu principal."),
        kb.teclado_voltar_menu(i)
    )


EXACT_ROUTES = {
    "menu_inicio": menu_inicio,
    "menu_catalogo": menu_catalogo,
    "sem_stock": sem_stock,
    "menu_encomendas": menu_encomendas,
    "menu_ajuda": menu_ajuda,
    "carrinho_ver": carrinho_ver,
    "carrinho_finalizar": carrinho_finalizar,
    "idioma_pt": mudar_idioma,
    "idioma_en": mudar_idioma,
}

PREFIX_ROUTES = (
    ("cat_", categoria),
    ("produto_", produto_detalhe),
    ("ver_encomenda_", encomenda_detalhe),
    ("encomendar_", carrinho_add),  # buttons drawn before the cart now add to it
    ("confirmar_encomenda_", confirmar_encomenda),
    ("carrinho_add_", carrinho_add),
    ("carrinho_mais_", carrinho_mais),
    ("carrinho_menos_", carrinho_menos),
    ("carrinho_remover_", carrinho_remover),
    ("repetir_encomenda_", repetir_encomenda),
)
