import os
import time
import asyncio
import django
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / '.env')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

import stripe
import logging
from asgiref.sync import sync_to_async
from django.conf import settings
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import TelegramError
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes
from core.models import (
    Utilizador, Produto, Encomenda, LinhaEncomenda,
    MovimentoStock, RegistoAuditoria, Pagamento,
)
from security.decorators import rate_limit, log_acao
from security.sanitization import (
    validar_callback,
    validar_telegram_id,
    sanitizar_id,
    sanitizar_delta_stock,
    sanitizar_nome_utilizador,
    EntradaInvalida,
)

stripe.api_key = settings.STRIPE_SECRET_KEY

logging.basicConfig(
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ── Admin IDs ──────────────────────────────────────────────────────────────

ADMIN_IDS = set(
    int(x.strip())
    for x in os.getenv("ADMIN_TELEGRAM_IDS", "").split(",")
    if x.strip().isdigit()
)

def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS

# ── Categorias ────────────────────────────────────────────────────────────

CATEGORIAS = {
    'cpu': '🔲 Processadores',
    'gpu': '🎮 Placas Gráficas',
    'ram': '🧠 Memória RAM',
    'ssd': '💾 Armazenamento',
    'motherboard': '🖥️ Motherboards',
    'psu': '⚡ Fontes de Alimentação',
    'caixa': '🗄️ Caixas',
    'cooling': '❄️ Coolers',
}

# ── Mensagens de notificação ───────────────────────────────────────────────

MENSAGENS_NOTIFICACAO = {
    'confirmada': (
        "✅ *A tua encomenda foi confirmada!*\n\n"
        "📦 Encomenda #{id}\n"
        "💶 Total: {total:.2f}€\n\n"
        "Estamos a preparar o teu pedido. Receberás uma notificação quando for enviado."
    ),
    'enviada': (
        "🚚 *A tua encomenda foi enviada!*\n\n"
        "📦 Encomenda #{id}\n"
        "💶 Total: {total:.2f}€\n\n"
        "O teu pedido está a caminho. Aguarda a entrega em breve."
    ),
    'entregue': (
        "📬 *A tua encomenda foi entregue!*\n\n"
        "📦 Encomenda #{id}\n"
        "💶 Total: {total:.2f}€\n\n"
        "Obrigado pela tua compra! Esperamos que fiques satisfeito. 🙏"
    ),
    'cancelada': (
        "❌ *A tua encomenda foi cancelada.*\n\n"
        "📦 Encomenda #{id}\n"
        "💶 Total: {total:.2f}€\n\n"
        "Se não solicitaste este cancelamento, contacta o suporte."
    ),
}

ESTADO_EMOJI = {
    'pendente': '🕐',
    'confirmada': '✅',
    'enviada': '🚚',
    'entregue': '📬',
    'cancelada': '❌',
}

# ── Session timeout ────────────────────────────────────────────────────────

SESSION_TIMEOUT = 30 * 60
_ultima_atividade: dict[int, float] = {}

def _registar_atividade(user_id: int) -> None:
    _ultima_atividade[user_id] = time.monotonic()

def _sessao_expirada(user_id: int) -> bool:
    ultimo = _ultima_atividade.get(user_id)
    if ultimo is None:
        return True
    return (time.monotonic() - ultimo) > SESSION_TIMEOUT

def verificar_sessao(func):
    from functools import wraps
    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        if update.message and update.message.text and update.message.text.startswith("/start"):
            _registar_atividade(user_id)
            return await func(update, context)
        if update.callback_query:
            if _sessao_expirada(user_id):
                logger.info("Sessão expirada: user_id=%s", user_id)
                await update.callback_query.answer(
                    "⏱️ A tua sessão expirou. Usa /start para recomeçar.",
                    show_alert=True
                )
                try:
                    await update.callback_query.edit_message_text(
                        "⏱️ *Sessão expirada.*\n\nUsa /start para voltar ao menu principal.",
                        parse_mode="Markdown"
                    )
                except Exception:
                    pass
                return
            _registar_atividade(user_id)
            return await func(update, context)
    return wrapper

async def _verificar_bloqueio(user_id: int) -> bool:
    try:
        u = await sync_to_async(Utilizador.objects.get)(telegram_id=str(user_id))
        return not u.ativo
    except Utilizador.DoesNotExist:
        return False

def verificar_bloqueio(func):
    from functools import wraps
    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        if await _verificar_bloqueio(user_id):
            logger.warning("Utilizador bloqueado tentou aceder: user_id=%s", user_id)
            if update.message:
                await update.message.reply_text(
                    "🚫 A tua conta foi suspensa.\n"
                    "Contacta o suporte se acreditas que isto é um erro."
                )
            elif update.callback_query:
                await update.callback_query.answer("🚫 A tua conta está suspensa.", show_alert=True)
                try:
                    await update.callback_query.edit_message_text(
                        "🚫 *Conta suspensa.*\n\nContacta o suporte se acreditas que isto é um erro.",
                        parse_mode="Markdown"
                    )
                except Exception:
                    pass
            return
        return await func(update, context)
    return wrapper

# ── Teclados ───────────────────────────────────────────────────────────────

def teclado_menu_principal(user_id: int = None):
    buttons = [
        [InlineKeyboardButton("🛍️ Ver Catálogo", callback_data="menu_catalogo")],
        [InlineKeyboardButton("🛒 As Minhas Encomendas", callback_data="menu_encomendas")],
        [InlineKeyboardButton("ℹ️ Ajuda", callback_data="menu_ajuda")],
    ]
    if user_id and is_admin(user_id):
        buttons.append([InlineKeyboardButton("🔐 Painel Admin", callback_data="admin_menu")])
    return InlineKeyboardMarkup(buttons)

def teclado_voltar_menu():
    return InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Menu Principal", callback_data="menu_inicio")]])

def teclado_voltar_catalogo():
    return InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Catálogo", callback_data="menu_catalogo")]])

def teclado_voltar_admin():
    return InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Painel Admin", callback_data="admin_menu")]])

def teclado_voltar_encomendas():
    return InlineKeyboardMarkup([[InlineKeyboardButton("🔙 As Minhas Encomendas", callback_data="menu_encomendas")]])

def teclado_admin_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📦 Gerir Produtos", callback_data="admin_produtos")],
        [InlineKeyboardButton("📊 Gerir Stock", callback_data="admin_stock")],
        [InlineKeyboardButton("📋 Ver Encomendas", callback_data="admin_encomendas")],
        [InlineKeyboardButton("👥 Gerir Utilizadores", callback_data="admin_utilizadores")],
        [InlineKeyboardButton("📜 Auditoria", callback_data="admin_auditoria")],
        [InlineKeyboardButton("🔙 Menu Principal", callback_data="menu_inicio")],
    ])

def teclado_produtos_admin(produtos):
    buttons = []
    for p in produtos:
        estado = "✅" if p.ativo else "❌"
        buttons.append([InlineKeyboardButton(
            f"{estado} {p.nome} — {p.preco:.2f}€",
            callback_data=f"admin_produto_detalhe_{p.id}"
        )])
    buttons.append([InlineKeyboardButton("🔙 Painel Admin", callback_data="admin_menu")])
    return InlineKeyboardMarkup(buttons)

def teclado_produto_acao(produto):
    toggle_label = "❌ Desativar" if produto.ativo else "✅ Ativar"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(toggle_label, callback_data=f"admin_produto_toggle_{produto.id}")],
        [InlineKeyboardButton("📊 Ajustar Stock", callback_data=f"admin_stock_produto_{produto.id}")],
        [InlineKeyboardButton("🔙 Lista de Produtos", callback_data="admin_produtos")],
    ])

def teclado_stock_ajuste(produto_id):
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("➕ +1",  callback_data=f"admin_stock_adj_{produto_id}_1"),
            InlineKeyboardButton("➕ +5",  callback_data=f"admin_stock_adj_{produto_id}_5"),
            InlineKeyboardButton("➕ +10", callback_data=f"admin_stock_adj_{produto_id}_10"),
        ],
        [
            InlineKeyboardButton("➖ -1",  callback_data=f"admin_stock_adj_{produto_id}_-1"),
            InlineKeyboardButton("➖ -5",  callback_data=f"admin_stock_adj_{produto_id}_-5"),
            InlineKeyboardButton("➖ -10", callback_data=f"admin_stock_adj_{produto_id}_-10"),
        ],
        [InlineKeyboardButton("🔙 Voltar ao Produto", callback_data=f"admin_produto_detalhe_{produto_id}")],
    ])

def teclado_utilizadores_admin(utilizadores):
    buttons = []
    for u in utilizadores:
        estado = "✅" if u.ativo else "🚫"
        buttons.append([InlineKeyboardButton(
            f"{estado} {u.nome} (@{u.username or 'sem username'})",
            callback_data=f"admin_utilizador_{u.telegram_id}"
        )])
    buttons.append([InlineKeyboardButton("🔙 Painel Admin", callback_data="admin_menu")])
    return InlineKeyboardMarkup(buttons)

def teclado_encomendas_admin(encomendas):
    buttons = []
    for e in encomendas:
        emoji = ESTADO_EMOJI.get(e.estado, '📦')
        buttons.append([InlineKeyboardButton(
            f"{emoji} #{e.id} — {e.utilizador.nome} — {e.total:.2f}€ [{e.estado}]",
            callback_data=f"admin_ver_encomenda_{e.id}"
        )])
    buttons.append([InlineKeyboardButton("🔙 Painel Admin", callback_data="admin_menu")])
    return InlineKeyboardMarkup(buttons)

def teclado_acoes_encomenda(encomenda):
    buttons = []
    estado = encomenda.estado
    if estado == 'pendente':
        buttons.append([InlineKeyboardButton("✅ Confirmar", callback_data=f"admin_enc_confirmar_{encomenda.id}")])
        buttons.append([InlineKeyboardButton("❌ Cancelar",  callback_data=f"admin_enc_cancelar_{encomenda.id}")])
    elif estado == 'confirmada':
        buttons.append([InlineKeyboardButton("🚚 Marcar Enviada", callback_data=f"admin_enc_enviada_{encomenda.id}")])
        buttons.append([InlineKeyboardButton("❌ Cancelar",       callback_data=f"admin_enc_cancelar_{encomenda.id}")])
    elif estado == 'enviada':
        buttons.append([InlineKeyboardButton("📬 Marcar Entregue", callback_data=f"admin_enc_entregue_{encomenda.id}")])
        buttons.append([InlineKeyboardButton("🔙 Lista de Encomendas", callback_data="admin_encomendas")])
    return InlineKeyboardMarkup(buttons)

# ── Helper UI ──────────────────────────────────────────────────────────────

async def responder(query, context, texto, reply_markup, parse_mode="Markdown"):
    if query.message.photo or query.message.document:
        await query.message.delete()
        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text=texto,
            parse_mode=parse_mode,
            reply_markup=reply_markup
        )
    else:
        await query.edit_message_text(
            text=texto,
            parse_mode=parse_mode,
            reply_markup=reply_markup
        )

# ── Helpers BD ─────────────────────────────────────────────────────────────

async def get_or_create_utilizador(user):
    telegram_id_limpo = validar_telegram_id(user.id)
    nome_limpo = sanitizar_nome_utilizador(user.full_name)
    return await sync_to_async(Utilizador.objects.get_or_create)(
        telegram_id=telegram_id_limpo,
        defaults={"nome": nome_limpo, "username": user.username or ""}
    )

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

async def get_todos_utilizadores():
    return await sync_to_async(list)(Utilizador.objects.all().order_by('nome'))

async def toggle_utilizador_ativo(telegram_id: str) -> tuple:
    utilizador = await sync_to_async(Utilizador.objects.get)(telegram_id=telegram_id)
    utilizador.ativo = not utilizador.ativo
    await sync_to_async(utilizador.save)()
    return utilizador, utilizador.ativo

# ── Notificações ───────────────────────────────────────────────────────────

async def notificar_cliente(context: ContextTypes.DEFAULT_TYPE, encomenda, novo_estado: str) -> bool:
    template = MENSAGENS_NOTIFICACAO.get(novo_estado)
    if not template:
        return False
    mensagem = template.format(id=encomenda.id, total=float(encomenda.total))
    try:
        await context.bot.send_message(
            chat_id=int(encomenda.utilizador.telegram_id),
            text=mensagem,
            parse_mode="Markdown"
        )
        logger.info(
            "Notificação enviada: encomenda=#%s estado=%s telegram_id=%s",
            encomenda.id, novo_estado, encomenda.utilizador.telegram_id
        )
        return True
    except TelegramError as exc:
        logger.error(
            "Falha ao notificar cliente: encomenda=#%s telegram_id=%s erro=%s",
            encomenda.id, encomenda.utilizador.telegram_id, exc
        )
        return False

async def _mudar_estado_e_notificar(
    query, context, user_id: int,
    encomenda_id: int, novo_estado: str,
    label_acao: str
):
    try:
        if novo_estado == 'cancelada':
            encomenda = await cancelar_encomenda_db(encomenda_id)
        else:
            encomenda = await atualizar_estado_encomenda(encomenda_id, novo_estado)
    except (Encomenda.DoesNotExist, ValueError):
        await responder(
            query, context,
            "❌ Encomenda não encontrada ou estado inválido.",
            teclado_voltar_admin(), parse_mode=None
        )
        return

    notificada = await notificar_cliente(context, encomenda, novo_estado)
    await registar_auditoria(
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
        teclado_voltar_admin()
    )

# ── Handlers ───────────────────────────────────────────────────────────────

@rate_limit
@verificar_bloqueio
@verificar_sessao
@log_acao("START")
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    utilizador, criado = await get_or_create_utilizador(update.effective_user)
    saudacao = "Bem-vindo! 👋" if criado else "Bem-vindo de volta! 👋"
    await update.message.reply_text(
        f"Olá, *{utilizador.nome}*! {saudacao}\n\nO que pretendes fazer?",
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

@rate_limit
@verificar_bloqueio
@verificar_sessao
@log_acao("CALLBACK")
async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

    try:
        data = validar_callback(query.data)
    except EntradaInvalida as exc:
        logger.warning("callback_data inválido de user_id=%s: %s", user_id, exc)
        await query.answer("❌ Pedido inválido.", show_alert=True)
        return

    # ── Menu principal ─────────────────────────────────────────────────────
    if data == "menu_inicio":
        await responder(query, context, "O que pretendes fazer?",
                        teclado_menu_principal(user_id), parse_mode=None)

    # ── Catálogo ───────────────────────────────────────────────────────────
    elif data == "menu_catalogo":
        keyboard = [
            [InlineKeyboardButton(label, callback_data=f"cat_{key}")]
            for key, label in CATEGORIAS.items()
        ]
        keyboard.append([InlineKeyboardButton("🔙 Menu Principal", callback_data="menu_inicio")])
        await responder(query, context, "🛍️ *Catálogo*\nEscolhe uma categoria:",
                        InlineKeyboardMarkup(keyboard))

    # ── Categoria ──────────────────────────────────────────────────────────
    elif data.startswith("cat_"):
        categoria = data[4:]
        nome_cat = CATEGORIAS.get(categoria, "Categoria")
        produtos = await get_produtos_por_categoria(categoria)
        if not produtos:
            await responder(query, context,
                            f"😔 Sem produtos disponíveis em *{nome_cat}*.",
                            teclado_voltar_catalogo())
            return
        keyboard = [
            [InlineKeyboardButton(f"{p.nome} — {p.preco:.2f}€", callback_data=f"produto_{p.id}")]
            for p in produtos
        ]
        keyboard.append([InlineKeyboardButton("🔙 Catálogo", callback_data="menu_catalogo")])
        await responder(query, context, f"{nome_cat}\nEscolhe um produto:",
                        InlineKeyboardMarkup(keyboard), parse_mode=None)

    # ── Detalhe produto ────────────────────────────────────────────────────
    elif data.startswith("produto_"):
        try:
            p = await get_produto(sanitizar_id(data.split("_")[1], campo="produto_id"))
        except (EntradaInvalida, Produto.DoesNotExist, ValueError):
            await responder(query, context, "❌ Produto não encontrado.",
                            teclado_voltar_menu(), parse_mode=None)
            return
        descricao = f"\n_{p.descricao}_" if p.descricao else ""
        stock_aviso = "\n⚠️ _Último em stock!_" if p.stock_atual == 1 else ""
        caption = (
            f"📦 *{p.nome}*\n\n"
            f"💶 Preço: *{p.preco:.2f}€*\n"
            f"📦 Stock: {p.stock_atual} unidades"
            f"{descricao}{stock_aviso}"
        )
        encomendar_label = "🛒 Encomendar" if p.stock_atual > 0 else "❌ Sem Stock"
        encomendar_data  = f"encomendar_{p.id}" if p.stock_atual > 0 else "sem_stock"
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton(encomendar_label, callback_data=encomendar_data)],
            [InlineKeyboardButton("🔙 Voltar à Categoria", callback_data=f"cat_{p.categoria}")],
        ])
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

    # ── Sem stock ──────────────────────────────────────────────────────────
    elif data == "sem_stock":
        await query.answer("❌ Este produto não tem stock disponível.", show_alert=True)

    # ── As minhas encomendas ───────────────────────────────────────────────
    elif data == "menu_encomendas":
        encomendas = await get_encomendas_utilizador(user_id)
        if not encomendas:
            await responder(query, context, "📦 Ainda não tens encomendas.",
                            teclado_voltar_menu(), parse_mode=None)
            return
        buttons = []
        for e in encomendas:
            emoji = ESTADO_EMOJI.get(e.estado, "📦")
            buttons.append([InlineKeyboardButton(
                f"{emoji} #{e.id} — {e.total:.2f}€ — {e.estado}",
                callback_data=f"user_encomenda_{e.id}"
            )])
        buttons.append([InlineKeyboardButton("🔙 Menu Principal", callback_data="menu_inicio")])
        await responder(query, context,
                        "🛒 *As Minhas Encomendas*\n\nEscolhe uma encomenda para ver detalhes:",
                        InlineKeyboardMarkup(buttons))

    # ── Detalhe encomenda do utilizador ────────────────────────────────────
    elif data.startswith("user_encomenda_"):
        try:
            eid = sanitizar_id(data.split("_")[-1], campo="encomenda_id")
            encomenda = await get_encomenda_detalhe(eid)
            if str(encomenda.utilizador.telegram_id) != str(user_id):
                await query.answer("⛔ Acesso negado.", show_alert=True)
                return
        except (EntradaInvalida, Encomenda.DoesNotExist, ValueError):
            await responder(query, context, "❌ Encomenda não encontrada.",
                            teclado_voltar_encomendas(), parse_mode=None)
            return
        emoji = ESTADO_EMOJI.get(encomenda.estado, "📦")
        linhas_enc = await sync_to_async(list)(
            encomenda.linhas.select_related("produto").all()
        )
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
            teclado_voltar_encomendas()
        )

    # ── Encomendar — confirmação ───────────────────────────────────────────
    elif data.startswith("encomendar_"):
        try:
            p = await get_produto(sanitizar_id(data.split("_")[1], campo="produto_id"))
        except (EntradaInvalida, Produto.DoesNotExist, ValueError):
            await responder(query, context, "❌ Produto não encontrado.",
                            teclado_voltar_menu(), parse_mode=None)
            return
        if p.stock_atual <= 0:
            await query.answer("❌ Produto sem stock disponível.", show_alert=True)
            return
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Confirmar", callback_data=f"confirmar_encomenda_{p.id}")],
            [InlineKeyboardButton("❌ Cancelar",  callback_data=f"produto_{p.id}")],
        ])
        await responder(
            query, context,
            f"🛒 *Confirmar Encomenda*\n\n"
            f"Produto: *{p.nome}*\n"
            f"Preço: *{p.preco:.2f}€*\n\n"
            f"Desejas confirmar esta encomenda?",
            keyboard
        )

    # ── Confirmar encomenda + Stripe ───────────────────────────────────────
    elif data.startswith("confirmar_encomenda_"):
        try:
            p = await get_produto(sanitizar_id(data.split("_")[-1], campo="produto_id"))
        except (EntradaInvalida, Produto.DoesNotExist, ValueError):
            await responder(query, context, "❌ Produto não encontrado.",
                            teclado_voltar_menu(), parse_mode=None)
            return
        if p.stock_atual <= 0:
            await responder(query, context,
                            "😔 Lamentamos, este produto ficou sem stock entretanto.",
                            teclado_voltar_catalogo(), parse_mode=None)
            return
        utilizador, _ = await get_or_create_utilizador(query.from_user)
        encomenda = await criar_encomenda(utilizador, p)
        await registar_auditoria(
            user_id, "CRIAR_ENCOMENDA", "Encomenda",
            f"#{encomenda.id} — {p.nome} — {p.preco:.2f}€"
        )
        try:
            payment_url = await criar_sessao_stripe(encomenda, p)
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("💳 Pagar Agora", url=payment_url)],
                [InlineKeyboardButton("🛒 Ver Encomendas", callback_data="menu_encomendas")],
            ])
            await responder(
                query, context,
                f"✅ *Encomenda #{encomenda.id} criada!*\n\n"
                f"Produto: *{p.nome}*\n"
                f"Total: *{encomenda.total:.2f}€*\n\n"
                f"Clica no botão abaixo para completar o pagamento.\n"
                f"A encomenda é confirmada automaticamente após o pagamento. 💳",
                keyboard
            )
        except Exception as exc:
            logger.error("Erro ao criar sessão Stripe: %s", exc)
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("🛒 Ver Encomendas", callback_data="menu_encomendas")],
            ])
            await responder(
                query, context,
                f"✅ *Encomenda #{encomenda.id} criada!*\n\n"
                f"⚠️ Não foi possível gerar o link de pagamento.\n"
                f"Contacta o suporte.",
                keyboard
            )

    # ── Ajuda ──────────────────────────────────────────────────────────────
    elif data == "menu_ajuda":
        await responder(
            query, context,
            "ℹ️ *Ajuda*\n\nNavega usando os botões apresentados.\n"
            "Usa /start a qualquer momento para voltar ao menu principal.",
            teclado_voltar_menu()
        )

    # ── Admin — Menu ───────────────────────────────────────────────────────
    elif data == "admin_menu":
        if not is_admin(user_id):
            await query.answer("⛔ Acesso negado.", show_alert=True)
            return
        await responder(query, context,
                        "🔐 *Painel Admin — Jarvis*\n\nO que pretendes gerir?",
                        teclado_admin_menu())

    # ── Admin — Produtos ───────────────────────────────────────────────────
    elif data == "admin_produtos":
        if not is_admin(user_id):
            await query.answer("⛔ Acesso negado.", show_alert=True)
            return
        produtos = await get_todos_produtos()
        if not produtos:
            await responder(query, context, "📦 Sem produtos na base de dados.", teclado_voltar_admin())
            return
        await responder(
            query, context,
            "📦 *Gestão de Produtos*\n✅ = ativo | ❌ = inativo\n\nEscolhe um produto:",
            teclado_produtos_admin(produtos)
        )

    # ── Admin — Detalhe produto ────────────────────────────────────────────
    elif data.startswith("admin_produto_detalhe_"):
        if not is_admin(user_id):
            await query.answer("⛔ Acesso negado.", show_alert=True)
            return
        try:
            p = await get_produto_admin(sanitizar_id(data.split("_")[-1], campo="produto_id"))
        except (EntradaInvalida, Produto.DoesNotExist, ValueError):
            await responder(query, context, "❌ Produto não encontrado.", teclado_voltar_admin())
            return
        estado = "✅ Ativo" if p.ativo else "❌ Inativo"
        await responder(
            query, context,
            f"📦 *{p.nome}*\n\n"
            f"💶 Preço: *{p.preco:.2f}€*\n"
            f"📦 Stock: *{p.stock_atual}* unidades\n"
            f"📁 Categoria: {p.categoria}\n"
            f"Estado: {estado}",
            teclado_produto_acao(p)
        )

    # ── Admin — Toggle produto ─────────────────────────────────────────────
    elif data.startswith("admin_produto_toggle_"):
        if not is_admin(user_id):
            await query.answer("⛔ Acesso negado.", show_alert=True)
            return
        try:
            p = await get_produto_admin(sanitizar_id(data.split("_")[-1], campo="produto_id"))
        except (EntradaInvalida, Produto.DoesNotExist, ValueError):
            await responder(query, context, "❌ Produto não encontrado.", teclado_voltar_admin())
            return
        p.ativo = not p.ativo
        await sync_to_async(p.save)()
        acao = "ATIVAR_PRODUTO" if p.ativo else "DESATIVAR_PRODUTO"
        await registar_auditoria(user_id, acao, "Produto", f"{p.nome} (id={p.id})")
        await query.answer(f"Produto {'✅ ativado' if p.ativo else '❌ desativado'}.", show_alert=False)
        estado = "✅ Ativo" if p.ativo else "❌ Inativo"
        await responder(
            query, context,
            f"📦 *{p.nome}*\n\n"
            f"💶 Preço: *{p.preco:.2f}€*\n"
            f"📦 Stock: *{p.stock_atual}* unidades\n"
            f"📁 Categoria: {p.categoria}\n"
            f"Estado: {estado}",
            teclado_produto_acao(p)
        )

    # ── Admin — Stock lista ────────────────────────────────────────────────
    elif data == "admin_stock":
        if not is_admin(user_id):
            await query.answer("⛔ Acesso negado.", show_alert=True)
            return
        produtos = await get_todos_produtos()
        if not produtos:
            await responder(query, context, "📦 Sem produtos na base de dados.", teclado_voltar_admin())
            return
        buttons = [
            [InlineKeyboardButton(f"{p.nome} — {p.stock_atual} un.",
                                  callback_data=f"admin_stock_produto_{p.id}")]
            for p in produtos
        ]
        buttons.append([InlineKeyboardButton("🔙 Painel Admin", callback_data="admin_menu")])
        await responder(query, context,
                        "📊 *Gestão de Stock*\nEscolhe um produto para ajustar:",
                        InlineKeyboardMarkup(buttons))

    # ── Admin — Stock ajustar produto ──────────────────────────────────────
    elif data.startswith("admin_stock_produto_"):
        if not is_admin(user_id):
            await query.answer("⛔ Acesso negado.", show_alert=True)
            return
        try:
            p = await get_produto_admin(sanitizar_id(data.split("_")[-1], campo="produto_id"))
        except (EntradaInvalida, Produto.DoesNotExist, ValueError):
            await responder(query, context, "❌ Produto não encontrado.", teclado_voltar_admin())
            return
        await responder(
            query, context,
            f"📊 *Stock — {p.nome}*\n\nStock atual: *{p.stock_atual}* unidades\n\nAjusta o stock:",
            teclado_stock_ajuste(p.id)
        )

    # ── Admin — Stock aplicar ajuste ───────────────────────────────────────
    elif data.startswith("admin_stock_adj_"):
        if not is_admin(user_id):
            await query.answer("⛔ Acesso negado.", show_alert=True)
            return
        parts = data.split("_")
        try:
            produto_id = sanitizar_id(parts[-2], campo="produto_id")
            delta = sanitizar_delta_stock(parts[-1])
            p = await get_produto_admin(produto_id)
        except (EntradaInvalida, ValueError, Produto.DoesNotExist):
            await responder(query, context, "❌ Erro ao ajustar stock.", teclado_voltar_admin())
            return
        p.stock_atual = max(0, p.stock_atual + delta)
        await sync_to_async(p.save)()
        tipo = "entrada" if delta > 0 else "saida"
        await sync_to_async(MovimentoStock.objects.create)(
            produto=p,
            tipo_movimento=tipo,
            quantidade=abs(delta),
            observacoes=f"Ajuste manual via bot admin (delta={delta})"
        )
        await registar_auditoria(
            user_id, "AJUSTE_STOCK", "Produto",
            f"{p.nome}: delta={delta}, novo stock={p.stock_atual}"
        )
        await query.answer(f"Stock atualizado: {p.stock_atual} unidades.", show_alert=False)
        await responder(
            query, context,
            f"📊 *Stock — {p.nome}*\n\nStock atual: *{p.stock_atual}* unidades\n\nAjusta o stock:",
            teclado_stock_ajuste(p.id)
        )

    # ── Admin — Lista de encomendas ────────────────────────────────────────
    elif data == "admin_encomendas":
        if not is_admin(user_id):
            await query.answer("⛔ Acesso negado.", show_alert=True)
            return
        encomendas = await get_encomendas_ativas()
        if not encomendas:
            await responder(query, context, "📋 Sem encomendas activas de momento.", teclado_voltar_admin())
            return
        await responder(
            query, context,
            f"📋 *Encomendas Activas* ({len(encomendas)})\n\nEscolhe uma encomenda para gerir:",
            teclado_encomendas_admin(encomendas)
        )

    # ── Admin — Detalhe encomenda ──────────────────────────────────────────
    elif data.startswith("admin_ver_encomenda_"):
        if not is_admin(user_id):
            await query.answer("⛔ Acesso negado.", show_alert=True)
            return
        try:
            eid = sanitizar_id(data.split("_")[-1], campo="encomenda_id")
            encomenda = await get_encomenda_detalhe(eid)
        except (EntradaInvalida, Encomenda.DoesNotExist, ValueError):
            await responder(query, context, "❌ Encomenda não encontrada.", teclado_voltar_admin())
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
            teclado_acoes_encomenda(encomenda)
        )

    # ── Admin — Confirmar encomenda ────────────────────────────────────────
    elif data.startswith("admin_enc_confirmar_"):
        if not is_admin(user_id):
            await query.answer("⛔ Acesso negado.", show_alert=True)
            return
        try:
            eid = sanitizar_id(data.split("_")[-1], campo="encomenda_id")
        except EntradaInvalida:
            await query.answer("❌ ID inválido.", show_alert=True)
            return
        await _mudar_estado_e_notificar(query, context, user_id, eid, 'confirmada', 'CONFIRMAR')

    # ── Admin — Marcar enviada ─────────────────────────────────────────────
    elif data.startswith("admin_enc_enviada_"):
        if not is_admin(user_id):
            await query.answer("⛔ Acesso negado.", show_alert=True)
            return
        try:
            eid = sanitizar_id(data.split("_")[-1], campo="encomenda_id")
        except EntradaInvalida:
            await query.answer("❌ ID inválido.", show_alert=True)
            return
        await _mudar_estado_e_notificar(query, context, user_id, eid, 'enviada', 'ENVIAR')

    # ── Admin — Marcar entregue ────────────────────────────────────────────
    elif data.startswith("admin_enc_entregue_"):
        if not is_admin(user_id):
            await query.answer("⛔ Acesso negado.", show_alert=True)
            return
        try:
            eid = sanitizar_id(data.split("_")[-1], campo="encomenda_id")
        except EntradaInvalida:
            await query.answer("❌ ID inválido.", show_alert=True)
            return
        await _mudar_estado_e_notificar(query, context, user_id, eid, 'entregue', 'ENTREGAR')

    # ── Admin — Cancelar encomenda ─────────────────────────────────────────
    elif data.startswith("admin_enc_cancelar_"):
        if not is_admin(user_id):
            await query.answer("⛔ Acesso negado.", show_alert=True)
            return
        try:
            eid = sanitizar_id(data.split("_")[-1], campo="encomenda_id")
        except EntradaInvalida:
            await query.answer("❌ ID inválido.", show_alert=True)
            return
        await _mudar_estado_e_notificar(query, context, user_id, eid, 'cancelada', 'CANCELAR')

    # ── Admin — Gerir utilizadores ─────────────────────────────────────────
    elif data == "admin_utilizadores":
        if not is_admin(user_id):
            await query.answer("⛔ Acesso negado.", show_alert=True)
            return
        utilizadores = await get_todos_utilizadores()
        if not utilizadores:
            await responder(query, context, "👥 Sem utilizadores registados.", teclado_voltar_admin())
            return
        await responder(
            query, context,
            "👥 *Gestão de Utilizadores*\n✅ = ativo | 🚫 = bloqueado\n\nEscolhe um utilizador:",
            teclado_utilizadores_admin(utilizadores)
        )

    # ── Admin — Detalhe utilizador ─────────────────────────────────────────
    elif data.startswith("admin_utilizador_"):
        if not is_admin(user_id):
            await query.answer("⛔ Acesso negado.", show_alert=True)
            return
        tid = data[len("admin_utilizador_"):]
        try:
            u, novo_ativo = await toggle_utilizador_ativo(tid)
        except Utilizador.DoesNotExist:
            await responder(query, context, "❌ Utilizador não encontrado.", teclado_voltar_admin())
            return
        acao = "DESBLOQUEAR_UTILIZADOR" if novo_ativo else "BLOQUEAR_UTILIZADOR"
        await registar_auditoria(user_id, acao, "Utilizador", f"{u.nome} (telegram_id={tid})")
        estado_label = "✅ desbloqueado" if novo_ativo else "🚫 bloqueado"
        await responder(
            query, context,
            f"👤 *{u.nome}*\n\nO utilizador foi {estado_label}.",
            teclado_voltar_admin(), parse_mode=None
        )

    # ── Admin — Auditoria ──────────────────────────────────────────────────
    elif data == "admin_auditoria":
        if not is_admin(user_id):
            await query.answer("⛔ Acesso negado.", show_alert=True)
            return
        registos = await get_ultimos_registos_auditoria(10)
        if not registos:
            await responder(query, context, "📜 Sem registos de auditoria.", teclado_voltar_admin())
            return
        linhas = [
            f"🔹 `{r.data_hora.strftime('%d/%m %H:%M')}` — *{r.acao}* — {r.entidade}"
            for r in registos
        ]
        await responder(
            query, context,
            "📜 *Últimos Registos de Auditoria*\n\n" + "\n".join(linhas),
            teclado_voltar_admin()
        )


# ── Main ───────────────────────────────────────────────────────────────────

def main():
    if hasattr(asyncio, "WindowsSelectorEventLoopPolicy"):
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    app = ApplicationBuilder().token(os.getenv("TELEGRAM_TOKEN")).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("admin", admin_command))
    app.add_handler(CallbackQueryHandler(callback_handler))
    logger.info("🤖 Bot a correr... Pressiona Ctrl+C para parar.")
    try:
        app.run_polling(stop_signals=None)
    except KeyboardInterrupt:
        logger.info("Bot parado pelo utilizador (Ctrl+C).")


if __name__ == "__main__":
    main()
