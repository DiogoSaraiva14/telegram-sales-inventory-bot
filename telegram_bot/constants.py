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
}

ESTADO_EMOJI = {
    'pendente': '🕐',
    'confirmada': '✅',
    'enviada': '🚚',
    'entregue': '📬',
    'cancelada': '❌',
}
