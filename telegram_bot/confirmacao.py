"""Confirmation-screen token carried in the Confirm button (SEC-13).

Format: confirmar_encomenda_<nonce>_<ts>_<produto_id>
  nonce: 16 lowercase hex characters, stored on the order it creates
  ts:    the screen's creation time, Unix seconds in base36, zero-padded to 7
At the largest BigAutoField id this is exactly Telegram's 64-byte limit.
"""
import re
import secrets
import time
from typing import NamedTuple

from telegram.constants import InlineKeyboardButtonLimit

from security.sanitization import EntradaInvalida, sanitizar_id

PREFIXO = "confirmar_encomenda_"
VALIDADE_SEGUNDOS = 30 * 60
TOLERANCIA_FUTURO_SEGUNDOS = 60
_BASE36 = "0123456789abcdefghijklmnopqrstuvwxyz"
_TS_LARGURA = 7
_NONCE = re.compile(r"^[0-9a-f]{16}$")
_TS = re.compile(r"^[0-9a-z]{7}$")


class Confirmacao(NamedTuple):
    chave: str
    emitida_em: int
    produto_id: int


def agora() -> int:
    """Current time; tests patch this."""
    return int(time.time())


def gerar_chave() -> str:
    return secrets.token_hex(8)


def _base36(n: int) -> str:
    texto = ""
    while n:
        n, resto = divmod(n, 36)
        texto = _BASE36[resto] + texto
    if len(texto) > _TS_LARGURA:
        raise ValueError("timestamp não cabe em 7 caracteres base36")
    return texto.rjust(_TS_LARGURA, "0")


def callback_confirmar(produto_id: int, chave: str, emitida_em: int | None = None) -> str:
    ts = _base36(agora() if emitida_em is None else emitida_em)
    data = f"{PREFIXO}{chave}_{ts}_{produto_id}"
    if len(data.encode("utf-8")) > InlineKeyboardButtonLimit.MAX_CALLBACK_DATA:
        raise ValueError(f"callback_data com mais de 64 bytes: {data!r}")
    return data


def ler_callback(data: str):
    """Return ("token", Confirmacao), ("antigo", produto_id) or ("invalido", None)."""
    partes = data[len(PREFIXO):].split("_")
    try:
        if len(partes) == 3 and _NONCE.match(partes[0]) and _TS.match(partes[1]):
            return "token", Confirmacao(partes[0], int(partes[1], 36), sanitizar_id(partes[2], campo="produto_id"))
        if len(partes) == 1:
            return "antigo", sanitizar_id(partes[0], campo="produto_id")
    except EntradaInvalida:
        pass
    return "invalido", None


def expirada(emitida_em: int, momento: int | None = None) -> bool:
    idade = (agora() if momento is None else momento) - emitida_em
    return idade > VALIDADE_SEGUNDOS or idade < -TOLERANCIA_FUTURO_SEGUNDOS
