import hashlib
import hmac
import re
import logging

from django.conf import settings

logger = logging.getLogger(__name__)


class EntradaInvalida(Exception):
    pass


def pseudonimo(telegram_id) -> str:
    """Log label for a Telegram user (SEC-08): stable, but not reversible without SECRET_KEY.

    Keyed, because Telegram ids are short numbers that a plain hash would not hide.
    Rotating SECRET_KEY changes every label.
    """
    return hmac.new(settings.SECRET_KEY.encode(), str(telegram_id).encode(), hashlib.sha256).hexdigest()[:12]


# ── Allowed callback exact values ──────────────────────────────────────────

ALLOWED_EXACT = {
    "menu_inicio", "menu_catalogo", "menu_encomendas", "menu_ajuda",
    "sem_stock",
    "admin_menu", "admin_produtos", "admin_stock",
    "admin_encomendas", "admin_utilizadores", "admin_auditoria",
}

# ── Allowed callback prefixes ──────────────────────────────────────────────

ALLOWED_PREFIXES = (
    "cat_",
    "produto_",
    "encomendar_",
    "confirmar_encomenda_",
    "cancelar_encomenda_",
    "ver_encomenda_",
    # admin
    "admin_produto_detalhe_",
    "admin_produto_toggle_",
    "admin_stock_produto_",
    "admin_stock_adj_",
    "admin_ver_encomenda_",
    "admin_enc_confirmar_",
    "admin_enc_enviada_",
    "admin_enc_entregue_",
    "admin_enc_cancelar_",
    "admin_utilizador_",
)


def validar_callback(data: str) -> str:
    if not isinstance(data, str):
        raise EntradaInvalida("callback_data não é string")
    data = data.strip()
    if len(data) > 200:
        raise EntradaInvalida("callback_data demasiado longo")
    if data in ALLOWED_EXACT:
        return data
    for prefix in ALLOWED_PREFIXES:
        if data.startswith(prefix):
            return data
    raise EntradaInvalida(f"callback_data desconhecido ou inválido: {data!r}")


def validar_telegram_id(value) -> str:
    try:
        tid = int(value)
    except (ValueError, TypeError):
        raise EntradaInvalida(f"telegram_id inválido: {value!r}")
    if tid <= 0:
        raise EntradaInvalida(f"telegram_id deve ser positivo: {tid}")
    return str(tid)


def sanitizar_id(value, campo: str = "id") -> int:
    try:
        v = int(value)
    except (ValueError, TypeError):
        raise EntradaInvalida(f"{campo} inválido: {value!r}")
    if v <= 0:
        raise EntradaInvalida(f"{campo} deve ser positivo: {v}")
    return v


def sanitizar_delta_stock(value) -> int:
    try:
        delta = int(value)
    except (ValueError, TypeError):
        raise EntradaInvalida(f"delta_stock inválido: {value!r}")
    if delta not in (-10, -5, -1, 1, 5, 10):
        raise EntradaInvalida(f"delta_stock fora dos valores permitidos: {delta}")
    return delta


def sanitizar_nome_utilizador(nome: str) -> str:
    if not isinstance(nome, str):
        return "Utilizador"
    nome = nome.strip()
    nome = re.sub(r"[<>\"\'%;()&+]", "", nome)
    return nome[:150] if nome else "Utilizador"
