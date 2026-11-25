"""PostgreSQL tests for checkout, payments, cancellation and the cart design stages.

Run with: python manage.py test core   (needs the telegram_bot_test role, see README).
"""
import importlib
import threading
import time
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import stripe
from asgiref.sync import async_to_sync, sync_to_async
from django.db import DatabaseError, IntegrityError, connection, migrations, transaction
from django.db.migrations.executor import MigrationExecutor
from django.db.models import ProtectedError
from django.db.models.query import QuerySet
from django.forms.models import model_to_dict
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone

from core import services
from core.models import (
    Carrinho, Categoria, ConfirmacaoCarrinho, Encomenda, LinhaCarrinho, LinhaEncomenda, Loja, MovimentoStock, Pagamento, Produto, RegistoAuditoria, Utilizador,
)
from telegram_bot import confirmacao
from telegram_bot.constants import MENSAGEM_JA_REGISTADA, MENSAGEM_PRAZO_TERMINADO
from telegram_bot.handlers import callbacks, customer

URLS_A = dict(STRIPE_SUCCESS_URL="https://loja.example/sucesso", STRIPE_CANCEL_URL="https://loja.example/cancelar")
URLS_B = dict(STRIPE_SUCCESS_URL="https://loja.example/v2/sucesso", STRIPE_CANCEL_URL="https://loja.example/v2/cancelar")


def utilizador_telegram(tid=1001, nome="Cliente Teste"):
    return SimpleNamespace(id=tid, full_name=nome, username="cliente")


def criar_produto(stock=5, **campos):
    if "categoria" not in campos:
        campos["categoria"] = Categoria.objects.get_or_create(nome="Armazenamento")[0]
    return Produto.objects.create(
        nome=campos.pop("nome", "SSD 1TB"), preco=campos.pop("preco", Decimal("50.00")),
        stock_atual=stock, **campos,
    )


def sessao(id_, status="open"):
    return SimpleNamespace(id=id_, url=f"https://checkout.example/{id_}", status=status, payment_status="unpaid")


def contagens():
    return (Encomenda.objects.count(), LinhaEncomenda.objects.count(), MovimentoStock.objects.count(),
            RegistoAuditoria.objects.filter(acao="CRIAR_ENCOMENDA").count())


def ecra(cliente, *linhas):
    """A saved confirmation screen for (produto, quantidade) pairs."""
    resultado, snap = services.criar_confirmacao(cliente, [(p.id, q) for p, q in linhas])
    assert resultado == "ok", snap
    return snap


def reservar(cliente, produto_id, quantidade=1):
    """Create an order the Stage 2 way: a one-line confirmation screen, then Confirm."""
    resultado, snap = services.criar_confirmacao(cliente, [(produto_id, quantidade)])
    assert resultado == "ok", snap
    return services.criar_encomenda_de_confirmacao(cliente, snap.nonce)


def envelhecer(modelo, pk, campo, **idade):
    modelo.objects.filter(pk=pk).update(**{campo: timezone.now() - timedelta(**idade)})


# ── criar_encomenda_de_confirmacao (DB1–DB16) ──────────────────────────────

class CriarEncomendaTests(TestCase):

    def setUp(self):
        self.produto = criar_produto(stock=5)
        self.cliente = utilizador_telegram()

    def stock(self, produto=None):
        return Produto.objects.get(pk=(produto or self.produto).pk).stock_atual

    def confirmar(self, snap, cliente=None):
        return services.criar_encomenda_de_confirmacao(cliente or self.cliente, snap.nonce)

    def test_db1_same_screen_twice_creates_one_order(self):
        snap = ecra(self.cliente, (self.produto, 1))
        r1, e1 = self.confirmar(snap)
        r2, e2 = self.confirmar(snap)
        self.assertEqual((r1, r2), ("criada", "existente"))
        self.assertEqual(e1.id, e2.id)
        self.assertEqual(contagens(), (1, 1, 1, 1))
        self.assertEqual(MovimentoStock.objects.get().tipo_movimento, "saida")
        self.assertEqual(self.stock(), 4)
        encomenda = Encomenda.objects.get()
        self.assertEqual((encomenda.confirmacao_id, encomenda.chave_confirmacao), (snap.pk, snap.nonce))

    def test_db2_constraint_maps_a_racing_pending_order_to_pendente(self):
        _, primeira = reservar(self.cliente, self.produto.id)
        snap = ecra(self.cliente, (self.produto, 1))
        real = services._pendente_de
        chamadas = []

        def falha_uma_vez(utilizador_id):
            chamadas.append(utilizador_id)
            return None if len(chamadas) == 1 else real(utilizador_id)

        with patch.object(services, "_pendente_de", side_effect=falha_uma_vez):
            resultado, encomenda = self.confirmar(snap)
        self.assertEqual((resultado, encomenda.id), ("pendente", primeira.id))
        self.assertEqual(len(chamadas), 2)  # the miss, then the read after the unique violation
        self.assertEqual(contagens(), (1, 1, 1, 1))
        self.assertEqual(self.stock(), 4)

    def test_db3_screen_of_other_user_reveals_and_creates_nothing(self):
        outro = utilizador_telegram(2002, "Outra")
        snap = ecra(self.cliente, (self.produto, 1))
        self.assertEqual(self.confirmar(snap, outro), ("de_outro_utilizador", None))
        self.assertEqual(contagens(), (0, 0, 0, 0))
        self.confirmar(snap)
        self.assertEqual(self.confirmar(snap, outro), ("de_outro_utilizador", None))
        self.assertEqual(contagens(), (1, 1, 1, 1))

    def test_db4_second_screen_gets_the_pending_order(self):
        primeiro, segundo = ecra(self.cliente, (self.produto, 1)), ecra(self.cliente, (self.produto, 1))
        _, encomenda = self.confirmar(primeiro)
        self.assertEqual(self.confirmar(segundo), ("pendente", encomenda))
        self.assertEqual(contagens(), (1, 1, 1, 1))
        self.assertEqual(self.stock(), 4)

    def test_db5_short_stock_gives_alterado_without_writing(self):
        snap = ecra(self.cliente, (self.produto, 2))
        Produto.objects.filter(pk=self.produto.pk).update(stock_atual=1)
        self.assertEqual(self.confirmar(snap),
                         ("alterado", [services.Alteracao(self.produto.id, "SSD 1TB", "stock", 2, 1)]))
        self.assertEqual(contagens(), (0, 0, 0, 0))
        self.assertEqual(self.stock(), 1)

    def test_db6_failed_audit_rolls_back_everything(self):
        snap = ecra(self.cliente, (self.produto, 1))
        with patch.object(RegistoAuditoria.objects, "create", side_effect=DatabaseError("audit down")):
            with self.assertRaises(DatabaseError):
                self.confirmar(snap)
        self.assertEqual(contagens(), (0, 0, 0, 0))
        self.assertEqual(self.stock(), 5)

    def test_db7_missing_image_file_does_not_break_checkout(self):
        Produto.objects.filter(pk=self.produto.pk).update(imagem="produtos/em-falta.jpg")
        resultado, _ = reservar(self.cliente, self.produto.id)
        self.assertEqual(resultado, "criada")
        self.assertEqual(self.stock(), 4)

    def test_db8_unique_constraint_allows_many_nulls_but_not_duplicates(self):
        utilizador = Utilizador.objects.create(telegram_id="3003", nome="Legado")
        Encomenda.objects.create(utilizador=utilizador, total=1)
        Encomenda.objects.create(utilizador=utilizador, total=1)
        Encomenda.objects.create(utilizador=utilizador, total=1, chave_confirmacao="ab" * 8)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Encomenda.objects.create(utilizador=utilizador, total=1, chave_confirmacao="ab" * 8)
        self.assertEqual(Encomenda.objects.filter(chave_confirmacao__isnull=True).count(), 2)

    def test_db10_retry_after_failed_attempt_creates_exactly_one(self):
        snap = ecra(self.cliente, (self.produto, 1))
        real = RegistoAuditoria.objects.create
        falhas = []

        def falha_uma_vez(**campos):
            if not falhas:
                falhas.append(1)
                raise DatabaseError("audit down")
            return real(**campos)

        with patch.object(RegistoAuditoria.objects, "create", side_effect=falha_uma_vez):
            with self.assertRaises(DatabaseError):
                self.confirmar(snap)
            resultado, _ = self.confirmar(snap)
        self.assertEqual(resultado, "criada")
        self.assertEqual(contagens(), (1, 1, 1, 1))

    def test_db11_line_keeps_the_name_shown_on_the_screen(self):
        snap = ecra(self.cliente, (self.produto, 1))
        Produto.objects.filter(pk=self.produto.pk).update(nome="SSD 1TB (novo nome)")
        _, encomenda = self.confirmar(snap)
        self.assertEqual(encomenda.linhas.get().nome_produto, "SSD 1TB")

    def test_db12_multi_line_order_takes_every_line(self):
        outro = criar_produto(nome="RAM 16GB", preco=Decimal("39.99"), stock=3)
        snap = ecra(self.cliente, (outro, 1), (self.produto, 2))
        self.assertEqual(snap.total_centimos, 13999)
        resultado, encomenda = self.confirmar(snap)
        self.assertEqual((resultado, encomenda.total), ("criada", Decimal("139.99")))
        linhas = list(encomenda.linhas.order_by("pk").values_list("produto_id", "quantidade", "subtotal"))
        self.assertEqual(linhas, [(self.produto.id, 2, Decimal("100.00")), (outro.id, 1, Decimal("39.99"))])
        self.assertEqual((self.stock(), self.stock(outro)), (3, 2))
        self.assertEqual(MovimentoStock.objects.filter(tipo_movimento="saida").count(), 2)
        descricao = RegistoAuditoria.objects.get(acao="CRIAR_ENCOMENDA").descricao
        self.assertIn("2× SSD 1TB @ 50.00€; 1× RAM 16GB @ 39.99€", descricao)

    def test_db13_each_change_gives_alterado_and_writes_nothing(self):
        mudancas = {
            "preco": lambda p: Produto.objects.filter(pk=p.pk).update(preco=Decimal("54.99")),
            "inativo": lambda p: Produto.objects.filter(pk=p.pk).update(ativo=False),
            "removido": lambda p: p.delete(),
        }
        for tipo, mudar in mudancas.items():
            with self.subTest(tipo):
                produto = criar_produto(nome=f"P {tipo}")
                snap = ecra(self.cliente, (produto, 1))
                mudar(produto)
                resultado, alteracoes = self.confirmar(snap)
                self.assertEqual((resultado, [a.tipo for a in alteracoes]), ("alterado", [tipo]))
        self.assertEqual(alteracoes[0].nome, "P removido")
        self.assertEqual(contagens(), (0, 0, 0, 0))

    def test_db14_old_screen_expires_but_its_order_still_wins(self):
        velho = ecra(self.cliente, (self.produto, 1))
        envelhecer(ConfirmacaoCarrinho, velho.pk, "criada_em", minutes=31)
        self.assertEqual(self.confirmar(velho), ("expirada", None))
        snap = ecra(self.cliente, (self.produto, 1))
        _, encomenda = self.confirmar(snap)
        envelhecer(ConfirmacaoCarrinho, snap.pk, "criada_em", hours=2)
        self.assertEqual(self.confirmar(snap), ("existente", encomenda))
        self.assertEqual(self.confirmar(SimpleNamespace(nonce="0" * 16)), ("invalida", None))

    def test_db15_legacy_pending_order_blocks_checkout_until_settled(self):
        snap = ecra(self.cliente, (self.produto, 1))
        utilizador = Utilizador.objects.get(telegram_id="1001")
        legado = Encomenda.objects.create(utilizador=utilizador, total=Decimal("50.00"))  # pre-cart: no snapshot
        self.assertEqual(self.confirmar(snap), ("pendente", legado))
        self.assertEqual((contagens(), self.stock()), ((1, 0, 0, 0), 5))
        Encomenda.objects.filter(pk=legado.pk).update(estado="cancelada")
        self.assertEqual(self.confirmar(snap)[0], "criada")

    def test_db16_screen_is_immutable_and_refused_when_unavailable(self):
        snap = ecra(self.cliente, (self.produto, 1))
        with self.assertRaises(ValueError):
            snap.save()
        Produto.objects.filter(pk=self.produto.pk).update(stock_atual=0)
        resultado, problemas = services.criar_confirmacao(self.cliente, [(self.produto.id, 1)])
        self.assertEqual((resultado, [p.tipo for p in problemas]), ("bloqueado", ["stock"]))
        self.assertEqual(ConfirmacaoCarrinho.objects.count(), 1)


# ── garantir_sessao_pagamento (S1–S22) ─────────────────────────────────────

@override_settings(**URLS_A)
class SessaoPagamentoTests(TestCase):

    def setUp(self):
        self.produto = criar_produto(stock=5, descricao="Descrição original")
        _, self.encomenda = reservar(utilizador_telegram(), self.produto.id)
        create = patch.object(stripe.checkout.Session, "create")
        retrieve = patch.object(stripe.checkout.Session, "retrieve")
        self.create = create.start()
        self.retrieve = retrieve.start()
        self.addCleanup(create.stop)
        self.addCleanup(retrieve.stop)
        self.create.return_value = sessao("cs_1")

    def pagamento(self, **campos):
        dados = dict(encomenda=self.encomenda, metodo="stripe", valor=self.encomenda.total,
                     estado_pagamento="pendente", stripe_session_id="cs_old")
        dados.update(campos)
        return Pagamento.objects.create(**dados)

    def chaves_usadas(self):
        return [c.kwargs["idempotency_key"] for c in self.create.call_args_list]

    def falhar_update_uma_vez(self):
        real = QuerySet.update
        falhas = []

        def update(qs, **campos):
            if not falhas:
                falhas.append(1)
                raise DatabaseError("save failed")
            return real(qs, **campos)

        return patch.object(QuerySet, "update", autospec=True, side_effect=update)

    def test_s1_first_attempt_saves_session_before_returning_url(self):
        estado, url = services.garantir_sessao_pagamento(self.encomenda)
        self.assertEqual((estado, url), ("aberta", "https://checkout.example/cs_1"))
        self.assertEqual(self.chaves_usadas(), [services.chave_primeira_tentativa(self.encomenda)])
        self.assertEqual(Pagamento.objects.get(encomenda=self.encomenda).stripe_session_id, "cs_1")

    def test_s2_failed_save_then_retry_reuses_key_and_params(self):
        with patch.object(Pagamento.objects, "get_or_create", side_effect=DatabaseError("save failed")):
            with self.assertRaises(DatabaseError):
                services.garantir_sessao_pagamento(self.encomenda)
        self.assertFalse(Pagamento.objects.exists())
        services.garantir_sessao_pagamento(self.encomenda)
        primeiro, segundo = self.create.call_args_list
        self.assertEqual(primeiro.kwargs, segundo.kwargs)
        self.assertEqual(Pagamento.objects.get().stripe_session_id, "cs_1")

    def test_s3_open_session_is_retrieved_not_recreated(self):
        self.pagamento(stripe_session_id="cs_open")
        self.retrieve.return_value = sessao("cs_open", "open")
        self.assertEqual(services.garantir_sessao_pagamento(self.encomenda),
                         ("aberta", "https://checkout.example/cs_open"))
        self.create.assert_not_called()

    def test_s4_completed_session_waits_for_webhook_and_writes_nothing(self):
        pagamento = self.pagamento(stripe_session_id="cs_done")
        antes = model_to_dict(pagamento)
        for payment_status in ("paid", "unpaid"):
            completa = sessao("cs_done", "complete")
            completa.payment_status = payment_status
            self.retrieve.return_value = completa
            for _ in range(2):
                self.assertEqual(services.garantir_sessao_pagamento(self.encomenda), ("a_confirmar", None))
        self.create.assert_not_called()
        self.assertEqual(model_to_dict(Pagamento.objects.get(pk=pagamento.pk)), antes)
        self.assertEqual(Encomenda.objects.get(pk=self.encomenda.pk).estado, "pendente")

    def test_s5_paid_refunded_or_inactive_orders_get_no_session(self):
        pagamento = self.pagamento(estado_pagamento="pago")
        self.assertEqual(services.garantir_sessao_pagamento(self.encomenda), ("paga", None))
        Pagamento.objects.filter(pk=pagamento.pk).update(estado_pagamento="reembolsado")
        self.assertEqual(services.garantir_sessao_pagamento(self.encomenda), ("nao_ativa", None))
        for estado in ("cancelada", "confirmada"):
            Encomenda.objects.filter(pk=self.encomenda.pk).update(estado=estado)
            self.assertEqual(services.garantir_sessao_pagamento(self.encomenda), ("nao_ativa", None))
        self.create.assert_not_called()
        self.retrieve.assert_not_called()

    def test_s6_stripe_error_then_retry_uses_same_key(self):
        self.create.side_effect = [stripe.APIConnectionError("stripe down"), sessao("cs_1")]
        with self.assertRaises(stripe.APIConnectionError):
            services.garantir_sessao_pagamento(self.encomenda)
        self.assertFalse(Pagamento.objects.exists())
        services.garantir_sessao_pagamento(self.encomenda)
        self.assertEqual(len(set(self.chaves_usadas())), 1)

    def test_s7_first_attempt_key_is_stable_per_order_and_short(self):
        _, outra = reservar(utilizador_telegram(1002, "Outro"), self.produto.id)
        chave = services.chave_primeira_tentativa(self.encomenda)
        self.assertEqual(chave, services.chave_primeira_tentativa(Encomenda.objects.get(pk=self.encomenda.pk)))
        self.assertNotEqual(chave, services.chave_primeira_tentativa(outra))
        self.assertLessEqual(len(chave), 255)

    def test_s8_request_uses_saved_order_data_only(self):
        antes = services.parametros_checkout(self.encomenda)
        Produto.objects.filter(pk=self.produto.pk).update(
            nome="Outro nome", preco=Decimal("99.00"), descricao="Outra descrição")
        depois = services.parametros_checkout(Encomenda.objects.get(pk=self.encomenda.pk))
        self.assertEqual(antes, depois)
        item = depois["line_items"][0]["price_data"]
        self.assertEqual(item["unit_amount"], 5000)
        self.assertEqual(item["product_data"], {"name": "SSD 1TB"})
        self.assertEqual(depois["expires_at"],
                         int((self.encomenda.data_encomenda + services.JANELA_PAGAMENTO).timestamp()))

    def test_s9_expired_session_cancels_the_order_without_a_new_session(self):
        self.pagamento(stripe_session_id="cs_old")
        self.retrieve.return_value = sessao("cs_old", "expired")
        self.assertEqual(services.garantir_sessao_pagamento(self.encomenda), ("expirada", None))
        self.create.assert_not_called()
        self.assertEqual(Encomenda.objects.get(pk=self.encomenda.pk).estado, "cancelada")
        self.assertEqual(Pagamento.objects.get().estado_pagamento, "falhado")
        self.assertEqual(Produto.objects.get(pk=self.produto.pk).stock_atual, 5)

    def test_s10_sessions_are_created_only_until_28_minutes(self):
        envelhecer(Encomenda, self.encomenda.pk, "data_encomenda", minutes=27)
        self.assertEqual(services.garantir_sessao_pagamento(self.encomenda)[0], "aberta")
        Pagamento.objects.all().delete()
        envelhecer(Encomenda, self.encomenda.pk, "data_encomenda", minutes=29)
        self.assertEqual(services.garantir_sessao_pagamento(self.encomenda), ("expirada", None))
        self.assertEqual(self.create.call_count, 1)
        self.assertEqual(Encomenda.objects.get(pk=self.encomenda.pk).estado, "cancelada")
        self.assertEqual(Produto.objects.get(pk=self.produto.pk).stock_atual, 5)

    def test_s11_retry_after_the_cutoff_creates_no_session(self):
        self.create.side_effect = stripe.APIConnectionError("stripe down")
        with self.assertRaises(stripe.APIConnectionError):
            services.garantir_sessao_pagamento(self.encomenda)
        envelhecer(Encomenda, self.encomenda.pk, "data_encomenda", minutes=29)
        self.assertEqual(services.garantir_sessao_pagamento(self.encomenda), ("expirada", None))
        self.assertEqual(self.create.call_count, 1)

    def test_s12_payment_completing_at_the_deadline_waits_for_the_webhook(self):
        self.pagamento(stripe_session_id="cs_old")
        self.retrieve.side_effect = [sessao("cs_old", "expired"), sessao("cs_old", "complete")]
        self.assertEqual(services.garantir_sessao_pagamento(self.encomenda), ("a_confirmar", None))
        self.assertEqual(Encomenda.objects.get(pk=self.encomenda.pk).estado, "pendente")

    def test_s13_stripe_error_during_the_deadline_cancel_changes_nothing(self):
        self.pagamento(stripe_session_id="cs_old")
        self.retrieve.side_effect = [sessao("cs_old", "expired"), stripe.APIConnectionError("down")]
        with self.assertRaises(stripe.APIConnectionError):
            services.garantir_sessao_pagamento(self.encomenda)
        self.assertEqual(Encomenda.objects.get(pk=self.encomenda.pk).estado, "pendente")
        self.assertEqual(Produto.objects.get(pk=self.produto.pk).stock_atual, 4)

    def test_s14_multi_line_request_order_rounding_and_legacy_names(self):
        outro = criar_produto(nome="RAM", preco=Decimal("19.99"), stock=3)
        cliente = utilizador_telegram(1003, "Multi")
        _, encomenda = services.criar_encomenda_de_confirmacao(
            cliente, ecra(cliente, (outro, 1), (self.produto, 2)).nonce)
        LinhaEncomenda.objects.filter(encomenda=encomenda, produto=outro).update(nome_produto=None)  # legacy line
        itens = services.parametros_checkout(encomenda)["line_items"]
        self.assertEqual([(i["price_data"]["product_data"]["name"], i["price_data"]["unit_amount"], i["quantity"])
                          for i in itens], [("SSD 1TB", 5000, 2), ("RAM", 1999, 1)])
        self.assertEqual((services._centimos(Decimal("0.125")), services._centimos(Decimal("19.994"))), (13, 1999))

    def test_s15_idempotency_error_is_raised_and_changes_nothing(self):
        self.create.side_effect = stripe.IdempotencyError(
            "Keys for idempotent requests can only be used with the same parameters")
        with self.assertRaises(stripe.IdempotencyError):
            services.garantir_sessao_pagamento(self.encomenda)
        self.assertFalse(Pagamento.objects.exists())
        self.assertEqual(Encomenda.objects.get(pk=self.encomenda.pk).estado, "pendente")

    def test_s16_saved_line_amount_equals_order_total(self):
        linha = self.encomenda.linhas.get()
        self.assertEqual(linha.preco_unitario * linha.quantidade, self.encomenda.total)

    def test_s17_same_urls_keep_the_same_key(self):
        with patch.object(Pagamento.objects, "get_or_create", side_effect=DatabaseError("save failed")):
            with self.assertRaises(DatabaseError):
                services.garantir_sessao_pagamento(self.encomenda)
        services.garantir_sessao_pagamento(self.encomenda)
        primeiro, segundo = self.create.call_args_list
        self.assertEqual(primeiro.kwargs["idempotency_key"], segundo.kwargs["idempotency_key"])
        self.assertRegex(primeiro.kwargs["idempotency_key"], r"-u[0-9a-f]{12}$")
        self.assertEqual(primeiro.kwargs["success_url"], URLS_A["STRIPE_SUCCESS_URL"])
        self.assertEqual(primeiro.kwargs["cancel_url"], segundo.kwargs["cancel_url"])

    def test_s18_changed_urls_before_first_session_is_saved_give_new_key(self):
        self.create.side_effect = [sessao("cs_a"), sessao("cs_b")]
        with patch.object(Pagamento.objects, "get_or_create", side_effect=DatabaseError("save failed")):
            with self.assertRaises(DatabaseError):
                services.garantir_sessao_pagamento(self.encomenda)
        with override_settings(**URLS_B):
            self.assertEqual(services.garantir_sessao_pagamento(self.encomenda),
                             ("aberta", "https://checkout.example/cs_b"))
        primeiro, segundo = self.create.call_args_list
        self.assertNotEqual(primeiro.kwargs["idempotency_key"], segundo.kwargs["idempotency_key"])
        self.assertEqual(segundo.kwargs["success_url"], URLS_B["STRIPE_SUCCESS_URL"])
        self.assertEqual(list(Pagamento.objects.values_list("stripe_session_id", flat=True)), ["cs_b"])

    def test_s20_changed_urls_after_session_recorded_only_retrieve(self):
        self.pagamento(stripe_session_id="cs_open")
        self.retrieve.return_value = sessao("cs_open", "open")
        with override_settings(**URLS_B):
            self.assertEqual(services.garantir_sessao_pagamento(self.encomenda),
                             ("aberta", "https://checkout.example/cs_open"))
        self.create.assert_not_called()

    def test_s21_concurrent_first_attempt_uses_the_recorded_session(self):
        self.pagamento(stripe_session_id="cs_first")
        self.retrieve.return_value = sessao("cs_first", "open")
        self.create.return_value = sessao("cs_second")
        real = services._pagamento_de
        vezes = []

        def falha_uma_vez(e):
            vezes.append(1)
            return None if len(vezes) == 1 else real(e)

        with patch.object(services, "_pagamento_de", side_effect=falha_uma_vez):
            self.assertEqual(services.garantir_sessao_pagamento(self.encomenda),
                             ("aberta", "https://checkout.example/cs_first"))
        self.assertEqual(list(Pagamento.objects.values_list("stripe_session_id", flat=True)), ["cs_first"])

    def cancelar_encomenda(self, *args, **kwargs):
        Encomenda.objects.filter(pk=self.encomenda.pk).update(estado="cancelada")

    def test_s23_cancelled_during_first_attempt_saves_nothing(self):
        self.create.side_effect = lambda **kw: self.cancelar_encomenda() or sessao("cs_1")
        self.assertEqual(services.garantir_sessao_pagamento(self.encomenda), ("nao_ativa", None))
        self.assertFalse(Pagamento.objects.exists())

    def test_s25_cancelled_before_open_session_is_returned(self):
        self.pagamento(stripe_session_id="cs_open")
        self.retrieve.side_effect = lambda id_: self.cancelar_encomenda() or sessao(id_, "open")
        self.assertEqual(services.garantir_sessao_pagamento(self.encomenda), ("nao_ativa", None))


# ── Handler + services + PostgreSQL together (E1–E3) ───────────────────────

@override_settings(**URLS_A)
class CheckoutEndToEndTests(TestCase):

    def setUp(self):
        self.produto = criar_produto(stock=5)
        self.cliente = utilizador_telegram(4242)

    async def toque(self, data):
        query = MagicMock()
        query.from_user = self.cliente
        query.answer = AsyncMock()
        query.message.photo = None
        query.message.document = None
        query.edit_message_text = AsyncMock()
        wrapped = callbacks._RespostaUnica(query)
        await customer.confirmar_encomenda(wrapped, MagicMock(), data, self.cliente.id)
        await wrapped.answer()
        return query

    async def ecra(self):
        snap = await sync_to_async(ecra)(self.cliente, (self.produto, 1))
        return confirmacao.callback_confirmar(snap.nonce)

    async def test_e1_two_taps_same_screen_one_order(self):
        data = await self.ecra()
        with patch.object(stripe.checkout.Session, "create", return_value=sessao("cs_e1")) as create, \
             patch.object(stripe.checkout.Session, "retrieve", return_value=sessao("cs_e1", "open")):
            primeiro = await self.toque(data)
            segundo = await self.toque(data)
        self.assertEqual(await sync_to_async(contagens)(), (1, 1, 1, 1))
        self.assertEqual(await sync_to_async(Pagamento.objects.count)(), 1)
        self.assertEqual(create.call_count, 1)
        primeiro.answer.assert_awaited_once_with()
        segundo.answer.assert_awaited_once_with(MENSAGEM_JA_REGISTADA, show_alert=True)

    async def test_e2_retry_after_failed_stripe_recovers_payment(self):
        data = await self.ecra()
        with patch.object(stripe.checkout.Session, "create",
                          side_effect=[stripe.APIConnectionError("down"), sessao("cs_e2")]) as create:
            with self.assertLogs("telegram_bot.handlers.customer", level="ERROR"):
                await self.toque(data)
            self.assertEqual(await sync_to_async(Pagamento.objects.count)(), 0)
            segundo = await self.toque(data)
        self.assertEqual(await sync_to_async(contagens)(), (1, 1, 1, 1))
        segundo.answer.assert_awaited_once_with(MENSAGEM_JA_REGISTADA, show_alert=True)
        primeira_chave, segunda_chave = (c.kwargs["idempotency_key"] for c in create.call_args_list)
        self.assertEqual(primeira_chave, segunda_chave)
        texto = segundo.edit_message_text.await_args.kwargs["text"]
        self.assertIn("Clica no botão abaixo para completar o pagamento", texto)

    async def test_e3_retry_after_the_cutoff_ends_the_order(self):
        data = await self.ecra()
        with patch.object(stripe.checkout.Session, "create", side_effect=stripe.APIConnectionError("down")) as create:
            with self.assertLogs("telegram_bot.handlers.customer", level="ERROR"):
                await self.toque(data)
            encomenda = await sync_to_async(Encomenda.objects.get)()
            await sync_to_async(envelhecer)(Encomenda, encomenda.pk, "data_encomenda", minutes=29)
            segundo = await self.toque(data)
        self.assertEqual(create.call_count, 1)
        self.assertEqual(segundo.edit_message_text.await_args.kwargs["text"], MENSAGEM_PRAZO_TERMINADO)
        self.assertEqual((await sync_to_async(Encomenda.objects.get)()).estado, "cancelada")
        produto = await sync_to_async(Produto.objects.get)(pk=self.produto.pk)
        self.assertEqual(produto.stock_atual, 5)


# ── Real concurrency on PostgreSQL (T1, T2; owner decision G1) ─────────────

class ConcurrentCheckoutTests(TransactionTestCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        with connection.cursor() as cursor:
            cursor.execute("SELECT has_function_privilege('pg_catalog.pg_blocking_pids(integer)', 'EXECUTE')")
            if not cursor.fetchone()[0]:
                raise AssertionError("telegram_bot_test não pode executar pg_blocking_pids")
            cursor.execute("SELECT count(*) >= 0 FROM pg_locks")
            cursor.fetchone()

    def esperar_bloqueio(self, pids, bloqueado, bloqueador):
        """Poll PostgreSQL until session `bloqueado` waits on a lock held by `bloqueador`."""
        limite = time.monotonic() + 5
        while time.monotonic() < limite:
            if bloqueado in pids:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT pg_blocking_pids(%s)", [pids[bloqueado]])
                    if pids[bloqueador] in cursor.fetchone()[0]:
                        return True
            time.sleep(0.05)
        return False

    def correr_em_paralelo(self, tarefa_a, tarefa_b):
        """Thread A pauses right after locking its products; prove B waits on A in PostgreSQL."""
        pids, resultados, tempos = {}, {}, {}
        a_tem_bloqueio, libertar_a = threading.Event(), threading.Event()
        real = services._bloquear_produtos

        def bloquear(ids):
            produtos = real(ids)
            if threading.current_thread().name == "A" and not a_tem_bloqueio.is_set():
                a_tem_bloqueio.set()
                if not libertar_a.wait(10):
                    raise RuntimeError("A não foi libertado")
            return produtos

        def correr(nome, tarefa):
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT pg_backend_pid()")
                    pids[nome] = cursor.fetchone()[0]
                resultados[nome] = tarefa()
                tempos[nome] = time.monotonic()
            except Exception as exc:  # reported by the assertions below
                resultados[nome] = exc
            finally:
                connection.close()

        with patch.object(services, "_bloquear_produtos", side_effect=bloquear):
            thread_a = threading.Thread(target=correr, name="A", args=("A", tarefa_a))
            thread_b = threading.Thread(target=correr, name="B", args=("B", tarefa_b))
            thread_a.start()
            try:
                self.assertTrue(a_tem_bloqueio.wait(5), "A não chegou ao bloqueio dos produtos")
                thread_b.start()
                self.assertTrue(self.esperar_bloqueio(pids, "B", "A"), "B nunca ficou bloqueado por A")
                self.assertNotIn("B", resultados)
            finally:
                tempos["libertado"] = time.monotonic()
                libertar_a.set()
                thread_a.join(10)
                if thread_b.is_alive() or thread_b.ident:
                    thread_b.join(10)
        self.assertFalse(thread_a.is_alive() or thread_b.is_alive(), "uma thread não terminou")
        for nome in ("A", "B"):
            self.assertNotIsInstance(resultados[nome], Exception, resultados[nome])
        self.assertGreater(tempos["B"], tempos["libertado"])
        return resultados

    @staticmethod
    def confirmar(cliente, snap):
        return lambda: services.criar_encomenda_de_confirmacao(cliente, snap.nonce)

    def test_t1_last_unit_two_customers_one_order_one_alterado(self):
        produto = criar_produto(stock=1)
        a, b = utilizador_telegram(5001, "Cliente A"), utilizador_telegram(5002, "Cliente B")
        snap_a, snap_b = ecra(a, (produto, 1)), ecra(b, (produto, 1))
        resultados = self.correr_em_paralelo(self.confirmar(a, snap_a), self.confirmar(b, snap_b))
        self.assertEqual(resultados["A"][0], "criada")
        self.assertEqual((resultados["B"][0], [x.tipo for x in resultados["B"][1]]), ("alterado", ["stock"]))
        self.assertEqual(contagens(), (1, 1, 1, 1))
        self.assertEqual(Produto.objects.get(pk=produto.pk).stock_atual, 0)

    def test_t2_same_screen_from_two_threads_one_order(self):
        produto = criar_produto(stock=5)
        cliente = utilizador_telegram(5003, "Cliente")
        snap = ecra(cliente, (produto, 1))
        resultados = self.correr_em_paralelo(self.confirmar(cliente, snap), self.confirmar(cliente, snap))
        self.assertEqual((resultados["A"][0], resultados["B"][0]), ("criada", "existente"))
        self.assertEqual(resultados["A"][1].id, resultados["B"][1].id)
        self.assertEqual(contagens(), (1, 1, 1, 1))
        self.assertEqual(Produto.objects.get(pk=produto.pk).stock_atual, 4)

    def test_t6_shared_products_in_opposite_order_never_deadlock(self):
        p1, p2 = criar_produto(nome="A", stock=5), criar_produto(nome="B", stock=5)
        a, b = utilizador_telegram(5011, "Cliente A"), utilizador_telegram(5012, "Cliente B")
        snap_a, snap_b = ecra(a, (p1, 1), (p2, 2)), ecra(b, (p2, 1), (p1, 1))
        resultados = self.correr_em_paralelo(self.confirmar(a, snap_a), self.confirmar(b, snap_b))
        self.assertEqual((resultados["A"][0], resultados["B"][0]), ("criada", "criada"))
        self.assertEqual([Produto.objects.get(pk=p.pk).stock_atual for p in (p1, p2)], [3, 2])

    def test_t7_checkout_waits_for_a_cancellation_returning_shared_stock(self):
        produto = criar_produto(stock=2)
        antigo, novo = utilizador_telegram(5013, "Antigo"), utilizador_telegram(5014, "Novo")
        snap = ecra(novo, (produto, 1))  # drawn while stock was 2
        _, encomenda = reservar(antigo, produto.id, 2)  # stock 0
        resultados = self.correr_em_paralelo(lambda: services.cancelar_encomenda(encomenda.id, "teste"),
                                             self.confirmar(novo, snap))
        self.assertEqual((resultados["A"][0], resultados["B"][0]), ("cancelada", "criada"))
        self.assertEqual(Produto.objects.get(pk=produto.pk).stock_atual, 1)

    def test_t8_two_screens_of_one_customer_one_order_one_pendente(self):
        produto = criar_produto(stock=5)
        cliente = utilizador_telegram(5015, "Cliente")
        primeiro, segundo = ecra(cliente, (produto, 1)), ecra(cliente, (produto, 1))
        resultados = self.correr_em_paralelo(self.confirmar(cliente, primeiro), self.confirmar(cliente, segundo))
        self.assertEqual((resultados["A"][0], resultados["B"][0]), ("criada", "pendente"))
        self.assertEqual(resultados["B"][1].id, resultados["A"][1].id)
        self.assertEqual(contagens(), (1, 1, 1, 1))
        self.assertEqual(Produto.objects.get(pk=produto.pk).stock_atual, 4)

    def test_t9_admin_cancel_of_a_paid_order_racing_the_same_customers_checkout(self):
        produto = criar_produto(stock=5)
        cliente = utilizador_telegram(5016, "Cliente")
        _, paga = reservar(cliente, produto.id)
        Encomenda.objects.filter(pk=paga.pk).update(estado="confirmada")
        Pagamento.objects.create(encomenda=paga, metodo="stripe", valor=paga.total,
                                 estado_pagamento="pago", stripe_session_id="cs_t9")
        snap = ecra(cliente, (produto, 1))
        with patch.object(stripe.checkout.Session, "retrieve", return_value=SimpleNamespace(payment_intent="pi_t9")), \
             patch.object(stripe.Refund, "create"):
            resultados = self.correr_em_paralelo(lambda: services.cancelar_encomenda(paga.id, "teste"),
                                                 self.confirmar(cliente, snap))
        self.assertEqual((resultados["A"][0], resultados["B"][0]), ("reembolsada", "criada"))
        self.assertEqual(Produto.objects.get(pk=produto.pk).stock_atual, 4)

    def test_t3_cancellation_in_progress_wins_over_session_save(self):
        """P1: an admin cancellation mid-transaction blocks the session save, then wins."""
        produto = criar_produto(stock=5)
        Utilizador.objects.create(telegram_id="5004", nome="Cliente")
        _, encomenda = reservar(utilizador_telegram(5004, "Cliente"), produto.id)
        pids, resultados = {}, {}
        admin_tem_bloqueio, libertar_admin = threading.Event(), threading.Event()

        def registar_pid(nome):
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_backend_pid()")
                pids[nome] = cursor.fetchone()[0]

        def cancelar():  # same row write as Django admin's bulk cancel, held open
            try:
                registar_pid("admin")
                with transaction.atomic():
                    Encomenda.objects.filter(pk=encomenda.pk).update(estado="cancelada")
                    admin_tem_bloqueio.set()
                    libertar_admin.wait(10)
            finally:
                connection.close()

        def garantir():
            try:
                registar_pid("bot")
                resultados["bot"] = services.garantir_sessao_pagamento(Encomenda.objects.get(pk=encomenda.pk))
            except Exception as exc:  # reported by the assertions below
                resultados["bot"] = exc
            finally:
                connection.close()

        with patch.object(stripe.checkout.Session, "create", return_value=sessao("cs_t3")):
            admin = threading.Thread(target=cancelar, name="admin")
            bot = threading.Thread(target=garantir, name="bot")
            admin.start()
            try:
                self.assertTrue(admin_tem_bloqueio.wait(5), "o cancelamento não chegou a bloquear a encomenda")
                bot.start()
                self.assertTrue(self.esperar_bloqueio(pids, "bot", "admin"),
                                "o bot nunca esperou pelo cancelamento")
                self.assertNotIn("bot", resultados)
            finally:
                libertar_admin.set()
                admin.join(10)
                if bot.ident:
                    bot.join(10)
        self.assertFalse(admin.is_alive() or bot.is_alive(), "uma thread não terminou")
        self.assertEqual(resultados["bot"], ("nao_ativa", None))
        self.assertFalse(Pagamento.objects.exists())
        self.assertEqual(Encomenda.objects.get(pk=encomenda.pk).estado, "cancelada")


# ── Expiry cancels the unpaid order and returns stock (SEC-04) ─────────────

@override_settings(TELEGRAM_TOKEN="test-token", **URLS_A)
class ExpiracaoPagamentoTests(TestCase):

    def setUp(self):
        from core.views import _handle_payment_expired
        self.expirar = _handle_payment_expired
        self.produto = criar_produto(stock=5)
        _, self.encomenda = reservar(utilizador_telegram(7007), self.produto.id)
        self.pagamento = Pagamento.objects.create(
            encomenda=self.encomenda, metodo="stripe", valor=self.encomenda.total,
            estado_pagamento="pendente", stripe_session_id="cs_exp")
        envio = patch("core.views.httpx.post", return_value=MagicMock(status_code=200))
        self.post = envio.start()
        self.addCleanup(envio.stop)

    def stock(self):
        return Produto.objects.get(pk=self.produto.pk).stock_atual

    def estado(self):
        return (Encomenda.objects.get(pk=self.encomenda.pk).estado,
                Pagamento.objects.get(pk=self.pagamento.pk).estado_pagamento)

    def test_x1_expiry_cancels_order_returns_stock_and_notifies(self):
        self.assertEqual(self.stock(), 4)
        self.expirar({"id": "cs_exp"})
        self.assertEqual(self.estado(), ("cancelada", "falhado"))
        self.assertEqual(self.stock(), 5)
        self.assertEqual(MovimentoStock.objects.filter(tipo_movimento="entrada").count(), 1)
        self.assertEqual(set(RegistoAuditoria.objects.values_list("acao", flat=True)),
                         {"CRIAR_ENCOMENDA", "PAGAMENTO_EXPIRADO", "CANCELAR_ENCOMENDA_EXPIRADA"})
        self.post.assert_called_once()
        corpo = self.post.call_args.kwargs["json"]
        self.assertEqual(corpo["chat_id"], "7007")
        self.assertIn("A tua encomenda foi cancelada", corpo["text"])

    def test_x2_duplicate_expiry_event_changes_nothing_twice(self):
        self.expirar({"id": "cs_exp"})
        self.expirar({"id": "cs_exp"})
        self.assertEqual(self.stock(), 5)
        self.assertEqual(MovimentoStock.objects.filter(tipo_movimento="entrada").count(), 1)
        self.post.assert_called_once()

    def test_x3_order_already_cancelled_keeps_stock_and_sends_nothing(self):
        Encomenda.objects.filter(pk=self.encomenda.pk).update(estado="cancelada")
        Produto.objects.filter(pk=self.produto.pk).update(stock_atual=5)  # the admin returned it then
        self.expirar({"id": "cs_exp"})
        self.assertEqual(self.estado(), ("cancelada", "falhado"))
        self.assertEqual(self.stock(), 5)
        self.post.assert_not_called()

    def test_x4_event_for_replaced_session_is_ignored(self):
        Pagamento.objects.filter(pk=self.pagamento.pk).update(stripe_session_id="cs_new")
        with self.assertLogs("core.views", level="ERROR"):
            self.expirar({"id": "cs_exp"})
        self.assertEqual(self.estado(), ("pendente", "pendente"))
        self.assertEqual(self.stock(), 4)

    def test_x5_retry_after_expiry_cancellation_gets_no_session(self):
        self.expirar({"id": "cs_exp"})
        with patch.object(stripe.checkout.Session, "create") as create, \
             patch.object(stripe.checkout.Session, "retrieve") as retrieve:
            self.assertEqual(services.garantir_sessao_pagamento(self.encomenda), ("nao_ativa", None))
        create.assert_not_called()
        retrieve.assert_not_called()

    def test_x6_missing_image_file_does_not_block_stock_return(self):
        Produto.objects.filter(pk=self.produto.pk).update(imagem="produtos/em-falta.jpg")
        self.expirar({"id": "cs_exp"})
        self.assertEqual(self.stock(), 5)

    def test_x7_paid_payment_is_left_alone(self):
        Pagamento.objects.filter(pk=self.pagamento.pk).update(estado_pagamento="pago")
        self.expirar({"id": "cs_exp"})
        self.assertEqual(self.estado(), ("pendente", "pago"))
        self.assertEqual(self.stock(), 4)

    def test_x8_service_refuses_a_session_that_is_no_longer_stored(self):
        self.assertFalse(services.cancelar_por_expiracao(self.pagamento.pk, "cs_other"))
        self.assertEqual(self.estado(), ("pendente", "pendente"))
        self.assertEqual(self.stock(), 4)


# ── Webhook payment confirmation is atomic and idempotent (SEC-05) ─────────

@override_settings(TELEGRAM_TOKEN="test-token", **URLS_A)
class ConfirmacaoPagamentoTests(TestCase):

    def setUp(self):
        from core.views import _handle_payment_success
        self.confirmar = _handle_payment_success
        _, self.encomenda = reservar(utilizador_telegram(7008), criar_produto(stock=5).id)
        self.pagamento = Pagamento.objects.create(
            encomenda=self.encomenda, metodo="stripe", valor=self.encomenda.total,
            estado_pagamento="pendente", stripe_session_id="cs_ok")
        envio = patch("core.views.httpx.post", return_value=MagicMock(status_code=200))
        self.post = envio.start()
        self.addCleanup(envio.stop)

    def estado(self):
        return (Encomenda.objects.get(pk=self.encomenda.pk).estado,
                Pagamento.objects.get(pk=self.pagamento.pk).estado_pagamento)

    def confirmacoes(self):
        return RegistoAuditoria.objects.filter(acao="PAGAMENTO_CONFIRMADO").count()

    def test_w1_completed_session_confirms_order_and_notifies_once(self):
        self.confirmar({"id": "cs_ok"})
        self.assertEqual(self.estado(), ("confirmada", "pago"))
        self.assertIsNotNone(Pagamento.objects.get(pk=self.pagamento.pk).data_pagamento)
        self.assertEqual(self.confirmacoes(), 1)
        self.post.assert_called_once()
        self.assertIn("Pagamento confirmado", self.post.call_args.kwargs["json"]["text"])

    def test_w2_duplicate_delivery_changes_nothing_and_sends_nothing(self):
        self.confirmar({"id": "cs_ok"})
        self.confirmar({"id": "cs_ok"})
        self.assertEqual(self.confirmacoes(), 1)
        self.post.assert_called_once()

    def test_w3_service_refuses_a_session_that_is_no_longer_stored(self):
        self.assertFalse(services.confirmar_pagamento(self.pagamento.pk, "cs_other"))
        self.assertEqual(self.estado(), ("pendente", "pendente"))
        self.assertEqual(self.confirmacoes(), 0)

    def test_w4_failed_payment_is_still_confirmed_by_a_completed_session(self):
        Pagamento.objects.filter(pk=self.pagamento.pk).update(estado_pagamento="falhado")
        self.confirmar({"id": "cs_ok"})
        self.assertEqual(self.estado(), ("confirmada", "pago"))


@override_settings(TELEGRAM_TOKEN="test-token", **URLS_A)
class ConcurrentWebhookTests(TransactionTestCase):

    esperar_bloqueio = ConcurrentCheckoutTests.esperar_bloqueio

    def test_t4_concurrent_duplicate_deliveries_confirm_once(self):
        """Delivery A pauses holding the locks; delivery B must wait, then do nothing."""
        from core.views import _handle_payment_success
        Utilizador.objects.create(telegram_id="5005", nome="Cliente")
        _, encomenda = reservar(utilizador_telegram(5005, "Cliente"), criar_produto(stock=5).id)
        Pagamento.objects.create(encomenda=encomenda, metodo="stripe", valor=encomenda.total,
                                 estado_pagamento="pendente", stripe_session_id="cs_t4")
        pids, erros = {}, []
        a_tem_bloqueio, libertar_a = threading.Event(), threading.Event()
        real = RegistoAuditoria.objects.create

        def auditar(**kwargs):
            if threading.current_thread().name == "A":
                a_tem_bloqueio.set()  # A holds the order and payment locks, uncommitted
                libertar_a.wait(10)
            return real(**kwargs)

        def entregar(nome):
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT pg_backend_pid()")
                    pids[nome] = cursor.fetchone()[0]
                _handle_payment_success({"id": "cs_t4"})
            except Exception as exc:  # reported by the assertions below
                erros.append(exc)
            finally:
                connection.close()

        with patch("core.views.httpx.post", return_value=MagicMock(status_code=200)) as post, \
             patch.object(RegistoAuditoria.objects, "create", side_effect=auditar):
            a = threading.Thread(target=entregar, name="A", args=("A",))
            b = threading.Thread(target=entregar, name="B", args=("B",))
            a.start()
            try:
                self.assertTrue(a_tem_bloqueio.wait(5), "A não chegou ao bloqueio")
                b.start()
                self.assertTrue(self.esperar_bloqueio(pids, "B", "A"), "B nunca esperou por A")
            finally:
                libertar_a.set()
                a.join(10)
                if b.ident:
                    b.join(10)
        self.assertFalse(a.is_alive() or b.is_alive(), "uma thread não terminou")
        self.assertEqual(erros, [])
        self.assertEqual(RegistoAuditoria.objects.filter(acao="PAGAMENTO_CONFIRMADO").count(), 1)
        self.assertEqual(post.call_count, 1)
        self.assertEqual(Encomenda.objects.get(pk=encomenda.pk).estado, "confirmada")


# ── One cancellation for every admin surface: Stripe, stock, audit (SEC-06) ─

@override_settings(TELEGRAM_TOKEN="test-token", **URLS_A)
class CancelamentoTests(TestCase):

    def setUp(self):
        self.produto = criar_produto(stock=5)
        _, self.encomenda = reservar(utilizador_telegram(7010), self.produto.id)
        mocks = {}
        for alvo, nome in ((stripe.checkout.Session, "retrieve"), (stripe.checkout.Session, "expire"),
                           (stripe.Refund, "create")):
            p = patch.object(alvo, nome)
            mocks[nome] = p.start()
            self.addCleanup(p.stop)
        self.retrieve, self.expire, self.refund = mocks["retrieve"], mocks["expire"], mocks["create"]
        envio = patch("core.views.httpx.post", return_value=MagicMock(status_code=200))
        self.post = envio.start()
        self.addCleanup(envio.stop)

    def pagar(self, estado="pendente", encomenda="pendente"):
        Encomenda.objects.filter(pk=self.encomenda.pk).update(estado=encomenda)
        Pagamento.objects.create(
            encomenda=self.encomenda, metodo="stripe", valor=self.encomenda.total,
            estado_pagamento=estado, stripe_session_id="cs_c")

    def stock(self):
        return Produto.objects.get(pk=self.produto.pk).stock_atual

    def estado(self):
        pagamento = Pagamento.objects.filter(encomenda=self.encomenda).values_list("estado_pagamento", flat=True).first()
        return (Encomenda.objects.get(pk=self.encomenda.pk).estado, pagamento)

    def nada_mudou(self, estado):
        self.assertEqual(self.estado(), estado)
        self.assertEqual(self.stock(), 4)
        self.assertFalse(MovimentoStock.objects.filter(tipo_movimento="entrada").exists())
        self.assertFalse(RegistoAuditoria.objects.filter(acao="CANCELAR_ENCOMENDA").exists())

    def test_c1_order_without_payment_is_cancelled_without_stripe(self):
        resultado, _ = services.cancelar_encomenda(self.encomenda.id, "teste")
        self.assertEqual(resultado, "cancelada")
        self.assertEqual(self.estado(), ("cancelada", None))
        self.assertEqual(self.stock(), 5)
        self.assertEqual(MovimentoStock.objects.filter(tipo_movimento="entrada").count(), 1)
        self.assertIn("(teste)", RegistoAuditoria.objects.get(acao="CANCELAR_ENCOMENDA").descricao)
        self.retrieve.assert_not_called()

    def test_c2_open_session_is_expired_before_cancelling(self):
        self.pagar()
        self.retrieve.return_value = sessao("cs_c", "open")
        self.assertEqual(services.cancelar_encomenda(self.encomenda.id)[0], "cancelada")
        self.expire.assert_called_once_with("cs_c")
        self.assertEqual(self.estado(), ("cancelada", "falhado"))
        self.assertEqual(self.stock(), 5)

    def test_c3_completed_session_refuses_the_cancellation(self):
        self.pagar()
        self.retrieve.return_value = sessao("cs_c", "complete")
        with self.assertRaises(services.PagamentoRecebido):
            services.cancelar_encomenda(self.encomenda.id)
        self.expire.assert_not_called()
        self.nada_mudou(("pendente", "pendente"))

    def test_c4_expired_session_needs_no_stripe_write(self):
        self.pagar()
        self.retrieve.return_value = sessao("cs_c", "expired")
        self.assertEqual(services.cancelar_encomenda(self.encomenda.id)[0], "cancelada")
        self.expire.assert_not_called()
        self.assertEqual(self.estado(), ("cancelada", "falhado"))

    def test_c5_paid_order_is_refunded_in_full(self):
        self.pagar(estado="pago", encomenda="confirmada")
        self.retrieve.return_value = SimpleNamespace(payment_intent="pi_1")
        self.assertEqual(services.cancelar_encomenda(self.encomenda.id)[0], "reembolsada")
        self.refund.assert_called_once()
        kwargs = self.refund.call_args.kwargs
        self.assertEqual(kwargs["payment_intent"], "pi_1")
        self.assertNotIn("amount", kwargs)  # full refund
        self.assertTrue(kwargs["idempotency_key"].startswith(f"reembolso-encomenda-{self.encomenda.id}-"))
        self.assertEqual(self.estado(), ("cancelada", "reembolsado"))
        self.assertEqual(self.stock(), 5)

    def test_c6_stripe_failure_changes_nothing(self):
        self.pagar(estado="pago", encomenda="confirmada")
        self.retrieve.return_value = SimpleNamespace(payment_intent="pi_1")
        self.refund.side_effect = stripe.error.APIConnectionError("sem rede")
        with self.assertRaises(stripe.error.StripeError):
            services.cancelar_encomenda(self.encomenda.id)
        self.nada_mudou(("confirmada", "pago"))

    def test_c7_shipped_or_missing_order_is_not_cancelled(self):
        Encomenda.objects.filter(pk=self.encomenda.pk).update(estado="enviada")
        self.assertEqual(services.cancelar_encomenda(self.encomenda.id)[0], "invalida")
        self.assertEqual(services.cancelar_encomenda(999999), ("invalida", None))
        self.nada_mudou(("enviada", None))

    def test_c8_payment_completing_after_cancellation_is_refunded(self):
        from core.views import _handle_payment_success
        self.pagar()
        self.retrieve.return_value = SimpleNamespace(status="open", payment_intent="pi_late")
        services.cancelar_encomenda(self.encomenda.id)
        _handle_payment_success({"id": "cs_c"})
        _handle_payment_success({"id": "cs_c"})  # duplicate delivery
        self.refund.assert_called_once()
        self.assertEqual(self.refund.call_args.kwargs["payment_intent"], "pi_late")
        self.assertEqual(self.estado(), ("cancelada", "reembolsado"))
        self.assertEqual(self.stock(), 5)
        self.assertEqual(RegistoAuditoria.objects.filter(acao="PAGAMENTO_REEMBOLSADO").count(), 1)
        self.post.assert_called_once()
        self.assertIn("reembolsado na totalidade", self.post.call_args.kwargs["json"]["text"])

    def test_c9_refund_failure_in_webhook_rolls_back_for_redelivery(self):
        from core.views import _handle_payment_success
        self.pagar(estado="falhado", encomenda="cancelada")
        self.retrieve.return_value = SimpleNamespace(payment_intent="pi_late")
        self.refund.side_effect = stripe.error.APIConnectionError("sem rede")
        with self.assertRaises(stripe.error.StripeError):
            _handle_payment_success({"id": "cs_c"})
        self.assertEqual(self.estado(), ("cancelada", "falhado"))
        self.post.assert_not_called()

    def test_c10_django_admin_bulk_cancel_uses_the_shared_operation(self):
        from django.contrib import admin as django_admin
        from core.admin import EncomendaAdmin
        _, enviada = reservar(utilizador_telegram(7011), self.produto.id)
        Encomenda.objects.filter(pk=enviada.pk).update(estado="enviada")
        modelo = EncomendaAdmin(Encomenda, django_admin.site)
        with patch.object(modelo, "message_user") as message_user:
            modelo.marcar_cancelada(SimpleNamespace(user="admin"),
                                    Encomenda.objects.filter(pk__in=[self.encomenda.pk, enviada.pk]))
        self.assertEqual(self.estado(), ("cancelada", None))
        self.assertEqual(Encomenda.objects.get(pk=enviada.pk).estado, "enviada")
        self.assertEqual(self.stock(), 4)  # 3 after two orders, +1 for the cancelled one
        self.assertIn("Django admin: admin", RegistoAuditoria.objects.get(acao="CANCELAR_ENCOMENDA").descricao)
        self.post.assert_called_once()
        self.assertIn("foi cancelada", self.post.call_args.kwargs["json"]["text"])
        textos = [c.args[1] for c in message_user.call_args_list]
        self.assertIn("1 encomenda(s) cancelada(s).", textos)
        self.assertTrue(any(f"#{enviada.pk}" in t for t in textos))


@override_settings(TELEGRAM_TOKEN="test-token", **URLS_A)
class ConcurrentCancellationTests(TransactionTestCase):

    esperar_bloqueio = ConcurrentCheckoutTests.esperar_bloqueio

    def test_t5_payment_webhook_waits_for_cancellation_then_refunds(self):
        """The admin cancel holds the order lock during its Stripe call; the webhook must wait."""
        from core.views import _handle_payment_success
        Utilizador.objects.create(telegram_id="5006", nome="Cliente")
        produto = criar_produto(stock=5)
        _, encomenda = reservar(utilizador_telegram(5006, "Cliente"), produto.id)
        Pagamento.objects.create(encomenda=encomenda, metodo="stripe", valor=encomenda.total,
                                 estado_pagamento="pendente", stripe_session_id="cs_t5")
        pids, erros = {}, []
        admin_tem_bloqueio, libertar_admin = threading.Event(), threading.Event()

        def expirar(_id):
            admin_tem_bloqueio.set()
            libertar_admin.wait(10)

        def correr(nome, funcao):
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT pg_backend_pid()")
                    pids[nome] = cursor.fetchone()[0]
                funcao()
            except Exception as exc:  # reported by the assertions below
                erros.append(exc)
            finally:
                connection.close()

        with patch.object(stripe.checkout.Session, "retrieve",
                          return_value=SimpleNamespace(status="open", payment_intent="pi_t5")), \
             patch.object(stripe.checkout.Session, "expire", side_effect=expirar), \
             patch.object(stripe.Refund, "create") as refund, \
             patch("core.views.httpx.post", return_value=MagicMock(status_code=200)) as post:
            admin = threading.Thread(target=correr, args=("admin", lambda: services.cancelar_encomenda(encomenda.id)))
            webhook = threading.Thread(target=correr, args=("webhook", lambda: _handle_payment_success({"id": "cs_t5"})))
            admin.start()
            try:
                self.assertTrue(admin_tem_bloqueio.wait(5), "o cancelamento não chegou à Stripe")
                webhook.start()
                self.assertTrue(self.esperar_bloqueio(pids, "webhook", "admin"), "o webhook nunca esperou")
                refund.assert_not_called()
            finally:
                libertar_admin.set()
                admin.join(10)
                if webhook.ident:
                    webhook.join(10)
        self.assertFalse(admin.is_alive() or webhook.is_alive(), "uma thread não terminou")
        self.assertEqual(erros, [])
        refund.assert_called_once()
        self.assertEqual(Encomenda.objects.get(pk=encomenda.pk).estado, "cancelada")
        self.assertEqual(Pagamento.objects.get(encomenda=encomenda).estado_pagamento, "reembolsado")
        self.assertEqual(Produto.objects.get(pk=produto.pk).stock_atual, 5)
        self.assertIn("reembolsado", post.call_args.kwargs["json"]["text"])


# ── Admin changes go through domain operations (SEC-10) ────────────────────

@override_settings(TELEGRAM_TOKEN="test-token", **URLS_A)
class AdminInvariantTests(TestCase):

    def setUp(self):
        from django.contrib import admin as django_admin
        from core.admin import EncomendaAdmin, PagamentoAdmin, LinhaEncomendaAdmin, ProdutoAdmin
        self.site = django_admin.site
        self.EncomendaAdmin, self.PagamentoAdmin = EncomendaAdmin, PagamentoAdmin
        self.LinhaEncomendaAdmin, self.ProdutoAdmin = LinhaEncomendaAdmin, ProdutoAdmin
        self.produto = criar_produto(stock=5)
        _, self.encomenda = reservar(utilizador_telegram(7020), self.produto.id)
        envio = patch("core.views.httpx.post", return_value=MagicMock(status_code=200))
        self.post = envio.start()
        self.addCleanup(envio.stop)

    def estado(self, encomenda=None):
        return Encomenda.objects.get(pk=(encomenda or self.encomenda).pk).estado

    def stock(self):
        return Produto.objects.get(pk=self.produto.pk).stock_atual

    def test_a1_status_only_moves_forward_one_step(self):
        self.assertIsNone(services.avancar_estado_encomenda(self.encomenda.id, "enviada"))  # still pendente
        self.assertIsNone(services.avancar_estado_encomenda(self.encomenda.id, "confirmada"))  # webhook only
        Encomenda.objects.filter(pk=self.encomenda.pk).update(estado="confirmada")
        self.assertIsNone(services.avancar_estado_encomenda(self.encomenda.id, "entregue"))  # no skipping
        self.assertIsNotNone(services.avancar_estado_encomenda(self.encomenda.id, "enviada", "teste"))
        self.assertIsNotNone(services.avancar_estado_encomenda(self.encomenda.id, "entregue"))
        self.assertEqual(self.estado(), "entregue")
        self.assertIsNone(services.avancar_estado_encomenda(self.encomenda.id, "enviada"))  # no going back
        self.assertEqual(list(RegistoAuditoria.objects.filter(entidade="Encomenda", acao__startswith="ENCOMENDA_")
                              .order_by("id").values_list("acao", flat=True)),
                         ["ENCOMENDA_ENVIADA", "ENCOMENDA_ENTREGUE"])
        self.assertIn("(teste)", RegistoAuditoria.objects.get(acao="ENCOMENDA_ENVIADA").descricao)

    def test_a2_cancelled_order_cannot_be_reopened(self):
        Encomenda.objects.filter(pk=self.encomenda.pk).update(estado="cancelada")
        for estado in ("enviada", "entregue", "confirmada", "pendente"):
            self.assertIsNone(services.avancar_estado_encomenda(self.encomenda.id, estado))
        self.assertEqual(self.estado(), "cancelada")

    def test_a3_django_ship_action_uses_the_step_and_notifies(self):
        _, confirmada = reservar(utilizador_telegram(7021), self.produto.id)
        Encomenda.objects.filter(pk=confirmada.pk).update(estado="confirmada")
        modelo = self.EncomendaAdmin(Encomenda, self.site)
        self.assertNotIn("marcar_confirmada", modelo.actions)
        with patch.object(modelo, "message_user") as message_user:
            modelo.marcar_enviada(SimpleNamespace(user="admin"),
                                  Encomenda.objects.filter(pk__in=[self.encomenda.pk, confirmada.pk]))
        self.assertEqual(self.estado(confirmada), "enviada")
        self.assertEqual(self.estado(), "pendente")
        self.post.assert_called_once()
        self.assertIn("enviada", self.post.call_args.kwargs["json"]["text"].lower())
        textos = [c.args[1] for c in message_user.call_args_list]
        self.assertIn("1 encomenda(s) marcada(s) como enviada.", textos)
        self.assertTrue(any(f"#{self.encomenda.pk}" in t for t in textos))

    def test_a4_orders_lines_and_payments_are_read_only(self):
        pedido = SimpleNamespace()
        encomendas = self.EncomendaAdmin(Encomenda, self.site)
        self.assertFalse(encomendas.has_add_permission(pedido))
        self.assertFalse(encomendas.has_delete_permission(pedido, self.encomenda))
        formulario = encomendas.get_form(pedido, self.encomenda, change=True)
        self.assertEqual(list(formulario.base_fields), ["notas"])
        inline = encomendas.inlines[0](Encomenda, self.site)
        self.assertFalse(inline.has_change_permission(pedido, self.encomenda))
        self.assertFalse(inline.has_add_permission(pedido, self.encomenda))
        for classe, modelo in ((self.PagamentoAdmin, Pagamento), (self.LinhaEncomendaAdmin, LinhaEncomenda)):
            admin_modelo = classe(modelo, self.site)
            self.assertFalse(admin_modelo.has_add_permission(pedido))
            self.assertFalse(admin_modelo.has_change_permission(pedido))
            self.assertFalse(admin_modelo.has_delete_permission(pedido))

    def test_a5_stock_adjustment_is_relative_clamped_and_recorded(self):
        self.assertEqual(services.ajustar_stock(self.produto.id, 3, "teste"), 7)
        self.assertEqual(services.ajustar_stock(self.produto.id, -10, "teste"), 0)
        self.assertEqual(services.ajustar_stock(self.produto.id, -1, "teste"), 0)  # nothing to remove
        movimentos = list(MovimentoStock.objects.filter(observacoes="teste").order_by("id")
                          .values_list("tipo_movimento", "quantidade"))
        self.assertEqual(movimentos, [("entrada", 3), ("saida", 7)])

    def test_a6_django_stock_edit_keeps_a_concurrent_checkout(self):
        produto = Produto.objects.get(pk=self.produto.pk)  # the admin opens the form at stock 4
        Produto.objects.filter(pk=produto.pk).update(stock_atual=3)  # a checkout lands meanwhile
        produto.stock_atual, produto.nome = 6, "Novo nome"  # the admin types 6 (+2) and renames
        formulario = SimpleNamespace(changed_data=["nome", "stock_atual"], initial={"stock_atual": 4})
        self.ProdutoAdmin(Produto, self.site).save_model(SimpleNamespace(user="admin"), produto, formulario, True)
        atual = Produto.objects.get(pk=produto.pk)
        self.assertEqual((atual.stock_atual, atual.nome), (5, "Novo nome"))
        self.assertTrue(MovimentoStock.objects.filter(tipo_movimento="entrada", quantidade=2).exists())
        self.assertIn("delta=2, novo stock=5 (Django admin: admin)",
                      RegistoAuditoria.objects.get(acao="AJUSTE_STOCK").descricao)

    def test_a8_bulk_activation_locks_products_in_id_order(self):
        from django.test.utils import CaptureQueriesContext
        outro = criar_produto(nome="Outro")
        modelo = self.ProdutoAdmin(Produto, self.site)
        with CaptureQueriesContext(connection) as consultas, patch.object(modelo, "message_user"):
            modelo.desativar_produtos(SimpleNamespace(user="admin"),
                                      Produto.objects.filter(pk__in=[outro.pk, self.produto.pk]))
        self.assertFalse(Produto.objects.filter(ativo=True).exists())
        bloqueio = next(q["sql"] for q in consultas.captured_queries if "FOR UPDATE" in q["sql"])
        self.assertIn("ORDER BY", bloqueio)

    def test_a7_telegram_toggle_and_stock_buttons_keep_a_concurrent_checkout(self):
        produto = Produto.objects.get(pk=self.produto.pk)  # stock 4 in memory
        Produto.objects.filter(pk=produto.pk).update(stock_atual=2)
        async_to_sync(services.alternar_produto_ativo)(produto)
        self.assertEqual(Produto.objects.get(pk=produto.pk).stock_atual, 2)
        async_to_sync(services.ajustar_stock_manual)(produto, 1)
        self.assertEqual(produto.stock_atual, 3)
        self.assertEqual(Produto.objects.get(pk=produto.pk).stock_atual, 3)


# ── Product images are processed once, on upload, through storage (SEC-11) ─

class ImagemProdutoTests(TestCase):

    def setUp(self):
        import shutil
        import tempfile
        pasta = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, pasta, ignore_errors=True)
        definicoes = override_settings(MEDIA_ROOT=pasta)
        definicoes.enable()
        self.addCleanup(definicoes.disable)

    def upload(self, tamanho=(1200, 600), formato="PNG", nome="foto.png"):
        from io import BytesIO
        from django.core.files.uploadedfile import SimpleUploadedFile
        from PIL import Image
        dados = BytesIO()
        Image.new("RGBA", tamanho, (200, 10, 10, 255)).save(dados, formato)
        return SimpleUploadedFile(nome, dados.getvalue(), content_type="image/png")

    def test_i1_new_upload_becomes_an_800px_square_jpeg(self):
        from PIL import Image
        produto = criar_produto(nome="GPU", preco=Decimal("10.00"), stock=3, imagem=self.upload())
        self.assertTrue(produto.imagem.name.startswith("produtos/") and produto.imagem.name.endswith(".jpg"))
        with produto.imagem.open("rb") as ficheiro, Image.open(ficheiro) as img:
            self.assertEqual((img.format, img.size, img.mode), ("JPEG", (800, 800), "RGB"))

    def test_i2_ordinary_saves_leave_the_stored_image_alone(self):
        produto = criar_produto(nome="GPU", preco=Decimal("10.00"), stock=3, imagem=self.upload())
        with produto.imagem.open("rb") as ficheiro:
            antes = ficheiro.read()
        nome = produto.imagem.name
        with patch.object(Produto, "_preparar_imagem") as preparar:
            produto = Produto.objects.get(pk=produto.pk)
            produto.nome, produto.ativo = "GPU 2", False
            produto.save()
        preparar.assert_not_called()
        produto.refresh_from_db()
        self.assertEqual(produto.imagem.name, nome)
        with produto.imagem.open("rb") as ficheiro:
            self.assertEqual(ficheiro.read(), antes)

    def test_i3_replacing_the_image_processes_the_new_one(self):
        produto = criar_produto(nome="GPU", preco=Decimal("10.00"), stock=3, imagem=self.upload())
        produto.imagem = self.upload(tamanho=(300, 900), nome="nova.png")
        produto.save()
        self.assertTrue(produto.imagem.name.startswith("produtos/nova"))
        self.assertTrue(produto.imagem.name.endswith(".jpg"))


# ── The cleanup reconciles every old pending order (SEC-03, cart design G12) ─

@override_settings(TELEGRAM_TOKEN="test-token", **URLS_A)
class EncomendasAbandonadasTests(TestCase):

    def setUp(self):
        self.produto = criar_produto(stock=10)
        self.encomendas = {}
        for nome, horas, telegram_id in (("velha", 25, 7030), ("recente", 23, 7031), ("com_sessao", 30, 7032)):
            _, encomenda = reservar(utilizador_telegram(telegram_id), self.produto.id)
            envelhecer(Encomenda, encomenda.pk, "data_encomenda", hours=horas)
            self.encomendas[nome] = encomenda
        self.pagamento = Pagamento.objects.create(
            encomenda=self.encomendas["com_sessao"], metodo="stripe", valor=Decimal("50.00"),
            estado_pagamento="pendente", stripe_session_id="cs_v")
        mocks = {}
        for nome in ("retrieve", "expire"):
            p = patch.object(stripe.checkout.Session, nome)
            mocks[nome] = p.start()
            self.addCleanup(p.stop)
        self.retrieve, self.expire = mocks["retrieve"], mocks["expire"]
        self.retrieve.return_value = sessao("cs_v", "open")
        envio = patch("core.views.httpx.post", return_value=MagicMock(status_code=200))
        self.post = envio.start()
        self.addCleanup(envio.stop)

    def correr(self, *argumentos):
        from io import StringIO
        from django.core.management import call_command
        saida = StringIO()
        call_command("cancelar_encomendas_abandonadas", *argumentos, stdout=saida)
        return saida.getvalue()

    def estado(self, nome):
        return Encomenda.objects.get(pk=self.encomendas[nome].pk).estado

    def stock(self):
        return Produto.objects.get(pk=self.produto.pk).stock_atual

    def test_unpaid_old_orders_are_cancelled_and_open_sessions_expired(self):
        saida = self.correr()
        self.assertEqual((self.estado("velha"), self.estado("recente"), self.estado("com_sessao")),
                         ("cancelada", "pendente", "cancelada"))
        self.expire.assert_called_once_with("cs_v")
        self.assertEqual(self.stock(), 9)  # 10 - 3 + 2
        self.assertIn("sem pagamento há mais de 24 h",
                      RegistoAuditoria.objects.filter(acao="CANCELAR_ENCOMENDA").first().descricao)
        self.assertEqual(self.post.call_count, 2)
        self.assertIn("0 confirmada(s), 2 cancelada(s), 0 erro(s)", saida)

    def test_paid_session_is_confirmed_like_the_webhook(self):
        self.retrieve.return_value = sessao("cs_v", "complete")
        saida = self.correr()
        self.assertEqual(self.estado("com_sessao"), "confirmada")
        self.assertEqual(Pagamento.objects.get(pk=self.pagamento.pk).estado_pagamento, "pago")
        self.expire.assert_not_called()
        self.assertEqual(RegistoAuditoria.objects.filter(acao="PAGAMENTO_CONFIRMADO").count(), 1)
        textos = [c.kwargs["json"]["text"] for c in self.post.call_args_list]
        self.assertTrue(any("foi confirmada" in t for t in textos))
        self.assertEqual(self.stock(), 8)  # only the unpaid order's unit came back
        self.assertIn("1 confirmada(s), 1 cancelada(s), 0 erro(s)", saida)

    def test_stripe_error_leaves_the_order_for_the_next_run(self):
        self.retrieve.side_effect = stripe.error.APIConnectionError("sem rede")
        with self.assertLogs("core.services", level="ERROR"):
            saida = self.correr()
        self.assertEqual(self.estado("com_sessao"), "pendente")
        self.assertIn("1 cancelada(s), 1 erro(s)", saida)

    def test_pending_order_with_a_paid_payment_is_never_refunded(self):
        Pagamento.objects.filter(pk=self.pagamento.pk).update(estado_pagamento="pago")
        with patch.object(stripe.Refund, "create") as refund, self.assertLogs("core.services", level="ERROR"):
            saida = self.correr()
        refund.assert_not_called()
        self.assertEqual(self.estado("com_sessao"), "pendente")
        self.assertIn("1 erro(s)", saida)

    def test_legacy_orders_are_reconciled_too(self):
        utilizador = Utilizador.objects.create(telegram_id="7033", nome="Legado")
        legado = Encomenda.objects.create(utilizador=utilizador, total=Decimal("50.00"))  # pre-cart: no snapshot
        LinhaEncomenda.objects.create(encomenda=legado, produto=self.produto, quantidade=1,
                                      preco_unitario=Decimal("50.00"), subtotal=Decimal("50.00"))
        envelhecer(Encomenda, legado.pk, "data_encomenda", hours=2)
        self.correr("--horas", "1")
        self.assertEqual((Encomenda.objects.get(pk=legado.pk).estado, self.estado("recente")),
                         ("cancelada", "cancelada"))

    def test_second_run_changes_nothing(self):
        self.correr()
        saida = self.correr()
        self.assertEqual(self.post.call_count, 2)
        self.assertEqual(MovimentoStock.objects.filter(tipo_movimento="entrada").count(), 2)
        self.assertIn("0 confirmada(s), 0 cancelada(s), 0 erro(s)", saida)

    def test_order_settled_meanwhile_is_skipped(self):
        Encomenda.objects.filter(pk=self.encomendas["velha"].pk).update(estado="confirmada")
        self.assertEqual(services.reconciliar_encomenda(self.encomendas["velha"].id), (None, None))


class CancelarAbandonadasArgumentosTests(TestCase):
    """--horas below 1 would move the cutoff to now or later and cancel orders still being paid."""

    def test_zero_or_negative_hours_are_rejected_before_any_change(self):
        from django.core.management import CommandError, call_command
        produto = criar_produto(stock=5)
        _, encomenda = reservar(utilizador_telegram(7040), produto.id)
        for horas in ("0", "-1"):
            with self.assertRaisesMessage(CommandError, "--horas"):
                call_command("cancelar_encomendas_abandonadas", "--horas", horas, stdout=MagicMock())
        self.assertEqual(Encomenda.objects.get(pk=encomenda.pk).estado, "pendente")


# ── Stage 1: categories in the database ────────────────────────────────────

class CategoriaServicosTests(TestCase):

    def test_active_categories_follow_ordem_then_name(self):
        Categoria.objects.create(nome="Zeta", ordem=1)
        Categoria.objects.create(nome="Alfa", ordem=2)
        Categoria.objects.create(nome="Beta", ordem=1)
        Categoria.objects.create(nome="Oculta", ordem=0, ativo=False)
        nomes = [c.nome for c in async_to_sync(services.get_categorias_ativas)()]
        self.assertEqual(nomes, ["Beta", "Zeta", "Alfa"])

    def test_inactive_or_missing_category_is_none(self):
        inativa = Categoria.objects.create(nome="Antiga", ativo=False)
        self.assertIsNone(async_to_sync(services.get_categoria_ativa)(inativa.pk))
        self.assertIsNone(async_to_sync(services.get_categoria_ativa)(inativa.pk + 1000))
        ativa = Categoria.objects.create(nome="Nova")
        self.assertEqual(async_to_sync(services.get_categoria_ativa)(ativa.pk), ativa)

    def test_products_of_inactive_category_are_hidden(self):
        cat = Categoria.objects.create(nome="Discos")
        produto = criar_produto(categoria=cat)
        criar_produto(nome="Inativo", categoria=cat, ativo=False)
        self.assertEqual(async_to_sync(services.get_produtos_por_categoria)(cat.pk), [produto])
        Categoria.objects.filter(pk=cat.pk).update(ativo=False)
        self.assertEqual(async_to_sync(services.get_produtos_por_categoria)(cat.pk), [])

    def test_all_products_sorted_by_category_order(self):
        primeira = Categoria.objects.create(nome="Primeira", ordem=1)
        segunda = Categoria.objects.create(nome="Segunda", ordem=2)
        criar_produto(nome="A", categoria=segunda)
        criar_produto(nome="B", categoria=primeira)
        nomes = [p.nome for p in async_to_sync(services.get_todos_produtos)()]
        self.assertEqual(nomes, ["B", "A"])

    def test_category_with_products_cannot_be_deleted(self):
        cat = Categoria.objects.create(nome="Protegida")
        criar_produto(categoria=cat)
        with self.assertRaises(ProtectedError):
            cat.delete()


class CategoriaMigracaoTests(TransactionTestCase):
    """0010 maps the old category codes to Categoria rows, run forward on real rows."""

    ANTES = [("core", "0009_categoria")]

    def setUp(self):
        # 0010 can't be reversed (forward-fix only). Only this test steps back over it,
        # to an empty database, so its reverse is a no-op here.
        operacao = importlib.import_module("core.migrations.0010_categoria_dados").Migration.operations[0]
        patcher = patch.object(operacao, "reverse_code", migrations.RunPython.noop)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.executor = MigrationExecutor(connection)
        self.addCleanup(self._para_o_fim)
        self.executor.migrate(self.ANTES)

    def _para_o_fim(self):
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())

    def test_codes_become_categories(self):
        antigos = self.executor.loader.project_state(self.ANTES).apps
        ProdutoAntigo = antigos.get_model("core", "Produto")
        for nome, codigo in [("Ryzen", "cpu"), ("Intel", "cpu"), ("SSD", "ssd"), ("Monitor", "monitor")]:
            ProdutoAntigo.objects.create(nome=nome, preco=Decimal("10.00"), categoria=codigo)

        self.executor = MigrationExecutor(connection)
        self.executor.migrate(self.executor.loader.graph.leaf_nodes())

        categorias = {c.nome: c.ordem for c in Categoria.objects.all()}
        self.assertEqual(categorias, {"Processadores": 0, "Armazenamento": 3, "monitor": 99})
        por_produto = dict(Produto.objects.values_list("nome", "categoria__nome"))
        self.assertEqual(por_produto, {"Ryzen": "Processadores", "Intel": "Processadores",
                                       "SSD": "Armazenamento", "Monitor": "monitor"})


# ── Stage 3: the cart ──────────────────────────────────────────────────────

class CarrinhoTests(TestCase):

    def setUp(self):
        self.cliente = utilizador_telegram(8001, "Cliente Carrinho")
        self.produto = criar_produto(stock=3)

    def add(self, produto=None, cliente=None):
        return services.adicionar_ao_carrinho(cliente or self.cliente, (produto or self.produto).id)

    def carrinho(self, cliente=None):
        return Carrinho.objects.get(utilizador__telegram_id=str((cliente or self.cliente).id))

    def linhas(self, cliente=None):
        return services.ver_carrinho(cliente or self.cliente)[0]

    def test_add_stops_at_stock_then_says_why(self):
        self.assertEqual([self.add() for _ in range(4)],
                         [("adicionado", 1), ("adicionado", 2), ("adicionado", 3), ("limite_stock", 3)])
        self.assertEqual(self.carrinho().revisao, 3)
        self.assertFalse(Encomenda.objects.exists())
        self.assertEqual(Produto.objects.get(pk=self.produto.pk).stock_atual, 3)  # the cart reserves nothing

    def test_add_limits_per_line_lines_and_availability(self):
        muito = criar_produto(nome="Muito", stock=500)
        self.add(muito)
        LinhaCarrinho.objects.filter(produto=muito).update(quantidade=services.MAX_QUANTIDADE)
        self.assertEqual(self.add(muito), ("limite_quantidade", 99))
        for i in range(services.MAX_LINHAS - 1):
            self.assertEqual(self.add(criar_produto(nome=f"P{i}"))[0], "adicionado")
        self.assertEqual(self.add(), ("limite_linhas", 0))
        inativo = criar_produto(nome="Inativo", ativo=False)
        esgotado = criar_produto(nome="Esgotado", stock=0)
        self.assertEqual([self.add(inativo)[0], self.add(esgotado)[0]], ["indisponivel", "indisponivel"])
        self.assertEqual(LinhaCarrinho.objects.count(), services.MAX_LINHAS)

    def test_minus_to_zero_and_remove_delete_the_line(self):
        self.add()
        self.add()
        self.assertEqual(services.alterar_quantidade(self.cliente, self.produto.id, -1), ("adicionado", 1))
        self.assertEqual(services.alterar_quantidade(self.cliente, self.produto.id, -1), ("removido", 0))
        self.assertEqual(services.alterar_quantidade(self.cliente, self.produto.id, 1), ("inexistente", 0))
        self.add()
        self.assertEqual(services.remover_do_carrinho(self.cliente, self.produto.id), "removido")
        self.assertEqual(services.remover_do_carrinho(self.cliente, self.produto.id), "inexistente")
        self.assertEqual(self.linhas(), [])

    def test_flags_and_which_ones_block_checkout(self):
        caro, sumido, curto = (criar_produto(nome=n, stock=5) for n in ("Caro", "Sumido", "Curto"))
        for p in (caro, sumido, curto):
            self.add(p)
        self.add(curto)
        Produto.objects.filter(pk=caro.pk).update(preco=Decimal("60.00"))
        Produto.objects.filter(pk=sumido.pk).update(ativo=False)
        Produto.objects.filter(pk=curto.pk).update(stock_atual=1)
        por_nome = {l.nome: l for l in self.linhas()}
        self.assertEqual((por_nome["Caro"].preco_alterado, por_nome["Caro"].bloqueia), (True, False))
        self.assertEqual((por_nome["Sumido"].indisponivel, por_nome["Sumido"].bloqueia), (True, True))
        self.assertEqual((por_nome["Curto"].stock_insuficiente, por_nome["Curto"].bloqueia), (True, True))
        resultado, problemas, _ = services.preparar_confirmacao_carrinho(self.cliente)
        self.assertEqual((resultado, sorted(p.tipo for p in problemas)), ("bloqueado", ["inativo", "stock"]))
        self.assertFalse(ConfirmacaoCarrinho.objects.exists())

    def test_viewing_changes_nothing(self):
        self.add()
        envelhecer(Carrinho, self.carrinho().pk, "atualizado_em", days=29)
        antes = model_to_dict(self.carrinho())
        linhas, total = services.ver_carrinho(self.cliente)
        self.assertEqual((len(linhas), total), (1, Decimal("50.00")))
        self.assertEqual(model_to_dict(self.carrinho()), antes)
        self.assertEqual(services.ver_carrinho(utilizador_telegram(8999)), ([], Decimal("0.00")))
        self.assertFalse(Utilizador.objects.filter(telegram_id="8999").exists())

    def test_whole_cart_becomes_one_order_and_only_that_cart_empties(self):
        outro = criar_produto(nome="RAM", preco=Decimal("19.99"), stock=4)
        self.add()
        self.add()
        self.add(outro)
        vizinho = utilizador_telegram(8002, "Vizinho")
        self.add(cliente=vizinho)
        Produto.objects.filter(pk=outro.pk).update(preco=Decimal("21.00"))
        resultado, snap, alterados = services.preparar_confirmacao_carrinho(self.cliente)
        self.assertEqual((resultado, alterados), ("ok", {outro.id}))
        self.assertEqual((snap.revisao_carrinho, snap.total_centimos), (self.carrinho().revisao, 12100))
        resultado, encomenda = services.criar_encomenda_de_confirmacao(self.cliente, snap.nonce)
        self.assertEqual((resultado, encomenda.total), ("criada", Decimal("121.00")))
        self.assertEqual(encomenda.linhas.count(), 2)
        self.assertEqual(self.linhas(), [])
        self.assertEqual(self.carrinho().revisao, snap.revisao_carrinho + 1)
        self.assertEqual(len(self.linhas(vizinho)), 1)

    def test_cart_changed_after_the_screen_gives_alterado(self):
        self.add()
        _, snap, _ = services.preparar_confirmacao_carrinho(self.cliente)
        self.add()
        resultado, alteracoes = services.criar_encomenda_de_confirmacao(self.cliente, snap.nonce)
        self.assertEqual((resultado, [a.tipo for a in alteracoes]), ("alterado", ["carrinho"]))
        self.assertEqual(contagens(), (0, 0, 0, 0))
        self.assertEqual(self.linhas()[0].quantidade, 2)

    def test_empty_cart_has_nothing_to_confirm(self):
        self.assertEqual(services.preparar_confirmacao_carrinho(self.cliente), ("vazio", None, set()))


class RepetirEncomendaTests(TestCase):

    def setUp(self):
        self.cliente = utilizador_telegram(8101, "Repete")
        self.produto = criar_produto(stock=10)

    def cancelada(self, *linhas, cliente=None):
        cliente = cliente or self.cliente
        _, encomenda = services.criar_encomenda_de_confirmacao(cliente, ecra(cliente, *linhas).nonce)
        Encomenda.objects.filter(pk=encomenda.pk).update(estado="cancelada")
        return Encomenda.objects.select_related("utilizador").get(pk=encomenda.pk)

    def quantidades(self):
        return {l.nome: l.quantidade for l in services.ver_carrinho(self.cliente)[0]}

    def test_merges_with_an_existing_line_capped_by_stock(self):
        encomenda = self.cancelada((self.produto, 4))
        Produto.objects.filter(pk=self.produto.pk).update(stock_atual=5)
        services.adicionar_ao_carrinho(self.cliente, self.produto.id)
        services.adicionar_ao_carrinho(self.cliente, self.produto.id)
        revisao = Carrinho.objects.get().revisao
        self.assertEqual(services.repetir_encomenda(self.cliente, encomenda.id), ([], ["SSD 1TB"], []))
        self.assertEqual(self.quantidades(), {"SSD 1TB": 5})  # 2 + 4 wanted, 5 in stock
        self.assertEqual(Carrinho.objects.get().revisao, revisao + 1)
        self.assertFalse(Encomenda.objects.filter(estado="pendente").exists())  # nothing is ordered

    def test_skips_unavailable_products_and_lines_beyond_twenty(self):
        inativo, esgotado = criar_produto(nome="Inativo"), criar_produto(nome="Esgotado")
        encomenda = self.cancelada((self.produto, 1), (inativo, 1), (esgotado, 1))
        Produto.objects.filter(pk=inativo.pk).update(ativo=False)
        Produto.objects.filter(pk=esgotado.pk).update(stock_atual=0)
        self.assertEqual(services.repetir_encomenda(self.cliente, encomenda.id),
                         (["SSD 1TB"], [], ["Inativo", "Esgotado"]))
        services.remover_do_carrinho(self.cliente, self.produto.id)
        for i in range(services.MAX_LINHAS):
            services.adicionar_ao_carrinho(self.cliente, criar_produto(nome=f"P{i}").id)
        self.assertEqual(services.repetir_encomenda(self.cliente, encomenda.id)[2], ["SSD 1TB", "Inativo", "Esgotado"])
        self.assertEqual(len(self.quantidades()), services.MAX_LINHAS)

    def test_only_own_cancelled_unpaid_orders_can_be_repeated(self):
        encomenda = self.cancelada((self.produto, 1))
        self.assertTrue(async_to_sync(services.pode_repetir)(encomenda, self.cliente.id))
        self.assertFalse(async_to_sync(services.pode_repetir)(encomenda, 8102))
        self.assertIsNone(services.repetir_encomenda(utilizador_telegram(8102, "Outro"), encomenda.id))
        pagamento = Pagamento.objects.create(encomenda=encomenda, metodo="stripe", valor=encomenda.total,
                                             estado_pagamento="falhado", stripe_session_id="cs_r")
        self.assertTrue(async_to_sync(services.pode_repetir)(encomenda, self.cliente.id))
        for estado in ("pago", "reembolsado"):
            Pagamento.objects.filter(pk=pagamento.pk).update(estado_pagamento=estado)
            self.assertFalse(async_to_sync(services.pode_repetir)(encomenda, self.cliente.id))
            self.assertIsNone(services.repetir_encomenda(self.cliente, encomenda.id))
        Pagamento.objects.all().delete()
        Encomenda.objects.filter(pk=encomenda.pk).update(estado="pendente")
        self.assertIsNone(services.repetir_encomenda(self.cliente, encomenda.id))
        self.assertIsNone(services.repetir_encomenda(self.cliente, 999999))


class LimpezaRascunhosTests(TestCase):

    def test_old_unused_screens_and_idle_carts_are_deleted(self):
        produto = criar_produto(stock=5)
        clientes = [utilizador_telegram(8200 + i) for i in range(4)]
        recente, velho, ligado = (ecra(clientes[0], (produto, 1)) for _ in range(3))
        envelhecer(ConfirmacaoCarrinho, recente.pk, "criada_em", minutes=44)
        envelhecer(ConfirmacaoCarrinho, velho.pk, "criada_em", minutes=46)
        services.criar_encomenda_de_confirmacao(clientes[0], ligado.nonce)
        envelhecer(ConfirmacaoCarrinho, ligado.pk, "criada_em", days=3)
        for cliente, dias in ((clientes[1], 29), (clientes[2], 31)):
            services.adicionar_ao_carrinho(cliente, produto.id)
            envelhecer(Carrinho, Carrinho.objects.get(utilizador__telegram_id=str(cliente.id)).pk,
                       "atualizado_em", days=dias)
        self.assertEqual(services.limpar_rascunhos(), (1, 1))
        self.assertEqual(set(ConfirmacaoCarrinho.objects.values_list("pk", flat=True)), {recente.pk, ligado.pk})
        self.assertEqual(list(Carrinho.objects.values_list("utilizador__telegram_id", flat=True)), [str(clientes[1].id)])
        self.assertEqual(LinhaCarrinho.objects.count(), 1)

    def test_the_cron_command_reports_the_deleted_drafts(self):
        from io import StringIO
        from django.core.management import call_command
        snap = ecra(utilizador_telegram(8210), (criar_produto(stock=5), 1))
        envelhecer(ConfirmacaoCarrinho, snap.pk, "criada_em", hours=1)
        saida = StringIO()
        call_command("cancelar_encomendas_abandonadas", "--horas", "1", stdout=saida)
        self.assertIn("1 ecrã(s) e 0 carrinho(s) apagados", saida.getvalue())


# ── Stage 4: shop settings, admin rights and owner permissions ─────────────

class LojaTests(TestCase):

    def test_settings_row_has_neutral_defaults_and_stays_single(self):
        loja = services.obter_loja()
        self.assertEqual((loja.pk, loja.nome), (1, "Loja"))
        self.assertTrue(loja.texto_boas_vindas)
        Loja(nome="Outra").save()
        self.assertEqual(list(Loja.objects.values_list("pk", "nome")), [(1, "Outra")])

    def test_django_admin_list_opens_the_single_row_with_no_add_or_delete(self):
        from django.contrib import admin as django_admin
        from django.test import RequestFactory
        from core.admin import LojaAdmin
        modelo = LojaAdmin(Loja, django_admin.site)
        pedido = RequestFactory().get("/admin/core/loja/")
        self.assertFalse(modelo.has_add_permission(pedido))
        self.assertFalse(modelo.has_delete_permission(pedido))
        resposta = modelo.changelist_view(pedido)
        self.assertEqual((resposta.status_code, resposta.url), (302, "/admin/core/loja/1/change/"))


class AdminTelegramTests(TestCase):

    def admin(self, user_id):
        return async_to_sync(services.e_admin)(user_id)

    def test_bootstrap_list_and_database_flag_both_grant_rights_at_once(self):
        with patch.object(services, "ADMINS_ARRANQUE", frozenset({111})):
            self.assertTrue(self.admin(111))
            self.assertFalse(self.admin(222))
            utilizador = Utilizador.objects.create(telegram_id="222", nome="Dono", e_admin=True)
            self.assertTrue(self.admin(222))
            Utilizador.objects.filter(pk=utilizador.pk).update(e_admin=False)
            self.assertFalse(self.admin(222))  # the next check sees the change, no restart
            Utilizador.objects.filter(pk=utilizador.pk).update(e_admin=True, ativo=False)
            self.assertFalse(self.admin(222))  # a blocked user is not an admin

    def test_only_a_superuser_can_change_the_admin_flag(self):
        from django.contrib import admin as django_admin
        from core.admin import UtilizadorAdmin
        modelo = UtilizadorAdmin(Utilizador, django_admin.site)
        staff = SimpleNamespace(user=SimpleNamespace(is_superuser=False))
        superuser = SimpleNamespace(user=SimpleNamespace(is_superuser=True))
        self.assertIn("e_admin", modelo.get_readonly_fields(staff))
        self.assertNotIn("e_admin", modelo.get_readonly_fields(superuser))


@override_settings(TELEGRAM_TOKEN="test-token", **URLS_A)
class DonoDaLojaTests(TestCase):
    """The owner's Django account: catalogue yes, order actions no, enforced on the server."""

    def setUp(self):
        from django.contrib.auth.models import Group, User
        self.grupo = Group.objects.get(name="Dono da loja")
        self.dono = User.objects.create_user("dono", password="x", is_staff=True)
        self.dono.groups.add(self.grupo)
        self.produto = criar_produto(stock=5)
        _, self.encomenda = reservar(utilizador_telegram(8301), self.produto.id)
        Encomenda.objects.filter(pk=self.encomenda.pk).update(estado="confirmada")
        envio = patch("core.views.httpx.post", return_value=MagicMock(status_code=200))
        self.post = envio.start()
        self.addCleanup(envio.stop)

    def test_group_has_exactly_the_listed_permissions(self):
        self.assertEqual(sorted(self.grupo.permissions.values_list("codename", flat=True)), sorted([
            "add_produto", "change_produto", "view_produto",
            "add_categoria", "change_categoria", "view_categoria",
            "change_loja", "view_loja",
            "view_encomenda", "view_linhaencomenda", "view_pagamento",
        ]))

    def test_a8_owner_cannot_post_order_actions(self):
        self.client.force_login(self.dono)
        url = "/admin/core/encomenda/"
        self.assertEqual(self.client.get(url).status_code, 200)  # can read orders
        for acao in ("marcar_cancelada", "marcar_enviada", "marcar_entregue"):
            self.client.post(url, {"action": acao, "_selected_action": [self.encomenda.pk]})
        self.assertEqual(Encomenda.objects.get(pk=self.encomenda.pk).estado, "confirmada")
        self.assertEqual(Produto.objects.get(pk=self.produto.pk).stock_atual, 4)
        self.assertFalse(RegistoAuditoria.objects.filter(acao__in=["CANCELAR_ENCOMENDA", "ENCOMENDA_ENVIADA"]).exists())
        self.post.assert_not_called()
        # Control: the same post from the reseller's superuser does run the action.
        from django.contrib.auth.models import User
        self.client.force_login(User.objects.create_superuser("revendedor", password="x"))
        self.client.post(url, {"action": "marcar_enviada", "_selected_action": [self.encomenda.pk]})
        self.assertEqual(Encomenda.objects.get(pk=self.encomenda.pk).estado, "enviada")

    def test_actions_are_listed_by_permission(self):
        from django.contrib import admin as django_admin
        from django.contrib.auth.models import User
        from django.test import RequestFactory
        from core.admin import EncomendaAdmin
        modelo = EncomendaAdmin(Encomenda, django_admin.site)
        pedido = RequestFactory().get("/admin/core/encomenda/")
        pedido.user = self.dono
        self.assertFalse({"marcar_cancelada", "marcar_enviada", "marcar_entregue"} & set(modelo.get_actions(pedido)))
        pedido.user = User.objects.create_superuser("revendedor", password="x")
        self.assertTrue({"marcar_cancelada", "marcar_enviada", "marcar_entregue"} <= set(modelo.get_actions(pedido)))

    def test_owner_can_add_a_category_but_not_delete_it(self):
        self.client.force_login(self.dono)
        resposta = self.client.post("/admin/core/categoria/add/", {"nome": "Monitores", "ordem": 5, "ativo": "on"})
        self.assertEqual(resposta.status_code, 302)
        categoria = Categoria.objects.get(nome="Monitores")
        self.assertEqual(self.client.post(f"/admin/core/categoria/{categoria.pk}/delete/", {"post": "yes"}).status_code, 403)
        self.assertTrue(Categoria.objects.filter(pk=categoria.pk).exists())


class DefinicoesHttpsTests(TestCase):

    def ler(self, **ambiente):
        import os
        import runpy
        from pathlib import Path
        with patch.dict(os.environ, ambiente):
            return runpy.run_path(str(Path(__file__).resolve().parent.parent / "config" / "settings.py"))

    def test_https_settings_switch_on_together(self):
        ligado = self.ler(DJANGO_HTTPS="True", DJANGO_BEHIND_PROXY="True",
                          CSRF_TRUSTED_ORIGINS="https://loja.example, https://www.loja.example")
        self.assertTrue(ligado["SECURE_SSL_REDIRECT"] and ligado["SESSION_COOKIE_SECURE"] and ligado["CSRF_COOKIE_SECURE"])
        self.assertEqual(ligado["SECURE_HSTS_SECONDS"], 31536000)
        self.assertEqual(ligado["SECURE_PROXY_SSL_HEADER"], ("HTTP_X_FORWARDED_PROTO", "https"))
        self.assertEqual(ligado["CSRF_TRUSTED_ORIGINS"], ["https://loja.example", "https://www.loja.example"])
        sem_proxy = self.ler(DJANGO_HTTPS="True", DJANGO_BEHIND_PROXY="False")
        self.assertNotIn("SECURE_PROXY_SSL_HEADER", sem_proxy)
        desligado = self.ler(DJANGO_HTTPS="False", CSRF_TRUSTED_ORIGINS="")
        self.assertNotIn("SECURE_SSL_REDIRECT", desligado)
        self.assertEqual(desligado["CSRF_TRUSTED_ORIGINS"], [])
