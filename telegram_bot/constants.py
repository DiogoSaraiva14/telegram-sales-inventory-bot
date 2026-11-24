"""Static labels and message templates shown to Telegram users."""

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
    'reembolsada': (
        "❌ *A tua encomenda foi cancelada.*\n\n"
        "📦 Encomenda #{id}\n"
        "💶 Total: {total:.2f}€\n\n"
        "O valor pago foi reembolsado na totalidade. Pode demorar alguns dias a aparecer na tua conta."
    ),
}

ESTADO_EMOJI = {
    'pendente': '🕐',
    'confirmada': '✅',
    'enviada': '🚚',
    'entregue': '📬',
    'cancelada': '❌',
}

# ── Checkout (SEC-13; owner decisions D7, D8, D14, D21) ───────────────────

MENSAGEM_ECRA_EXPIRADO = "Este ecrã expirou. Abre o produto novamente para continuar."
MENSAGEM_FALHA_REGISTO = "❌ Não foi possível registar a encomenda. Tenta novamente."
MENSAGEM_JA_REGISTADA = "Esta encomenda já foi registada."
MENSAGEM_PAGAMENTO_A_CONFIRMAR = (
    "⏳ O pagamento da encomenda #{id} está a ser confirmado. "
    "Não voltes a pagar. Receberás uma mensagem quando a confirmação chegar."
)
