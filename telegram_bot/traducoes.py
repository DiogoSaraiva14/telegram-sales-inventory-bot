"""Customer-facing texts in English. The Portuguese text is the key, so pt needs no table.

tr(idioma, texto) returns the English version for "en" and the text itself otherwise.
Translate the template before .format(), never the formatted result. Customer screens and
the Telegram admin panel are translated; stored data (product names, audit descriptions) is
not. A test checks that every text passed to tr() has an entry here.
"""
IDIOMAS = ("pt", "en")


def idioma_de_codigo(codigo) -> str:
    """Telegram's language_code → "pt" or "en". Unknown or missing: the shop's own language (pt)."""
    if not isinstance(codigo, str) or not codigo:
        return "pt"
    return "pt" if codigo.lower().startswith("pt") else "en"


def tr(idioma, texto):
    return EN.get(texto, texto) if idioma == "en" else texto


ESTADOS_EN = {
    'pendente': 'pending', 'confirmada': 'confirmed', 'enviada': 'shipped',
    'entregue': 'delivered', 'cancelada': 'cancelled',
}


def estado(idioma, valor):
    return ESTADOS_EN.get(valor, valor) if idioma == "en" else valor


EN = {
    # ── Notifications (constants.MENSAGENS_NOTIFICACAO) ──
    "✅ *Pagamento confirmado!*\n\n📦 Encomenda #{id}\n💶 Total: {total:.2f}€\n\n"
    "O teu pagamento foi recebido. Estamos a preparar o teu pedido! 🙏":
        "✅ *Payment confirmed!*\n\n📦 Order #{id}\n💶 Total: {total:.2f}€\n\n"
        "We received your payment and are preparing your order! 🙏",
    "✅ *A tua encomenda foi confirmada!*\n\n📦 Encomenda #{id}\n💶 Total: {total:.2f}€\n\n"
    "Estamos a preparar o teu pedido. Receberás uma notificação quando for enviado.":
        "✅ *Your order is confirmed!*\n\n📦 Order #{id}\n💶 Total: {total:.2f}€\n\n"
        "We are preparing your order. You'll get a message when it ships.",
    "🚚 *A tua encomenda foi enviada!*\n\n📦 Encomenda #{id}\n💶 Total: {total:.2f}€\n\n"
    "O teu pedido está a caminho. Aguarda a entrega em breve.":
        "🚚 *Your order has shipped!*\n\n📦 Order #{id}\n💶 Total: {total:.2f}€\n\n"
        "Your order is on its way and should arrive soon.",
    "📬 *A tua encomenda foi entregue!*\n\n📦 Encomenda #{id}\n💶 Total: {total:.2f}€\n\n"
    "Obrigado pela tua compra! Esperamos que fiques satisfeito. 🙏":
        "📬 *Your order was delivered!*\n\n📦 Order #{id}\n💶 Total: {total:.2f}€\n\n"
        "Thank you for your purchase! We hope you enjoy it. 🙏",
    "❌ *A tua encomenda foi cancelada.*\n\n📦 Encomenda #{id}\n💶 Total: {total:.2f}€\n\n"
    "Se não solicitaste este cancelamento, contacta o suporte.":
        "❌ *Your order was cancelled.*\n\n📦 Order #{id}\n💶 Total: {total:.2f}€\n\n"
        "If you didn't ask for this, please contact support.",
    "❌ *A tua encomenda foi cancelada.*\n\n📦 Encomenda #{id}\n💶 Total: {total:.2f}€\n\n"
    "O valor pago foi reembolsado na totalidade. Pode demorar alguns dias a aparecer na tua conta.":
        "❌ *Your order was cancelled.*\n\n📦 Order #{id}\n💶 Total: {total:.2f}€\n\n"
        "You've been refunded in full. It may take a few days to show in your account.",

    # ── Checkout and cart (constants) ──
    "Este menu já não está disponível. Abre o catálogo novamente.":
        "This menu is no longer available. Open the catalogue again.",
    "Este ecrã expirou. Abre o carrinho para continuar.": "This screen has expired. Open the cart to continue.",
    "❌ Pedido inválido.": "❌ Invalid request.",
    "Já tens uma encomenda por pagar (#{id}). "
    "Paga-a com o link que recebeste, ou espera que o prazo de pagamento termine.":
        "You already have an unpaid order (#{id}). "
        "Pay it with the link you received, or wait for the payment window to end.",
    "O prazo de pagamento desta encomenda terminou.": "The payment window for this order has ended.",
    "⚠️ Algo mudou desde que abriste este ecrã. Nada foi encomendado.":
        "⚠️ Something changed since you opened this screen. Nothing was ordered.",
    "⚠️ preço alterado: {antes:.2f}€ → {depois:.2f}€": "⚠️ price changed: {antes:.2f}€ → {depois:.2f}€",
    "⚠️ sem stock suficiente": "⚠️ not enough stock",
    "⚠️ indisponível": "⚠️ unavailable",
    "⚠️ o carrinho mudou depois de abrires este ecrã": "⚠️ the cart changed after you opened this screen",
    "🛒 O teu carrinho está vazio.": "🛒 Your cart is empty.",
    "🛒 Adicionado ao carrinho ({quantidade}).": "🛒 Added to cart ({quantidade}).",
    "🗑 Removido do carrinho.": "🗑 Removed from cart.",
    "Não há mais stock deste produto.": "There is no more stock of this product.",
    "Máximo de 99 unidades por produto.": "Maximum 99 units per product.",
    "O carrinho já tem 20 produtos diferentes.": "The cart already has 20 different products.",
    "❌ Este produto não está disponível.": "❌ This product is not available.",
    "Este produto já não está no carrinho.": "This product is no longer in the cart.",
    "🔁 Os produtos da encomenda foram juntos ao carrinho, aos preços atuais. Nada foi encomendado.":
        "🔁 The order's products were added to the cart at today's prices. Nothing was ordered.",
    "Com menos unidades (stock ou limite): {nomes}": "Fewer units (stock or limit): {nomes}",
    "Não adicionados (indisponíveis ou limite de 20 produtos): {nomes}":
        "Not added (unavailable or 20-product limit): {nomes}",
    "❌ Não foi possível registar a encomenda. Tenta novamente.": "❌ The order could not be saved. Please try again.",
    "Esta encomenda já foi registada.": "This order has already been placed.",
    "⏳ O pagamento da encomenda #{id} está a ser confirmado. "
    "Não voltes a pagar. Receberás uma mensagem quando a confirmação chegar.":
        "⏳ The payment for order #{id} is being confirmed. "
        "Don't pay again. You'll get a message when it is confirmed.",

    # ── Customer screens (handlers/customer.py) ──
    "O que pretendes fazer?": "What would you like to do?",
    "🛍️ *Catálogo*\nEscolhe uma categoria:": "🛍️ *Catalogue*\nChoose a category:",
    "😔 Sem produtos disponíveis em *{nome}*.": "😔 No products available in *{nome}*.",
    "{nome}\nEscolhe um produto:": "{nome}\nChoose a product:",
    "❌ Produto não encontrado.": "❌ Product not found.",
    "\n⚠️ _Último em stock!_": "\n⚠️ _Last one in stock!_",
    "📦 *{nome}*\n\n💶 Preço: *{preco:.2f}€*\n📦 Stock: {stock} unidades{descricao}{aviso}":
        "📦 *{nome}*\n\n💶 Price: *{preco:.2f}€*\n📦 Stock: {stock} units{descricao}{aviso}",
    "\n(Imagem indisponível)": "\n(Image unavailable)",
    "❌ Este produto não tem stock disponível.": "❌ This product is out of stock.",
    "📦 Ainda não tens encomendas.": "📦 You have no orders yet.",
    "🛒 *As Minhas Encomendas*\n\nEscolhe uma encomenda para ver detalhes:":
        "🛒 *My Orders*\n\nChoose an order to see its details:",
    "⛔ Acesso negado.": "⛔ Access denied.",
    "❌ Encomenda não encontrada.": "❌ Order not found.",
    "📋 *Encomenda #{id}*\n\n{emoji} Estado: *{estado}*\n💶 Total: *{total:.2f}€*\n📅 Data: {data}\n\n"
    "*Produtos:*\n{produtos}":
        "📋 *Order #{id}*\n\n{emoji} Status: *{estado}*\n💶 Total: *{total:.2f}€*\n📅 Date: {data}\n\n"
        "*Products:*\n{produtos}",
    "Produto": "Product",
    "🛒 Carrinho\n": "🛒 Cart\n",
    " ⚠️ só há {stock} em stock": " ⚠️ only {stock} in stock",
    "\nTotal: {total:.2f}€": "\nTotal: {total:.2f}€",
    " ⚠️ preço alterado": " ⚠️ price changed",
    "🛒 Confirmar encomenda\n\n{linhas}\n\nTotal: {total:.2f}€\n\nDesejas confirmar esta encomenda?":
        "🛒 Confirm order\n\n{linhas}\n\nTotal: {total:.2f}€\n\nDo you want to place this order?",
    "✅ *Encomenda #{id} criada!*\n\n⚠️ Não foi possível gerar o link de pagamento.\nContacta o suporte.":
        "✅ *Order #{id} created!*\n\n⚠️ The payment link could not be created.\nPlease contact support.",
    "✅ *Encomenda #{id} criada!*\n\n{linhas}\n\nTotal: *{total:.2f}€*\n\n"
    "Clica no botão abaixo para completar o pagamento.\nA encomenda é confirmada automaticamente após o pagamento. 💳":
        "✅ *Order #{id} created!*\n\n{linhas}\n\nTotal: *{total:.2f}€*\n\n"
        "Tap the button below to pay.\nThe order is confirmed automatically once you've paid. 💳",
    "ℹ️ *Ajuda*\n\nNavega usando os botões apresentados.\nUsa /start a qualquer momento para voltar ao menu principal.":
        "ℹ️ *Help*\n\nUse the buttons to move around.\nSend /start at any time to go back to the main menu.",

    # ── /start and guards ──
    "Bem-vindo! 👋": "Welcome! 👋",
    "Bem-vindo de volta! 👋": "Welcome back! 👋",
    "Olá, *{nome}*! {saudacao}\n\n*{loja}*\n{boas_vindas}": "Hi, *{nome}*! {saudacao}\n\n*{loja}*\n{boas_vindas}",
    "⏱️ A tua sessão expirou. Usa /start para recomeçar.": "⏱️ Your session has expired. Send /start to begin again.",
    "⏱️ *Sessão expirada.*\n\nUsa /start para voltar ao menu principal.":
        "⏱️ *Session expired.*\n\nSend /start to go back to the main menu.",
    "🚫 A tua conta foi suspensa.\nContacta o suporte se acreditas que isto é um erro.":
        "🚫 Your account has been suspended.\nContact support if you think this is a mistake.",
    "🚫 A tua conta está suspensa.": "🚫 Your account is suspended.",
    "🚫 *Conta suspensa.*\n\nContacta o suporte se acreditas que isto é um erro.":
        "🚫 *Account suspended.*\n\nContact support if you think this is a mistake.",
    "⚠️ Demasiados pedidos. Aguarda alguns segundos.": "⚠️ Too many requests. Please wait a few seconds.",
    "⚠️ Demasiados pedidos. Aguarda um momento.": "⚠️ Too many requests. Please wait a moment.",

    # ── Buttons (keyboards.py) ──
    "🔙 Menu Principal": "🔙 Main Menu",
    "🛍️ Ver Catálogo": "🛍️ Catalogue",
    "🛒 Carrinho": "🛒 Cart",
    "📦 As Minhas Encomendas": "📦 My Orders",
    "ℹ️ Ajuda": "ℹ️ Help",
    "🔐 Painel Admin": "🔐 Admin Panel",
    "🔙 Catálogo": "🔙 Catalogue",
    "🔙 As Minhas Encomendas": "🔙 My Orders",
    "🛒 Adicionar ao carrinho": "🛒 Add to cart",
    "❌ Sem Stock": "❌ Out of stock",
    "🛒 Ver carrinho": "🛒 View cart",
    "🔙 Voltar à Categoria": "🔙 Back to category",
    "✅ Finalizar compra": "✅ Checkout",
    "🛍️ Continuar a comprar": "🛍️ Keep shopping",
    "🔁 Repetir encomenda": "🔁 Order again",
    "✅ Confirmar": "✅ Confirm",
    "🔙 Voltar ao carrinho": "🔙 Back to cart",
    "📋 Ver encomenda": "📋 View order",
    "🔄 Tentar novamente": "🔄 Try again",
    "🛒 Ver Encomendas": "🛒 My Orders",
    "💳 Pagar Agora": "💳 Pay now",

    # ── Admin panel (handlers/admin.py, keyboards.py) ──
    "📦 *{nome}*\n\n💶 Preço: *{preco:.2f}€*\n📦 Stock: *{stock}* unidades\n📁 Categoria: {categoria}\nEstado: {estado}":
        "📦 *{nome}*\n\n💶 Price: *{preco:.2f}€*\n📦 Stock: *{stock}* units\n📁 Category: {categoria}\nStatus: {estado}",
    "✅ Ativo": "✅ Active",
    "❌ Inativo": "❌ Inactive",
    "📊 *Stock — {nome}*\n\nStock atual: *{stock}* unidades\n\nAjusta o stock:":
        "📊 *Stock — {nome}*\n\nCurrent stock: *{stock}* units\n\nAdjust the stock:",
    " · página {n}": " · page {n}",
    "🔐 *Painel Admin — {loja}*\n\nO que pretendes gerir?": "🔐 *Admin Panel — {loja}*\n\nWhat would you like to manage?",
    "📦 *Gestão de Produtos*": "📦 *Products*",
    "✅ = ativo | ❌ = inativo\n\nEscolhe um produto:": "✅ = active | ❌ = inactive\n\nChoose a product:",
    "📦 Sem produtos na base de dados.": "📦 No products in the database.",
    "Produto ✅ ativado.": "Product ✅ activated.",
    "Produto ❌ desativado.": "Product ❌ deactivated.",
    "📊 *Gestão de Stock*": "📊 *Stock*",
    "Escolhe um produto para ajustar:": "Choose a product to adjust:",
    "❌ Erro ao ajustar stock.": "❌ The stock could not be adjusted.",
    "Stock atualizado: {stock} unidades.": "Stock updated: {stock} units.",
    "📋 *Encomendas Activas*": "📋 *Active Orders*",
    "Mais antigas primeiro.\n\nEscolhe uma encomenda para gerir:": "Oldest first.\n\nChoose an order to manage:",
    "📋 Sem encomendas activas de momento.": "📋 No active orders right now.",
    "📋 *Encomenda #{id}*\n\n👤 Cliente: *{cliente}*\n📱 Telegram ID: `{telegram_id}`\n"
    "{emoji} Estado: *{estado}*\n📅 Data: {data}\n\n{linhas}\n\n💶 Total: *{total:.2f}€*":
        "📋 *Order #{id}*\n\n👤 Customer: *{cliente}*\n📱 Telegram ID: `{telegram_id}`\n"
        "{emoji} Status: *{estado}*\n📅 Date: {data}\n\n{linhas}\n\n💶 Total: *{total:.2f}€*",
    "O pagamento é reembolsado na totalidade e o stock volta ao inventário.":
        "The payment is refunded in full and the stock goes back to the inventory.",
    "O stock volta ao inventário.": "The stock goes back to the inventory.",
    "⚠️ Cancelar a encomenda #{id} ({total:.2f}€)?\n\n{aviso} O cliente recebe uma mensagem. Não é possível desfazer.":
        "⚠️ Cancel order #{id} ({total:.2f}€)?\n\n{aviso} The customer gets a message. This can't be undone.",
    "⚠️ O cliente acabou de pagar esta encomenda, por isso não foi cancelada. "
    "Aguarda a confirmação do pagamento e cancela de novo para a reembolsar.":
        "⚠️ The customer has just paid for this order, so it was not cancelled. "
        "Wait for the payment to be confirmed, then cancel again to refund it.",
    "❌ Não foi possível contactar a Stripe. A encomenda não foi alterada. Tenta novamente.":
        "❌ Stripe could not be reached. The order was not changed. Please try again.",
    "❌ Encomenda não encontrada ou estado inválido.": "❌ Order not found, or its status doesn't allow this.",
    "\n\n⚠️ _A notificação ainda não chegou ao cliente. É reenviada automaticamente durante 24 h; "
    "se falhar de vez, fica no registo de auditoria._":
        "\n\n⚠️ _The customer hasn't received the message yet. It is resent automatically for 24 h; "
        "if it fails for good, it is recorded in the audit log._",
    "{emoji} *Encomenda #{id}* marcada como *{estado}*.\n👤 Cliente: {cliente}\n💶 Total: {total:.2f}€"
    "\n📩 Notificação enviada: {enviada}":
        "{emoji} *Order #{id}* marked as *{estado}*.\n👤 Customer: {cliente}\n💶 Total: {total:.2f}€"
        "\n📩 Message sent: {enviada}",
    "✅ sim": "✅ yes",
    "⏳ em fila": "⏳ queued",
    "❌ ID inválido.": "❌ Invalid ID.",
    "👥 *Gestão de Utilizadores*": "👥 *Users*",
    "✅ = ativo | 🚫 = bloqueado\n\nEscolhe um utilizador:": "✅ = active | 🚫 = blocked\n\nChoose a user:",
    "👥 Sem utilizadores registados.": "👥 No registered users.",
    "⚠️ Não é possível bloquear um administrador (nem a ti próprio) a partir do Telegram. "
    "Retira primeiro os direitos de administrador no Django admin.":
        "⚠️ You can't block an admin (or yourself) from Telegram. "
        "Remove their admin rights in Django admin first.",
    "❌ Utilizador não encontrado.": "❌ User not found.",
    "👤 {nome}\n\nO utilizador foi ✅ desbloqueado.": "👤 {nome}\n\nThe user was ✅ unblocked.",
    "👤 {nome}\n\nO utilizador foi 🚫 bloqueado.": "👤 {nome}\n\nThe user was 🚫 blocked.",
    "📜 Sem registos de auditoria.": "📜 No audit records.",
    "📜 Não há registos mais antigos.": "📜 There are no older records.",
    "📜 Registos de auditoria · página {n}\n\n": "📜 Audit log · page {n}\n\n",
    "📦 Gerir Produtos": "📦 Products",
    "📊 Gerir Stock": "📊 Stock",
    "📋 Ver Encomendas": "📋 Orders",
    "👥 Gerir Utilizadores": "👥 Users",
    "📜 Auditoria": "📜 Audit log",
    "⬅️ Anterior": "⬅️ Previous",
    "Seguinte ➡️": "Next ➡️",
    "⬅️ Mais recentes": "⬅️ Newer",
    "Mais antigos ➡️": "Older ➡️",
    "❌ Desativar": "❌ Deactivate",
    "✅ Ativar": "✅ Activate",
    "📊 Ajustar Stock": "📊 Adjust stock",
    "🔙 Lista de Produtos": "🔙 Product list",
    "{nome} — {stock} un.": "{nome} — {stock} pcs",
    "🔙 Voltar ao Produto": "🔙 Back to product",
    "(sem username)": "(no username)",
    "❌ Cancelar": "❌ Cancel",
    "🚚 Marcar Enviada": "🚚 Mark shipped",
    "📬 Marcar Entregue": "📬 Mark delivered",
    "🔙 Lista de Encomendas": "🔙 Order list",
    "✅ Sim, cancelar": "✅ Yes, cancel",
    "🔙 Não, voltar": "🔙 No, go back",
    "📋 Lista de Encomendas": "📋 Order list",
    "👥 Lista de Utilizadores": "👥 User list",
    "🔙 Painel Admin": "🔙 Admin Panel",

    # ── Audit actions (constants.ROTULO_AUDITORIA) ──
    "🛒 Encomenda criada": "🛒 Order created",
    "💳 Pagamento confirmado": "💳 Payment confirmed",
    "⌛ Pagamento expirado": "⌛ Payment expired",
    "↩️ Pagamento reembolsado": "↩️ Payment refunded",
    "❌ Encomenda cancelada": "❌ Order cancelled",
    "⌛ Encomenda cancelada (prazo de pagamento)": "⌛ Order cancelled (payment window)",
    "🚚 Encomenda enviada": "🚚 Order shipped",
    "📬 Encomenda entregue": "📬 Order delivered",
    "🚚 Encomenda enviada (painel Telegram)": "🚚 Order shipped (Telegram panel)",
    "📬 Encomenda entregue (painel Telegram)": "📬 Order delivered (Telegram panel)",
    "❌ Encomenda cancelada (painel Telegram)": "❌ Order cancelled (Telegram panel)",
    "📊 Ajuste de stock": "📊 Stock adjustment",
    "✅ Produto ativado": "✅ Product activated",
    "🚫 Produto desativado": "🚫 Product deactivated",
    "🚫 Utilizador bloqueado": "🚫 User blocked",
    "📵 Notificação falhada": "📵 Message failed",
    "🗑️ Cliente anonimizado": "🗑️ Customer anonymised",
    "✅ Utilizador desbloqueado": "✅ User unblocked",
}
