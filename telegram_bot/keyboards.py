"""Inline keyboard builders. Callback values here must match security/sanitization.py."""
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from telegram_bot.constants import CATEGORIAS, ESTADO_EMOJI
from telegram_bot.guards import is_admin


# ── Navegação ──────────────────────────────────────────────────────────────

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


# ── Cliente ────────────────────────────────────────────────────────────────

def teclado_catalogo():
    keyboard = [
        [InlineKeyboardButton(label, callback_data=f"cat_{key}")]
        for key, label in CATEGORIAS.items()
    ]
    keyboard.append([InlineKeyboardButton("🔙 Menu Principal", callback_data="menu_inicio")])
    return InlineKeyboardMarkup(keyboard)

def teclado_produtos_categoria(produtos):
    keyboard = [
        [InlineKeyboardButton(f"{p.nome} — {p.preco:.2f}€", callback_data=f"produto_{p.id}")]
        for p in produtos
    ]
    keyboard.append([InlineKeyboardButton("🔙 Catálogo", callback_data="menu_catalogo")])
    return InlineKeyboardMarkup(keyboard)

def teclado_produto(produto):
    encomendar_label = "🛒 Encomendar" if produto.stock_atual > 0 else "❌ Sem Stock"
    encomendar_data  = f"encomendar_{produto.id}" if produto.stock_atual > 0 else "sem_stock"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(encomendar_label, callback_data=encomendar_data)],
        [InlineKeyboardButton("🔙 Voltar à Categoria", callback_data=f"cat_{produto.categoria}")],
    ])

def teclado_minhas_encomendas(encomendas):
    buttons = []
    for e in encomendas:
        emoji = ESTADO_EMOJI.get(e.estado, "📦")
        buttons.append([InlineKeyboardButton(
            f"{emoji} #{e.id} — {e.total:.2f}€ — {e.estado}",
            callback_data=f"ver_encomenda_{e.id}"
        )])
    buttons.append([InlineKeyboardButton("🔙 Menu Principal", callback_data="menu_inicio")])
    return InlineKeyboardMarkup(buttons)

def teclado_confirmar_encomenda(produto, callback_confirmar):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Confirmar", callback_data=callback_confirmar)],
        [InlineKeyboardButton("❌ Cancelar",  callback_data=f"produto_{produto.id}")],
    ])

def teclado_ecra_expirado(produto_id):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔙 Voltar ao Produto", callback_data=f"produto_{produto_id}")],
    ])

def teclado_falha_registo(callback_confirmar, categoria):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Tentar novamente", callback_data=callback_confirmar)],
        [InlineKeyboardButton("🔙 Voltar aos produtos", callback_data=f"cat_{categoria}")],
    ])

def teclado_falha_pagamento(callback_confirmar):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Tentar novamente", callback_data=callback_confirmar)],
        [InlineKeyboardButton("🛒 Ver Encomendas", callback_data="menu_encomendas")],
    ])

def teclado_pagamento(payment_url):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💳 Pagar Agora", url=payment_url)],
        [InlineKeyboardButton("🛒 Ver Encomendas", callback_data="menu_encomendas")],
    ])

def teclado_ver_encomendas():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🛒 Ver Encomendas", callback_data="menu_encomendas")],
    ])


# ── Admin ──────────────────────────────────────────────────────────────────

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

def teclado_stock_lista(produtos):
    buttons = [
        [InlineKeyboardButton(f"{p.nome} — {p.stock_atual} un.",
                              callback_data=f"admin_stock_produto_{p.id}")]
        for p in produtos
    ]
    buttons.append([InlineKeyboardButton("🔙 Painel Admin", callback_data="admin_menu")])
    return InlineKeyboardMarkup(buttons)

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
