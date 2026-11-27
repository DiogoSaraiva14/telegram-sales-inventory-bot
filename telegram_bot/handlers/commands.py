"""Slash-command handlers: /start and /admin."""
from telegram import Update
from telegram.ext import ContextTypes

from core import services
from security.decorators import rate_limit, log_acao
from telegram_bot.guards import verificar_bloqueio, verificar_sessao
from telegram_bot.handlers.admin import titulo_admin
from telegram_bot.keyboards import teclado_menu_principal, teclado_admin_menu
from telegram_bot.traducoes import tr
from telegram_bot.ui import guardar_idioma, idioma, md


@rate_limit
@verificar_bloqueio
@verificar_sessao
@log_acao("START")
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    utilizador, criado = await services.get_or_create_utilizador(update.effective_user)
    i = utilizador.idioma or "pt"
    guardar_idioma(context, i)
    loja = await services.obter_loja_async()
    saudacao = tr(i, "Bem-vindo! 👋") if criado else tr(i, "Bem-vindo de volta! 👋")
    await update.message.reply_text(
        # The shop's name and welcome text stay as the shop wrote them.
        tr(i, "Olá, *{nome}*! {saudacao}\n\n*{loja}*\n{boas_vindas}").format(
            nome=md(utilizador.nome, '*'), saudacao=saudacao, loja=md(loja.nome, '*'),
            boas_vindas=md(loja.texto_boas_vindas)),
        parse_mode="Markdown",
        reply_markup=teclado_menu_principal(await services.e_admin(update.effective_user.id), i)
    )


@log_acao("ADMIN")
async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    i = await idioma(context, update.effective_user)
    if not await services.e_admin(update.effective_user.id):
        await update.message.reply_text(tr(i, "⛔ Acesso negado."))
        return
    await update.message.reply_text(
        titulo_admin(await services.obter_loja_async(), i),
        parse_mode="Markdown",
        reply_markup=teclado_admin_menu(i)
    )
