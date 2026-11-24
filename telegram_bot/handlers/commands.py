"""Slash-command handlers: /start and /admin."""
from telegram import Update
from telegram.ext import ContextTypes

from core.services import get_or_create_utilizador
from security.decorators import rate_limit, log_acao
from telegram_bot.guards import is_admin, verificar_bloqueio, verificar_sessao
from telegram_bot.keyboards import teclado_menu_principal, teclado_admin_menu
from telegram_bot.ui import md


@rate_limit
@verificar_bloqueio
@verificar_sessao
@log_acao("START")
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    utilizador, criado = await get_or_create_utilizador(update.effective_user)
    saudacao = "Bem-vindo! 👋" if criado else "Bem-vindo de volta! 👋"
    await update.message.reply_text(
        f"Olá, *{md(utilizador.nome, '*')}*! {saudacao}\n\nO que pretendes fazer?",
        parse_mode="Markdown",
        reply_markup=teclado_menu_principal(update.effective_user.id)
    )


@log_acao("ADMIN")
async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ Acesso negado.")
        return
    await update.message.reply_text(
        "🔐 *Painel Admin — Jarvis*\n\nO que pretendes gerir?",
        parse_mode="Markdown",
        reply_markup=teclado_admin_menu()
    )
