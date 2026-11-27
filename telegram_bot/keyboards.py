"""Inline keyboard builders. Callback values here must match security/sanitization.py.

Keyboards take the user's bot language (idioma); "pt" by default.
"""
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from telegram_bot.constants import ESTADO_EMOJI
from telegram_bot.traducoes import estado, tr


def _botao(idioma, texto, **kwargs):
    return InlineKeyboardButton(tr(idioma, texto), **kwargs)


def _menu(idioma="pt"):
    return [_botao(idioma, "🔙 Menu Principal", callback_data="menu_inicio")]


# ── Navegação ──────────────────────────────────────────────────────────────

def teclado_menu_principal(admin: bool = False, idioma="pt"):
    """admin: the caller's `await services.e_admin(user_id)`."""
    buttons = [
        [_botao(idioma, "🛍️ Ver Catálogo", callback_data="menu_catalogo")],
        [_botao(idioma, "🛒 Carrinho", callback_data="carrinho_ver")],
        [_botao(idioma, "📦 As Minhas Encomendas", callback_data="menu_encomendas")],
        [_botao(idioma, "ℹ️ Ajuda", callback_data="menu_ajuda")],
        # Each label is in its own language, so a customer who can't read the other one finds it.
        [InlineKeyboardButton("🌐 Português", callback_data="idioma_pt") if idioma == "en"
         else InlineKeyboardButton("🌐 English", callback_data="idioma_en")],
    ]
    if admin:
        buttons.append([_botao(idioma, "🔐 Painel Admin", callback_data="admin_menu")])
    return InlineKeyboardMarkup(buttons)

def teclado_voltar_menu(idioma="pt"):
    return InlineKeyboardMarkup([_menu(idioma)])

def teclado_voltar_catalogo(idioma="pt"):
    return InlineKeyboardMarkup([[_botao(idioma, "🔙 Catálogo", callback_data="menu_catalogo")]])

def teclado_voltar_admin(idioma="pt"):
    return InlineKeyboardMarkup([[_botao(idioma, "🔙 Painel Admin", callback_data="admin_menu")]])

def teclado_voltar_encomendas(idioma="pt"):
    return InlineKeyboardMarkup([[_botao(idioma, "🔙 As Minhas Encomendas", callback_data="menu_encomendas")]])


# ── Cliente ────────────────────────────────────────────────────────────────

def teclado_catalogo(categorias, idioma="pt"):
    keyboard = [
        [InlineKeyboardButton(c.nome, callback_data=f"cat_{c.id}")]
        for c in categorias
    ]
    keyboard.append(_menu(idioma))
    return InlineKeyboardMarkup(keyboard)

def teclado_produtos_categoria(produtos, idioma="pt"):
    keyboard = [
        [InlineKeyboardButton(f"{p.nome} — {p.preco:.2f}€", callback_data=f"produto_{p.id}")]
        for p in produtos
    ]
    keyboard.append([_botao(idioma, "🔙 Catálogo", callback_data="menu_catalogo")])
    return InlineKeyboardMarkup(keyboard)

def teclado_produto(produto, idioma="pt"):
    adicionar_label = "🛒 Adicionar ao carrinho" if produto.stock_atual > 0 else "❌ Sem Stock"
    adicionar_data  = f"carrinho_add_{produto.id}" if produto.stock_atual > 0 else "sem_stock"
    return InlineKeyboardMarkup([
        [_botao(idioma, adicionar_label, callback_data=adicionar_data)],
        [_botao(idioma, "🛒 Ver carrinho", callback_data="carrinho_ver")],
        [_botao(idioma, "🔙 Voltar à Categoria", callback_data=f"cat_{produto.categoria_id}")],
    ])

def teclado_carrinho(linhas, idioma="pt"):
    """+ / − / 🗑 per line; "Finalizar compra" only when no line blocks checkout."""
    buttons = [[
        InlineKeyboardButton(f"➖ {l.nome[:18]}", callback_data=f"carrinho_menos_{l.produto_id}"),
        InlineKeyboardButton("➕", callback_data=f"carrinho_mais_{l.produto_id}"),
        InlineKeyboardButton("🗑", callback_data=f"carrinho_remover_{l.produto_id}"),
    ] for l in linhas]
    if linhas and not any(l.bloqueia for l in linhas):
        buttons.append([_botao(idioma, "✅ Finalizar compra", callback_data="carrinho_finalizar")])
    buttons.append([_botao(idioma, "🛍️ Continuar a comprar", callback_data="menu_catalogo")])
    buttons.append(_menu(idioma))
    return InlineKeyboardMarkup(buttons)

def teclado_detalhe_encomenda(encomenda_id, repetir=False, idioma="pt"):
    buttons = []
    if repetir:
        buttons.append([_botao(idioma, "🔁 Repetir encomenda", callback_data=f"repetir_encomenda_{encomenda_id}")])
    buttons.append([_botao(idioma, "🔙 As Minhas Encomendas", callback_data="menu_encomendas")])
    return InlineKeyboardMarkup(buttons)

def teclado_minhas_encomendas(encomendas, idioma="pt"):
    buttons = []
    for e in encomendas:
        emoji = ESTADO_EMOJI.get(e.estado, "📦")
        buttons.append([InlineKeyboardButton(
            f"{emoji} #{e.id} — {e.total:.2f}€ — {estado(idioma, e.estado)}",
            callback_data=f"ver_encomenda_{e.id}"
        )])
    buttons.append(_menu(idioma))
    return InlineKeyboardMarkup(buttons)


def teclado_confirmar_encomenda(callback_confirmar, idioma="pt"):
    return InlineKeyboardMarkup([
        [_botao(idioma, "✅ Confirmar", callback_data=callback_confirmar)],
        [_botao(idioma, "🔙 Voltar ao carrinho", callback_data="carrinho_ver")],
    ])

def teclado_ecra_expirado(idioma="pt"):
    return InlineKeyboardMarkup([
        [_botao(idioma, "🛒 Ver carrinho", callback_data="carrinho_ver")],
        _menu(idioma),
    ])

def teclado_ver_encomenda(encomenda_id, idioma="pt"):
    return InlineKeyboardMarkup([
        [_botao(idioma, "📋 Ver encomenda", callback_data=f"ver_encomenda_{encomenda_id}")],
        _menu(idioma),
    ])

def teclado_falha_registo(callback_confirmar, idioma="pt"):
    return InlineKeyboardMarkup([
        [_botao(idioma, "🔄 Tentar novamente", callback_data=callback_confirmar)],
        _menu(idioma),
    ])

def teclado_falha_pagamento(callback_confirmar, idioma="pt"):
    return InlineKeyboardMarkup([
        [_botao(idioma, "🔄 Tentar novamente", callback_data=callback_confirmar)],
        [_botao(idioma, "🛒 Ver Encomendas", callback_data="menu_encomendas")],
    ])

def teclado_pagamento(payment_url, idioma="pt"):
    return InlineKeyboardMarkup([
        [_botao(idioma, "💳 Pagar Agora", url=payment_url)],
        [_botao(idioma, "🛒 Ver Encomendas", callback_data="menu_encomendas")],
    ])

def teclado_ver_encomendas(idioma="pt"):
    return InlineKeyboardMarkup([
        [_botao(idioma, "🛒 Ver Encomendas", callback_data="menu_encomendas")],
    ])


# ── Admin ──────────────────────────────────────────────────────────────────

def teclado_admin_menu(idioma="pt"):
    return InlineKeyboardMarkup([
        [_botao(idioma, "📦 Gerir Produtos", callback_data="admin_produtos")],
        [_botao(idioma, "📊 Gerir Stock", callback_data="admin_stock")],
        [_botao(idioma, "📋 Ver Encomendas", callback_data="admin_encomendas")],
        [_botao(idioma, "👥 Gerir Utilizadores", callback_data="admin_utilizadores")],
        [_botao(idioma, "📜 Auditoria", callback_data="admin_auditoria")],
        _menu(idioma),
    ])

def _com_paginas(buttons, prefixo, pagina, ha_mais, idioma="pt", anterior="⬅️ Anterior", seguinte="Seguinte ➡️"):
    """Add the page buttons (prefixo + page number) and the back button to an admin list."""
    navegacao = []
    if pagina > 0:
        navegacao.append(_botao(idioma, anterior, callback_data=f"{prefixo}{pagina - 1}"))
    if ha_mais:
        navegacao.append(_botao(idioma, seguinte, callback_data=f"{prefixo}{pagina + 1}"))
    if navegacao:
        buttons.append(navegacao)
    buttons.append([_botao(idioma, "🔙 Painel Admin", callback_data="admin_menu")])
    return InlineKeyboardMarkup(buttons)

def teclado_auditoria(pagina, ha_mais_antigos, idioma="pt"):
    return _com_paginas([], "admin_auditoria_p_", pagina, ha_mais_antigos, idioma,
                        tr(idioma, "⬅️ Mais recentes"), tr(idioma, "Mais antigos ➡️"))

def teclado_produtos_admin(produtos, pagina=0, ha_mais=False, idioma="pt"):
    buttons = []
    for p in produtos:
        estado_p = "✅" if p.ativo else "❌"
        buttons.append([InlineKeyboardButton(
            f"{estado_p} {p.nome} — {p.preco:.2f}€",
            callback_data=f"admin_produto_detalhe_{p.id}"
        )])
    return _com_paginas(buttons, "admin_produtos_p_", pagina, ha_mais, idioma)

def teclado_produto_acao(produto, idioma="pt"):
    toggle_label = "❌ Desativar" if produto.ativo else "✅ Ativar"
    return InlineKeyboardMarkup([
        [_botao(idioma, toggle_label, callback_data=f"admin_produto_toggle_{produto.id}")],
        [_botao(idioma, "📊 Ajustar Stock", callback_data=f"admin_stock_produto_{produto.id}")],
        [_botao(idioma, "🔙 Lista de Produtos", callback_data="admin_produtos")],
    ])

def teclado_stock_lista(produtos, pagina=0, ha_mais=False, idioma="pt"):
    buttons = [
        [InlineKeyboardButton(tr(idioma, "{nome} — {stock} un.").format(nome=p.nome, stock=p.stock_atual),
                              callback_data=f"admin_stock_produto_{p.id}")]
        for p in produtos
    ]
    return _com_paginas(buttons, "admin_stock_p_", pagina, ha_mais, idioma)

def teclado_stock_ajuste(produto_id, idioma="pt"):
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
        [_botao(idioma, "🔙 Voltar ao Produto", callback_data=f"admin_produto_detalhe_{produto_id}")],
    ])

def teclado_utilizadores_admin(utilizadores, pagina=0, ha_mais=False, idioma="pt"):
    buttons = []
    for u in utilizadores:
        estado_u = "✅" if u.ativo else "🚫"
        buttons.append([InlineKeyboardButton(
            f"{estado_u} {u.nome} " + (f"(@{u.username})" if u.username else tr(idioma, "(sem username)")),
            callback_data=f"admin_utilizador_{u.telegram_id}"
        )])
    return _com_paginas(buttons, "admin_utilizadores_p_", pagina, ha_mais, idioma)

def teclado_encomendas_admin(encomendas, pagina=0, ha_mais=False, idioma="pt"):
    buttons = []
    for e in encomendas:
        emoji = ESTADO_EMOJI.get(e.estado, '📦')
        buttons.append([InlineKeyboardButton(
            f"{emoji} #{e.id} — {e.utilizador.nome} — {e.total:.2f}€ [{estado(idioma, e.estado)}]",
            callback_data=f"admin_ver_encomenda_{e.id}"
        )])
    return _com_paginas(buttons, "admin_encomendas_p_", pagina, ha_mais, idioma)

def teclado_acoes_encomenda(encomenda, idioma="pt"):
    buttons = []
    estado_e = encomenda.estado
    cancelar = _botao(idioma, "❌ Cancelar", callback_data=f"admin_enc_pedir_cancelar_{encomenda.id}")
    if estado_e == 'pendente':
        buttons.append([cancelar])
    elif estado_e == 'confirmada':
        buttons.append([_botao(idioma, "🚚 Marcar Enviada", callback_data=f"admin_enc_enviada_{encomenda.id}")])
        buttons.append([cancelar])
    elif estado_e == 'enviada':
        buttons.append([_botao(idioma, "📬 Marcar Entregue", callback_data=f"admin_enc_entregue_{encomenda.id}")])
    buttons.append([_botao(idioma, "🔙 Lista de Encomendas", callback_data="admin_encomendas")])
    return InlineKeyboardMarkup(buttons)

def teclado_confirmar_cancelamento(encomenda_id, idioma="pt"):
    return InlineKeyboardMarkup([
        [_botao(idioma, "✅ Sim, cancelar", callback_data=f"admin_enc_cancelar_{encomenda_id}")],
        [_botao(idioma, "🔙 Não, voltar", callback_data=f"admin_ver_encomenda_{encomenda_id}")],
    ])

def teclado_voltar_encomendas_admin(idioma="pt"):
    return InlineKeyboardMarkup([
        [_botao(idioma, "📋 Lista de Encomendas", callback_data="admin_encomendas")],
        [_botao(idioma, "🔙 Painel Admin", callback_data="admin_menu")],
    ])

def teclado_voltar_utilizadores(idioma="pt"):
    return InlineKeyboardMarkup([
        [_botao(idioma, "👥 Lista de Utilizadores", callback_data="admin_utilizadores")],
        [_botao(idioma, "🔙 Painel Admin", callback_data="admin_menu")],
    ])
