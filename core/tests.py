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
