"""Confirmation-screen token carried in the Confirm button (SEC-13, cart design Stage 2).

Format: confirmar_encomenda_<nonce>
  nonce: the 16 lowercase hex characters of a stored ConfirmacaoCarrinho (36 bytes in all).
The screen's age is checked on the server against the snapshot's criada_em, not from
the button. Buttons drawn before Stage 2 carry confirmar_encomenda_<nonce>_<ts>_<produto_id>;
their nonce is only used to find an order that already exists.
"""
import re

from telegram.constants import InlineKeyboardButtonLimit

PREFIXO = "confirmar_encomenda_"
_NONCE = re.compile(r"^[0-9a-f]{16}$")
_TS = re.compile(r"^[0-9a-z]{7}$")


def callback_confirmar(nonce: str) -> str:
    data = f"{PREFIXO}{nonce}"
    if not _NONCE.match(nonce) or len(data.encode("utf-8")) > InlineKeyboardButtonLimit.MAX_CALLBACK_DATA:
        raise ValueError(f"callback_data inválido: {data!r}")
    return data


def ler_callback(data: str):
    """Return ("snapshot", nonce), ("antigo", nonce) or ("invalido", None)."""
    resto = data[len(PREFIXO):]
    if _NONCE.match(resto):
        return "snapshot", resto
    partes = resto.split("_")
    if len(partes) == 3 and _NONCE.match(partes[0]) and _TS.match(partes[1]) and partes[2].isdecimal():
        return "antigo", partes[0]
    return "invalido", None
