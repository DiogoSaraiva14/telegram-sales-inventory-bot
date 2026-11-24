"""PostgreSQL tests for Change 2 (concepts brief §3.7, §3.9, §3.10).

Run with: python manage.py test core   (needs the telegram_bot_test role, see README).
"""
import secrets
import threading
import time
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import stripe
from asgiref.sync import sync_to_async
from django.db import DatabaseError, IntegrityError, connection, transaction
from django.db.models.query import QuerySet
from django.forms.models import model_to_dict
from django.test import TestCase, TransactionTestCase, override_settings

from core import services
from core.models import (
    Encomenda, LinhaEncomenda, MovimentoStock, Pagamento, Produto, RegistoAuditoria, Utilizador,
)
from telegram_bot import confirmacao
from telegram_bot.constants import MENSAGEM_JA_REGISTADA
from telegram_bot.handlers import callbacks, customer

URLS_A = dict(STRIPE_SUCCESS_URL="https://loja.example/sucesso", STRIPE_CANCEL_URL="https://loja.example/cancelar")
URLS_B = dict(STRIPE_SUCCESS_URL="https://loja.example/v2/sucesso", STRIPE_CANCEL_URL="https://loja.example/v2/cancelar")


def utilizador_telegram(tid=1001, nome="Cliente Teste"):
    return SimpleNamespace(id=tid, full_name=nome, username="cliente")


def criar_produto(stock=5, **campos):
    return Produto.objects.create(
        nome=campos.pop("nome", "SSD 1TB"), preco=campos.pop("preco", Decimal("50.00")),
        stock_atual=stock, categoria="ssd", **campos,
    )


def nova_chave():
    return secrets.token_hex(8)


def sessao(id_, status="open"):
    return SimpleNamespace(id=id_, url=f"https://checkout.example/{id_}", status=status, payment_status="unpaid")


def contagens():
    return (Encomenda.objects.count(), LinhaEncomenda.objects.count(), MovimentoStock.objects.count(),
            RegistoAuditoria.objects.filter(acao="CRIAR_ENCOMENDA").count())


# ── reservar_encomenda (DB1–DB11) ──────────────────────────────────────────

class ReservarEncomendaTests(TestCase):

    def setUp(self):
        self.produto = criar_produto(stock=5)
        self.cliente = utilizador_telegram()

    def stock(self):
        return Produto.objects.get(pk=self.produto.pk).stock_atual

    def test_db1_same_token_twice_creates_one_order(self):
        chave = nova_chave()
        r1, e1 = services.reservar_encomenda(self.cliente, self.produto.id, chave)
        r2, e2 = services.reservar_encomenda(self.cliente, self.produto.id, chave)
        self.assertEqual((r1, r2), ("criada", "existente"))
        self.assertEqual(e1.id, e2.id)
        self.assertEqual(contagens(), (1, 1, 1, 1))
        self.assertEqual(MovimentoStock.objects.get().tipo_movimento, "saida")
        self.assertEqual(self.stock(), 4)

    def test_db2_unique_index_path_when_lookup_misses(self):
        chave = nova_chave()
        _, original = services.reservar_encomenda(self.cliente, self.produto.id, chave)
        real = services._encomenda_por_chave
        chamadas = []

        def falha_uma_vez(c):
            chamadas.append(c)
            return None if len(chamadas) == 1 else real(c)

        with patch.object(services, "_encomenda_por_chave", side_effect=falha_uma_vez):
            resultado, encomenda = services.reservar_encomenda(self.cliente, self.produto.id, chave)
        self.assertEqual(resultado, "existente")
        self.assertEqual(encomenda.id, original.id)
        self.assertEqual(len(chamadas), 2)  # the miss, then the lookup after the unique violation
        self.assertEqual(contagens(), (1, 1, 1, 1))
        self.assertEqual(self.stock(), 4)
        self.assertEqual(Encomenda.objects.filter(chave_confirmacao=chave).count(), 1)  # transaction still usable

    def test_db3_token_of_other_user_reveals_and_creates_nothing(self):
        chave = nova_chave()
        services.reservar_encomenda(self.cliente, self.produto.id, chave)
        resultado, _ = services.reservar_encomenda(utilizador_telegram(2002, "Outra"), self.produto.id, chave)
        self.assertEqual(resultado, "de_outro_utilizador")
        self.assertEqual(contagens(), (1, 1, 1, 1))
        self.assertEqual(self.stock(), 4)

    def test_db4_two_screens_create_two_orders(self):
        services.reservar_encomenda(self.cliente, self.produto.id, nova_chave())
        services.reservar_encomenda(self.cliente, self.produto.id, nova_chave())
        self.assertEqual(contagens(), (2, 2, 2, 2))
        self.assertEqual(self.stock(), 3)

    def test_db5_no_stock_refuses_without_writing(self):
        Produto.objects.filter(pk=self.produto.pk).update(stock_atual=0)
        resultado, encomenda = services.reservar_encomenda(self.cliente, self.produto.id, nova_chave())
        self.assertEqual((resultado, encomenda), ("sem_stock", None))
        self.assertEqual(contagens(), (0, 0, 0, 0))
        self.assertEqual(self.stock(), 0)

    def test_db6_failed_audit_rolls_back_everything(self):
        with patch.object(RegistoAuditoria.objects, "create", side_effect=DatabaseError("audit down")):
            with self.assertRaises(DatabaseError):
                services.reservar_encomenda(self.cliente, self.produto.id, nova_chave())
        self.assertEqual(contagens(), (0, 0, 0, 0))
        self.assertEqual(self.stock(), 5)

    def test_db7_missing_image_file_does_not_break_checkout(self):
        Produto.objects.filter(pk=self.produto.pk).update(imagem="produtos/em-falta.jpg")
        resultado, _ = services.reservar_encomenda(self.cliente, self.produto.id, nova_chave())
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

    def test_db9_retry_after_committed_attempt_returns_same_order(self):
        chave = nova_chave()
        _, primeira = services.reservar_encomenda(self.cliente, self.produto.id, chave)
        antes = contagens()
        resultado, segunda = services.reservar_encomenda(self.cliente, self.produto.id, chave)
        self.assertEqual((resultado, segunda.id), ("existente", primeira.id))
        self.assertEqual(contagens(), antes)
        self.assertEqual(self.stock(), 4)

    def test_db10_retry_after_failed_attempt_creates_exactly_one(self):
        chave = nova_chave()
        real = RegistoAuditoria.objects.create
        falhas = []

        def falha_uma_vez(**campos):
            if not falhas:
                falhas.append(1)
                raise DatabaseError("audit down")
            return real(**campos)

        with patch.object(RegistoAuditoria.objects, "create", side_effect=falha_uma_vez):
            with self.assertRaises(DatabaseError):
                services.reservar_encomenda(self.cliente, self.produto.id, chave)
            resultado, _ = services.reservar_encomenda(self.cliente, self.produto.id, chave)
        self.assertEqual(resultado, "criada")
        self.assertEqual(contagens(), (1, 1, 1, 1))

    def test_db11_line_keeps_product_name_from_order_time(self):
        _, encomenda = services.reservar_encomenda(self.cliente, self.produto.id, nova_chave())
        Produto.objects.filter(pk=self.produto.pk).update(nome="SSD 1TB (novo nome)")
        self.assertEqual(encomenda.linhas.get().nome_produto, "SSD 1TB")


# ── garantir_sessao_pagamento (S1–S22) ─────────────────────────────────────

@override_settings(**URLS_A)
class SessaoPagamentoTests(TestCase):

    def setUp(self):
        self.produto = criar_produto(stock=5, descricao="Descrição original")
        _, self.encomenda = services.reservar_encomenda(utilizador_telegram(), self.produto.id, nova_chave())
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
        _, outra = services.reservar_encomenda(utilizador_telegram(), self.produto.id, nova_chave())
        chave = services.chave_primeira_tentativa(self.encomenda)
        self.assertEqual(chave, services.chave_primeira_tentativa(Encomenda.objects.get(pk=self.encomenda.pk)))
        self.assertNotEqual(chave, services.chave_primeira_tentativa(outra))
        for k in (chave, services.chave_substituicao(self.encomenda, "cs_" + "x" * 250)):
            self.assertLessEqual(len(k), 255)

    def test_s8_request_uses_saved_order_data_only(self):
        antes = services.parametros_checkout(self.encomenda)
        Produto.objects.filter(pk=self.produto.pk).update(
            nome="Outro nome", preco=Decimal("99.00"), descricao="Outra descrição")
        depois = services.parametros_checkout(Encomenda.objects.get(pk=self.encomenda.pk))
        self.assertEqual(antes, depois)
        item = depois["line_items"][0]["price_data"]
        self.assertEqual(item["unit_amount"], 5000)
        self.assertEqual(item["product_data"], {"name": "SSD 1TB"})

    def test_s9_expired_session_is_replaced_with_new_key(self):
        self.pagamento(stripe_session_id="cs_old")
        self.retrieve.return_value = sessao("cs_old", "expired")
        self.create.return_value = sessao("cs_new")
        self.assertEqual(services.garantir_sessao_pagamento(self.encomenda),
                         ("aberta", "https://checkout.example/cs_new"))
        self.assertEqual(self.chaves_usadas(), [services.chave_substituicao(self.encomenda, "cs_old")])
        linha = Pagamento.objects.get()
        self.assertEqual((linha.stripe_session_id, linha.estado_pagamento), ("cs_new", "pendente"))

    def test_s10_failed_replacement_save_then_retry_reuses_replacement_key(self):
        self.pagamento(stripe_session_id="cs_old")
        self.retrieve.return_value = sessao("cs_old", "expired")
        self.create.return_value = sessao("cs_new")
        with self.falhar_update_uma_vez():
            with self.assertRaises(DatabaseError):
                services.garantir_sessao_pagamento(self.encomenda)
            self.assertEqual(Pagamento.objects.get().stripe_session_id, "cs_old")
            services.garantir_sessao_pagamento(self.encomenda)
        chaves = self.chaves_usadas()
        self.assertEqual(chaves, [services.chave_substituicao(self.encomenda, "cs_old")] * 2)
        self.assertEqual(Pagamento.objects.get().stripe_session_id, "cs_new")

    def test_s11_second_expiry_gets_a_different_key(self):
        self.pagamento(stripe_session_id="cs_old")
        self.retrieve.return_value = sessao("cs_old", "expired")
        self.create.return_value = sessao("cs_new")
        services.garantir_sessao_pagamento(self.encomenda)
        self.retrieve.return_value = sessao("cs_new", "expired")
        self.create.return_value = sessao("cs_newer")
        services.garantir_sessao_pagamento(self.encomenda)
        primeira, segunda = self.chaves_usadas()
        self.assertNotEqual(primeira, segunda)
        self.assertEqual(segunda, services.chave_substituicao(self.encomenda, "cs_new"))
        self.assertEqual(Pagamento.objects.get().stripe_session_id, "cs_newer")

    def test_s12_failed_payment_with_expired_session_goes_back_to_pending(self):
        self.pagamento(stripe_session_id="cs_old", estado_pagamento="falhado")
        self.retrieve.return_value = sessao("cs_old", "expired")
        self.create.return_value = sessao("cs_new")
        services.garantir_sessao_pagamento(self.encomenda)
        self.assertEqual(Pagamento.objects.get().estado_pagamento, "pendente")

    def test_s13_lost_replacement_race_uses_the_stored_session(self):
        pagamento = self.pagamento(stripe_session_id="cs_old")
        self.retrieve.side_effect = lambda id_: sessao(id_, "expired" if id_ == "cs_old" else "open")

        def outro_trabalhador_substitui(**kwargs):
            Pagamento.objects.filter(pk=pagamento.pk).update(stripe_session_id="cs_other")
            return sessao("cs_mine")

        self.create.side_effect = outro_trabalhador_substitui
        self.assertEqual(services.garantir_sessao_pagamento(self.encomenda),
                         ("aberta", "https://checkout.example/cs_other"))
        self.assertEqual(self.create.call_count, 1)
        self.assertEqual(Pagamento.objects.get().stripe_session_id, "cs_other")

    def test_s14_replacement_sends_same_parameters_as_first_attempt(self):
        services.garantir_sessao_pagamento(self.encomenda)  # first attempt, records cs_1
        self.retrieve.return_value = sessao("cs_1", "expired")
        self.create.return_value = sessao("cs_2")
        services.garantir_sessao_pagamento(self.encomenda)
        primeiro, substituicao = (dict(c.kwargs) for c in self.create.call_args_list)
        self.assertNotEqual(primeiro.pop("idempotency_key"), substituicao.pop("idempotency_key"))
        self.assertEqual(primeiro, substituicao)

    def test_s15_idempotency_error_is_raised_and_changes_nothing(self):
        pagamento = self.pagamento(stripe_session_id="cs_old")
        antes = model_to_dict(pagamento)
        self.retrieve.return_value = sessao("cs_old", "expired")
        self.create.side_effect = stripe.IdempotencyError("Keys for idempotent requests can only be used with the same parameters")
        with self.assertRaises(stripe.IdempotencyError):
            services.garantir_sessao_pagamento(self.encomenda)
        self.assertEqual(model_to_dict(Pagamento.objects.get(pk=pagamento.pk)), antes)

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

    def test_s19_changed_urls_during_replacement_give_new_replacement_key(self):
        self.pagamento(stripe_session_id="cs_old")
        self.retrieve.return_value = sessao("cs_old", "expired")
        self.create.side_effect = [sessao("cs_a"), sessao("cs_b")]
        with self.falhar_update_uma_vez():
            with self.assertRaises(DatabaseError):
                services.garantir_sessao_pagamento(self.encomenda)
            with override_settings(**URLS_B):
                services.garantir_sessao_pagamento(self.encomenda)
                chave_b = services.chave_substituicao(self.encomenda, "cs_old")
        primeira, segunda = self.chaves_usadas()
        self.assertEqual(primeira, services.chave_substituicao(self.encomenda, "cs_old"))
        self.assertEqual(segunda, chave_b)
        self.assertNotEqual(primeira, segunda)
        self.assertEqual(Pagamento.objects.get().stripe_session_id, "cs_b")

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

    def test_s24_cancelled_during_replacement_keeps_old_session(self):
        self.pagamento(stripe_session_id="cs_old")
        self.retrieve.return_value = sessao("cs_old", "expired")
        self.create.side_effect = lambda **kw: self.cancelar_encomenda() or sessao("cs_new")
        self.assertEqual(services.garantir_sessao_pagamento(self.encomenda), ("nao_ativa", None))
        self.assertEqual(Pagamento.objects.get().stripe_session_id, "cs_old")

    def test_s25_cancelled_before_open_session_is_returned(self):
        self.pagamento(stripe_session_id="cs_open")
        self.retrieve.side_effect = lambda id_: self.cancelar_encomenda() or sessao(id_, "open")
        self.assertEqual(services.garantir_sessao_pagamento(self.encomenda), ("nao_ativa", None))

    def test_s22_unchanged_webhook_confirms_replaced_session_and_ignores_old_id(self):
        from core.views import _handle_payment_success

        self.pagamento(stripe_session_id="cs_old")
        self.retrieve.return_value = sessao("cs_old", "expired")
        self.create.return_value = sessao("cs_new")
        services.garantir_sessao_pagamento(self.encomenda)
        with patch("core.views.httpx.post"), self.assertLogs("core.views", level="ERROR") as logs:
            _handle_payment_success({"id": "cs_old"})
        self.assertIn("Pagamento não encontrado", "\n".join(logs.output))
        self.assertEqual(Pagamento.objects.get().estado_pagamento, "pendente")
        with patch("core.views.httpx.post"):
            _handle_payment_success({"id": "cs_new"})
        self.assertEqual(Pagamento.objects.get().estado_pagamento, "pago")
        self.assertEqual(Encomenda.objects.get(pk=self.encomenda.pk).estado, "confirmada")


# ── Handler + services + PostgreSQL together (E1, E2) ──────────────────────

@override_settings(**URLS_A)
class CheckoutEndToEndTests(TestCase):

    def setUp(self):
        self.produto = criar_produto(stock=5)
        self.cliente = utilizador_telegram(4242)

    async def toque(self, data, momento):
        query = MagicMock()
        query.from_user = self.cliente
        query.answer = AsyncMock()
        query.message.photo = None
        query.message.document = None
        query.edit_message_text = AsyncMock()
        wrapped = callbacks._RespostaUnica(query)
        with patch.object(confirmacao, "agora", return_value=momento):
            await customer.confirmar_encomenda(wrapped, MagicMock(), data, self.cliente.id)
            await wrapped.answer()
        return query

    async def test_e1_two_taps_same_screen_one_order(self):
        agora = int(time.time())
        data = confirmacao.callback_confirmar(self.produto.id, nova_chave(), emitida_em=agora)
        with patch.object(stripe.checkout.Session, "create", return_value=sessao("cs_e1")) as create, \
             patch.object(stripe.checkout.Session, "retrieve", return_value=sessao("cs_e1", "open")):
            primeiro = await self.toque(data, agora)
            segundo = await self.toque(data, agora + 5)
        self.assertEqual(await sync_to_async(contagens)(), (1, 1, 1, 1))
        self.assertEqual(await sync_to_async(Pagamento.objects.count)(), 1)
        self.assertEqual(create.call_count, 1)
        primeiro.answer.assert_awaited_once_with()
        segundo.answer.assert_awaited_once_with(MENSAGEM_JA_REGISTADA, show_alert=True)

    async def test_e2_old_button_after_failed_stripe_recovers_payment(self):
        agora = int(time.time())
        data = confirmacao.callback_confirmar(self.produto.id, nova_chave(), emitida_em=agora)
        with patch.object(stripe.checkout.Session, "create",
                          side_effect=[stripe.APIConnectionError("down"), sessao("cs_e2")]) as create:
            with self.assertLogs("telegram_bot.handlers.customer", level="ERROR"):
                await self.toque(data, agora)
            self.assertEqual(await sync_to_async(Pagamento.objects.count)(), 0)
            segundo = await self.toque(data, agora + 45 * 60)
        self.assertEqual(await sync_to_async(contagens)(), (1, 1, 1, 1))
        segundo.answer.assert_awaited_once_with(MENSAGEM_JA_REGISTADA, show_alert=True)
        primeira_chave, segunda_chave = (c.kwargs["idempotency_key"] for c in create.call_args_list)
        self.assertEqual(primeira_chave, segunda_chave)
        texto = segundo.edit_message_text.await_args.kwargs["text"]
        self.assertIn("Clica no botão abaixo para completar o pagamento", texto)


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

    def correr_em_paralelo(self, pedido_a, pedido_b):
        """Thread A pauses while holding the product lock; prove B waits on it in PostgreSQL."""
        pids, resultados, tempos = {}, {}, {}
        a_tem_bloqueio, libertar_a = threading.Event(), threading.Event()
        real = services._encomenda_por_chave

        def procura(chave):
            nome = threading.current_thread().name
            if nome == "A" and not a_tem_bloqueio.is_set():
                a_tem_bloqueio.set()  # step 3 runs after step 2's SELECT ... FOR UPDATE
                if not libertar_a.wait(10):
                    raise RuntimeError("A não foi libertado")
            elif nome == "B":
                tempos.setdefault("b_passou_bloqueio", time.monotonic())
            return real(chave)

        def correr(nome, pedido):
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT pg_backend_pid()")
                    pids[nome] = cursor.fetchone()[0]
                resultados[nome] = services.reservar_encomenda(*pedido)
            except Exception as exc:  # reported by the assertions below
                resultados[nome] = exc
            finally:
                connection.close()

        with patch.object(services, "_encomenda_por_chave", side_effect=procura):
            thread_a = threading.Thread(target=correr, name="A", args=("A", pedido_a))
            thread_b = threading.Thread(target=correr, name="B", args=("B", pedido_b))
            thread_a.start()
            try:
                self.assertTrue(a_tem_bloqueio.wait(5), "A não chegou ao bloqueio do produto")
                thread_b.start()
                self.assertTrue(self.esperar_bloqueio(pids, "B", "A"), "B nunca ficou bloqueado por A")
                self.assertNotIn("B", resultados)
                with connection.cursor() as cursor:
                    cursor.execute("SELECT pg_blocking_pids(%s)", [pids["B"]])
                    self.assertIn(pids["A"], cursor.fetchone()[0])
            finally:
                tempos["libertado"] = time.monotonic()
                libertar_a.set()
                thread_a.join(10)
                if thread_b.is_alive() or thread_b.ident:
                    thread_b.join(10)
        self.assertFalse(thread_a.is_alive() or thread_b.is_alive(), "uma thread não terminou")
        self.assertGreater(tempos["b_passou_bloqueio"], tempos["libertado"])
        for nome in ("A", "B"):
            self.assertNotIsInstance(resultados[nome], Exception, resultados[nome])
        return resultados

    def test_t1_last_unit_two_screens_one_order_one_no_stock(self):
        produto = criar_produto(stock=1)
        a = (utilizador_telegram(5001, "Cliente A"), produto.id, nova_chave())
        b = (utilizador_telegram(5002, "Cliente B"), produto.id, nova_chave())
        Utilizador.objects.create(telegram_id="5001", nome="Cliente A")
        Utilizador.objects.create(telegram_id="5002", nome="Cliente B")
        resultados = self.correr_em_paralelo(a, b)
        self.assertEqual(resultados["A"][0], "criada")
        self.assertEqual(resultados["B"], ("sem_stock", None))
        self.assertEqual(contagens(), (1, 1, 1, 1))
        self.assertEqual(Produto.objects.get(pk=produto.pk).stock_atual, 0)

    def test_t2_same_screen_from_two_threads_one_order(self):
        produto = criar_produto(stock=5)
        Utilizador.objects.create(telegram_id="5003", nome="Cliente")
        pedido = (utilizador_telegram(5003, "Cliente"), produto.id, nova_chave())
        resultados = self.correr_em_paralelo(pedido, pedido)
        self.assertEqual(resultados["A"][0], "criada")
        self.assertEqual(resultados["B"][0], "existente")
        self.assertEqual(resultados["A"][1].id, resultados["B"][1].id)
        self.assertEqual(contagens(), (1, 1, 1, 1))
        self.assertEqual(Produto.objects.get(pk=produto.pk).stock_atual, 4)

    def test_t3_cancellation_in_progress_wins_over_session_save(self):
        """P1: an admin cancellation mid-transaction blocks the session save, then wins."""
        produto = criar_produto(stock=5)
        Utilizador.objects.create(telegram_id="5004", nome="Cliente")
        _, encomenda = services.reservar_encomenda(utilizador_telegram(5004, "Cliente"), produto.id, nova_chave())
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
        _, self.encomenda = services.reservar_encomenda(utilizador_telegram(7007), self.produto.id, nova_chave())
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
        _, self.encomenda = services.reservar_encomenda(utilizador_telegram(7008), criar_produto(stock=5).id, nova_chave())
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
        _, encomenda = services.reservar_encomenda(utilizador_telegram(5005, "Cliente"), criar_produto(stock=5).id, nova_chave())
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
        _, self.encomenda = services.reservar_encomenda(utilizador_telegram(7010), self.produto.id, nova_chave())
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
        _, enviada = services.reservar_encomenda(utilizador_telegram(7011), self.produto.id, nova_chave())
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
        _, encomenda = services.reservar_encomenda(utilizador_telegram(5006, "Cliente"), produto.id, nova_chave())
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
