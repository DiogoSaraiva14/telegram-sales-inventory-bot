"""Callback routing checks. Run with: python manage.py test telegram_bot"""
import inspect
import os
import tempfile
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from django.test import SimpleTestCase
from telegram.error import BadRequest, TimedOut

from security.sanitization import ALLOWED_EXACT, ALLOWED_PREFIXES, validar_callback
from telegram_bot import confirmacao, keyboards
from telegram_bot.constants import (
    MENSAGEM_ECRA_EXPIRADO, MENSAGEM_ENCOMENDA_PENDENTE, MENSAGEM_FALHA_REGISTO, MENSAGEM_JA_REGISTADA,
    MENSAGEM_MENU_EXPIRADO, MENSAGEM_PAGAMENTO_A_CONFIRMAR, MENSAGEM_PEDIDO_INVALIDO, MENSAGEM_PRAZO_TERMINADO,
)
from telegram_bot.handlers import admin, callbacks, customer
from telegram_bot.handlers.callbacks import encontrar_rota

# Allowed by the validator but never handled, same as the original bot.py.
UNROUTED_PREFIXES = {"cancelar_encomenda_"}


class StartupConfigTests(SimpleTestCase):
    def test_bot_refuses_to_start_with_missing_settings(self):
        from telegram_bot import app
        self.assertEqual(app.variaveis_em_falta({"TELEGRAM_TOKEN": "x", "STRIPE_SECRET_KEY": ""}),
                         ["STRIPE_SECRET_KEY", "STRIPE_SUCCESS_URL", "STRIPE_CANCEL_URL"])
        completo = {nome: "x" for nome in app.VARIAVEIS_OBRIGATORIAS}
        self.assertEqual(app.variaveis_em_falta(completo), [])
        self.assertEqual(app.variaveis_em_falta({**completo, "TELEGRAM_TOKEN": "   "}), ["TELEGRAM_TOKEN"])
        with patch.object(app, "variaveis_em_falta", return_value=["TELEGRAM_TOKEN"]), \
             self.assertRaisesMessage(SystemExit, "TELEGRAM_TOKEN"):
            app.main()


class RoutingTests(SimpleTestCase):
    def test_every_allowed_callback_has_one_route(self):
        for data in ALLOWED_EXACT:
            handler, _ = encontrar_rota(data)
            self.assertIsNotNone(handler, data)
        for prefix in ALLOWED_PREFIXES:
            handler, _ = encontrar_rota(prefix + "1_1")
            if prefix in UNROUTED_PREFIXES:
                self.assertIsNone(handler, prefix)
            else:
                self.assertIsNotNone(handler, prefix)

    def test_admin_flag_matches_callback_namespace(self):
        for data in list(ALLOWED_EXACT) + [p + "1_1" for p in ALLOWED_PREFIXES]:
            handler, admin_only = encontrar_rota(data)
            if handler is not None:
                self.assertEqual(admin_only, data.startswith("admin_"), data)

    def test_specific_routes(self):
        self.assertIs(encontrar_rota("admin_utilizadores")[0], admin.utilizadores)
        self.assertIs(encontrar_rota("admin_utilizador_42")[0], admin.utilizador_toggle)
        self.assertIs(encontrar_rota("admin_stock")[0], admin.stock)
        self.assertIs(encontrar_rota("admin_stock_adj_3_-5")[0], admin.stock_ajuste)
        self.assertIs(encontrar_rota("produto_7")[0], customer.produto_detalhe)
        self.assertIs(encontrar_rota("confirmar_encomenda_7")[0], customer.confirmar_encomenda)


class CallbackAnswerTests(SimpleTestCase):
    """Telegram accepts one answer per callback query; every path must send exactly one."""

    def setUp(self):
        self.query = MagicMock()
        self.query.from_user.id = 99
        self.query.answer = AsyncMock()

    async def _dispatch(self, data, *, admin_user=False, route=(None, False)):
        query = self.query
        query.data = data
        update = MagicMock(callback_query=query)
        raw = inspect.unwrap(callbacks.callback_handler)  # skip rate/session/block decorators
        with patch.object(callbacks, "is_admin", return_value=admin_user),              patch.object(callbacks, "encontrar_rota", return_value=route):
            await raw(update, MagicMock())
        return query

    async def test_invalid_callback_shows_alert_once(self):
        query = await self._dispatch("not_a_route")
        query.answer.assert_awaited_once_with("❌ Pedido inválido.", show_alert=True)

    async def test_non_admin_is_denied_with_one_alert(self):
        handler = AsyncMock()
        query = await self._dispatch("admin_menu", admin_user=False, route=(handler, True))
        handler.assert_not_awaited()
        query.answer.assert_awaited_once_with("⛔ Acesso negado.", show_alert=True)

    async def test_admin_reaches_handler_and_gets_plain_answer(self):
        handler = AsyncMock()
        query = await self._dispatch("admin_menu", admin_user=True, route=(handler, True))
        handler.assert_awaited_once()
        query.answer.assert_awaited_once_with()

    async def test_handler_alert_is_the_only_answer(self):
        async def handler(q, context, data, user_id):
            await q.answer("❌ Este produto não tem stock disponível.", show_alert=True)
        query = await self._dispatch("sem_stock", route=(handler, False))
        query.answer.assert_awaited_once_with("❌ Este produto não tem stock disponível.", show_alert=True)

    async def test_handler_error_still_answers_once(self):
        handler = AsyncMock(side_effect=RuntimeError("boom"))
        with self.assertRaises(RuntimeError):
            await self._dispatch("menu_inicio", route=(handler, False))
        self.query.answer.assert_awaited_once_with()


class OrderDetailTests(SimpleTestCase):
    """SEC-12: the order list's buttons must pass validation and reach the detail screen."""

    def test_order_list_buttons_pass_validation_and_route(self):
        encomenda = SimpleNamespace(id=1, total=Decimal("9.99"), estado="pendente")
        for row in keyboards.teclado_minhas_encomendas([encomenda]).inline_keyboard:
            for button in row:
                self.assertIsNotNone(encontrar_rota(validar_callback(button.callback_data))[0])
        self.assertIs(encontrar_rota("ver_encomenda_1")[0], customer.encomenda_detalhe)

    async def _detalhe(self, dono):
        encomenda = SimpleNamespace(
            id=1, estado="pendente", total=Decimal("9.99"), utilizador=SimpleNamespace(telegram_id=dono),
            data_encomenda=MagicMock(strftime=MagicMock(return_value="24/11/2026 10:00")),
        )
        query = MagicMock()
        query.answer = AsyncMock()
        responder = AsyncMock()
        with patch.object(customer.services, "get_encomenda_detalhe", AsyncMock(return_value=encomenda)), \
             patch.object(customer.services, "get_linhas_encomenda", AsyncMock(return_value=[
                 SimpleNamespace(produto=SimpleNamespace(nome="SSD"), quantidade=1, subtotal=Decimal("9.99"))])), \
             patch.object(customer, "responder", responder):
            wrapped = callbacks._RespostaUnica(query)
            await customer.encomenda_detalhe(wrapped, MagicMock(), "ver_encomenda_1", 99)
            await wrapped.answer()
        return query, responder

    async def test_owner_sees_order_detail(self):
        query, responder = await self._detalhe("99")
        self.assertIn("Encomenda #1", responder.await_args.args[2])
        query.answer.assert_awaited_once_with()

    async def test_other_user_is_denied(self):
        query, responder = await self._detalhe("123")
        responder.assert_not_awaited()
        query.answer.assert_awaited_once_with("⛔ Acesso negado.", show_alert=True)


NONCE = "21489ae5df01141f"
URL = "https://checkout.example/cs_test_1"


def _botoes(teclado):
    return [(b.text, b.callback_data or b.url) for row in teclado.inline_keyboard for b in row]


class CheckoutHandlerTests(SimpleTestCase):
    """Stage 2 Confirm branches: snapshot tokens, old tokens and every result. Mock-only: no database."""

    def setUp(self):
        self.calls = []
        self.query = MagicMock()
        self.query.from_user.id = 99
        self.query.answer = AsyncMock(side_effect=lambda *a, **k: self.calls.append("answer"))
        self.responder = AsyncMock(side_effect=lambda *a, **k: self.calls.append("responder"))
        self.encomenda = SimpleNamespace(id=5, total=Decimal("50.00"), utilizador=SimpleNamespace(telegram_id="99"))
        self.linhas = [SimpleNamespace(nome_produto="SSD", quantidade=1, subtotal=Decimal("50.00"), produto=None)]
        self.data = confirmacao.callback_confirmar(NONCE)

    async def _run(self, data, *, criar=None, existente=None, garantir=("aberta", URL)):
        self.criar = AsyncMock(side_effect=lambda *a: self.calls.append("criar") or (
            _raise(criar) if isinstance(criar, Exception) else criar or ("criada", self.encomenda)))
        self.garantir = AsyncMock(side_effect=lambda *a: self.calls.append("garantir") or (
            _raise(garantir) if isinstance(garantir, Exception) else garantir))
        self.lookup = AsyncMock(return_value=existente)
        wrapped = callbacks._RespostaUnica(self.query)
        with patch.object(customer.services, "get_encomenda_por_chave", self.lookup), \
             patch.object(customer.services, "criar_encomenda_de_confirmacao_async", self.criar), \
             patch.object(customer.services, "garantir_sessao_pagamento_async", self.garantir), \
             patch.object(customer.services, "get_linhas_encomenda", AsyncMock(return_value=self.linhas)), \
             patch.object(customer, "responder", self.responder):
            await customer.confirmar_encomenda(wrapped, MagicMock(), data, 99)
            await wrapped.answer()  # the router's fallback must never send a second answer

    def _ecra(self):
        args, kwargs = self.responder.await_args
        return args[2], _botoes(args[3]), kwargs.get("parse_mode", "Markdown")

    def test_snapshot_token_is_short_validated_and_routed(self):
        self.assertEqual(self.data, f"confirmar_encomenda_{NONCE}")
        self.assertEqual(len(self.data.encode("utf-8")), 36)
        self.assertEqual(validar_callback(self.data), self.data)
        self.assertEqual(confirmacao.ler_callback(self.data), ("snapshot", NONCE))
        self.assertIs(encontrar_rota(self.data)[0], customer.confirmar_encomenda)
        for nonce in (NONCE.upper(), NONCE[:-1], NONCE + "0"):
            with self.assertRaises(ValueError):
                confirmacao.callback_confirmar(nonce)

    def test_old_and_malformed_tokens_are_parsed_strictly(self):
        self.assertEqual(confirmacao.ler_callback(f"confirmar_encomenda_{NONCE}_0towlsj_7"), ("antigo", NONCE))
        for data in (f"confirmar_encomenda_{NONCE}_towlsj_7", f"confirmar_encomenda_{NONCE}_0TOWLSJ_7",
                     f"confirmar_encomenda_{NONCE}_0towlsj_7_8", f"confirmar_encomenda_{NONCE.upper()}",
                     "confirmar_encomenda_7", "confirmar_encomenda_abc"):
            self.assertEqual(confirmacao.ler_callback(data), ("invalido", None), data)

    async def test_pre_token_and_malformed_buttons_get_the_expired_screen_without_services(self):
        for data in ("confirmar_encomenda_7", f"confirmar_encomenda_{NONCE}_0towlsj_7_8"):
            await self._run(data)
            texto, botoes, parse_mode = self._ecra()
            self.assertEqual((texto, parse_mode), (MENSAGEM_ECRA_EXPIRADO, None))
            self.assertEqual(botoes, [("🛍️ Ver Catálogo", "menu_catalogo"), ("🔙 Menu Principal", "menu_inicio")])
            self.criar.assert_not_awaited()
            self.lookup.assert_not_awaited()

    async def test_old_token_only_reopens_its_existing_order(self):
        antigo = f"confirmar_encomenda_{NONCE}_0towlsj_7"
        await self._run(antigo)
        self.assertEqual(self._ecra()[0], MENSAGEM_ECRA_EXPIRADO)
        self.lookup.assert_awaited_once_with(NONCE)
        self.criar.assert_not_awaited()

        self.query.answer.reset_mock()
        await self._run(antigo, existente=self.encomenda)
        self.query.answer.assert_awaited_once_with(MENSAGEM_JA_REGISTADA, show_alert=True)
        self.assertEqual(self._ecra()[1][0], ("💳 Pagar Agora", URL))

        self.query.answer.reset_mock()
        self.responder.reset_mock()
        alheia = SimpleNamespace(id=6, total=Decimal("1"), utilizador=SimpleNamespace(telegram_id="123"))
        await self._run(antigo, existente=alheia)
        self.query.answer.assert_awaited_once_with(MENSAGEM_PEDIDO_INVALIDO, show_alert=True)
        self.responder.assert_not_awaited()
        self.garantir.assert_not_awaited()

    async def test_new_order_answers_then_shows_every_line_and_the_link(self):
        self.linhas = [SimpleNamespace(nome_produto="SSD *1TB*", quantidade=2, subtotal=Decimal("100.00"), produto=None),
                       SimpleNamespace(nome_produto=None, quantidade=1, subtotal=Decimal("9.99"),
                                       produto=SimpleNamespace(nome="RAM"))]
        self.encomenda.total = Decimal("109.99")
        await self._run(self.data)
        self.assertEqual(self.calls, ["criar", "answer", "garantir", "responder"])
        self.query.answer.assert_awaited_once_with()
        texto, botoes, _ = self._ecra()
        self.assertIn("• 2× SSD \\*1TB\\* — 100.00€\n• 1× RAM — 9.99€\n\nTotal: *109.99€*", texto)
        self.assertEqual(botoes[0], ("💳 Pagar Agora", URL))

    async def test_existing_paid_order_gets_one_alert_and_no_edit(self):
        await self._run(self.data, criar=("existente", self.encomenda), garantir=("paga", None))
        self.query.answer.assert_awaited_once_with(MENSAGEM_JA_REGISTADA, show_alert=True)
        self.responder.assert_not_awaited()

    async def test_existing_unpaid_order_redraws_payment_screen(self):
        await self._run(self.data, criar=("existente", self.encomenda))
        self.query.answer.assert_awaited_once_with(MENSAGEM_JA_REGISTADA, show_alert=True)
        texto, botoes, _ = self._ecra()
        self.assertIn("Encomenda #5", texto)
        self.assertEqual(botoes[0], ("💳 Pagar Agora", URL))

    async def test_changed_screen_lists_the_changes_and_orders_nothing(self):
        alteracoes = [customer.services.Alteracao(7, "SSD", "preco", Decimal("49.99"), Decimal("54.99")),
                      customer.services.Alteracao(8, "RAM", "stock", 2, 0)]
        await self._run(self.data, criar=("alterado", alteracoes))
        texto, botoes, parse_mode = self._ecra()
        self.assertIsNone(parse_mode)
        self.assertIn("• SSD: ⚠️ preço alterado: 49.99€ → 54.99€\n• RAM: ⚠️ sem stock suficiente", texto)
        self.assertEqual(botoes, [("🔙 Voltar", "produto_7"), ("🔙 Menu Principal", "menu_inicio")])
        self.garantir.assert_not_awaited()
        self.query.answer.assert_awaited_once_with()

    async def test_pending_order_screen_points_to_that_order(self):
        await self._run(self.data, criar=("pendente", self.encomenda))
        texto, botoes, _ = self._ecra()
        self.assertEqual(texto, MENSAGEM_ENCOMENDA_PENDENTE.format(id=5))
        self.assertEqual(botoes, [("📋 Ver encomenda", "ver_encomenda_5"), ("🔙 Menu Principal", "menu_inicio")])
        self.garantir.assert_not_awaited()

    async def test_expired_screen_and_invalid_results(self):
        await self._run(self.data, criar=("expirada", None))
        self.assertEqual(self._ecra()[0], MENSAGEM_ECRA_EXPIRADO)
        for resultado in ("de_outro_utilizador", "invalida"):
            await self._run(self.data, criar=(resultado, None))
            texto, botoes, _ = self._ecra()
            self.assertEqual((texto, botoes), (MENSAGEM_PEDIDO_INVALIDO, [("🔙 Menu Principal", "menu_inicio")]))
        self.garantir.assert_not_awaited()

    async def test_order_creation_failure_shows_retry_screen(self):
        with self.assertLogs("telegram_bot.handlers.customer", level="ERROR") as logs:
            await self._run(self.data, criar=RuntimeError("segredo interno"))
        texto, botoes, parse_mode = self._ecra()
        self.assertEqual((texto, parse_mode), (MENSAGEM_FALHA_REGISTO, None))
        self.assertEqual(botoes, [("🔄 Tentar novamente", self.data), ("🔙 Menu Principal", "menu_inicio")])
        self.assertNotIn("segredo", texto)
        self.assertIn("segredo interno", "\n".join(logs.output))
        self.garantir.assert_not_awaited()
        self.query.answer.assert_awaited_once_with()

    async def test_payment_link_failure_offers_retry(self):
        with self.assertLogs("telegram_bot.handlers.customer", level="ERROR"):
            await self._run(self.data, garantir=RuntimeError("stripe down"))
        texto, botoes, _ = self._ecra()
        self.assertIn("Não foi possível gerar o link de pagamento", texto)
        self.assertEqual(botoes, [("🔄 Tentar novamente", self.data), ("🛒 Ver Encomendas", "menu_encomendas")])
        self.query.answer.assert_awaited_once_with()

    async def test_payment_deadline_passed_screen(self):
        await self._run(self.data, criar=("existente", self.encomenda), garantir=("expirada", None))
        texto, botoes, parse_mode = self._ecra()
        self.assertEqual((texto, parse_mode), (MENSAGEM_PRAZO_TERMINADO, None))
        self.assertEqual(botoes, [("📋 Ver encomenda", "ver_encomenda_5"), ("🔙 Menu Principal", "menu_inicio")])

    async def test_completed_session_shows_dont_pay_again_screen(self):
        await self._run(self.data, criar=("existente", self.encomenda), garantir=("a_confirmar", None))
        texto, botoes, parse_mode = self._ecra()
        self.assertEqual(texto, MENSAGEM_PAGAMENTO_A_CONFIRMAR.format(id=5))
        self.assertEqual(botoes, [("🛒 Ver Encomendas", "menu_encomendas")])
        self.assertIsNone(parse_mode)

    async def test_message_not_modified_counts_as_success(self):
        self.responder.side_effect = BadRequest("Message is not modified: specified new message content is the same")
        await self._run(self.data, criar=("existente", self.encomenda), garantir=("a_confirmar", None))
        self.query.answer.assert_awaited_once()

    async def test_payment_screen_fits_telegram_at_20_long_lines(self):
        self.linhas = [SimpleNamespace(nome_produto="_*[" * 70, quantidade=99, subtotal=Decimal("99999.99"), produto=None)
                       for _ in range(20)]
        self.encomenda.total = Decimal("1999999.80")
        await self._run(self.data)
        self.assertLess(len(self._ecra()[0]), 4096)

    async def test_each_product_confirm_screen_saves_a_new_snapshot(self):
        produto = SimpleNamespace(id=7, nome="SSD", preco=Decimal("50.00"), stock_atual=3)
        snaps = [SimpleNamespace(nonce=n, linhas=[{"nome": "SSD", "preco_unitario_centimos": 5000}])
                 for n in (NONCE, "0123456789abcdef")]
        criar = AsyncMock(side_effect=[("ok", snaps[0]), ("ok", snaps[1])])
        responder = AsyncMock()
        with patch.object(customer.services, "get_produto", AsyncMock(return_value=produto)), \
             patch.object(customer.services, "criar_confirmacao_async", criar), \
             patch.object(customer, "responder", responder):
            for _ in range(2):
                await customer.encomendar(callbacks._RespostaUnica(self.query), MagicMock(), "encomendar_7", 99)
        self.assertEqual([c.args[1] for c in criar.await_args_list], [[(7, 1)], [(7, 1)]])
        vistos = [_botoes(c.args[3]) for c in responder.await_args_list]
        self.assertEqual([v[0][1] for v in vistos], [f"confirmar_encomenda_{n.nonce}" for n in snaps])
        self.assertEqual(vistos[0][1], ("❌ Cancelar", "produto_7"))

    async def test_product_sold_out_before_its_screen_shows_the_change(self):
        produto = SimpleNamespace(id=7, nome="SSD", preco=Decimal("50.00"), stock_atual=3)
        bloqueado = ("bloqueado", [customer.services.Alteracao(7, "SSD", "stock", 1, 0)])
        responder = AsyncMock()
        with patch.object(customer.services, "get_produto", AsyncMock(return_value=produto)), \
             patch.object(customer.services, "criar_confirmacao_async", AsyncMock(return_value=bloqueado)), \
             patch.object(customer, "responder", responder):
            await customer.encomendar(callbacks._RespostaUnica(self.query), MagicMock(), "encomendar_7", 99)
        self.assertIn("• SSD: ⚠️ sem stock suficiente", responder.await_args.args[2])


class PhotoFallbackTests(SimpleTestCase):
    """Change 1: a missing or failed photo still leaves a product screen with buttons."""

    def setUp(self):
        self.calls = []
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".jpg")
        tmp.write(b"not really a jpeg")
        tmp.close()
        self.addCleanup(os.remove, tmp.name)
        self.existing_path = tmp.name

    async def _show(self, image_path, send_photo_error=None, delete_error=None):
        produto = SimpleNamespace(
            id=7, nome="SSD", preco=Decimal("50.00"), stock_atual=3, descricao=None,
            categoria_id=1, imagem=SimpleNamespace(open=lambda mode: open(image_path, mode)),
        )
        query = MagicMock()
        query.answer = AsyncMock()
        query.message.photo = None
        query.message.document = None
        query.message.delete = AsyncMock(
            side_effect=lambda: self.calls.append("delete") or (_raise(delete_error) if delete_error else None))
        context = MagicMock()
        context.bot.send_photo = AsyncMock(
            side_effect=lambda **k: self.calls.append("send_photo") or (_raise(send_photo_error) if send_photo_error else None))
        responder = AsyncMock(side_effect=lambda *a, **k: self.calls.append("responder"))
        with patch.object(customer.services, "get_produto", AsyncMock(return_value=produto)), \
             patch.object(customer, "responder", responder):
            await customer.produto_detalhe(callbacks._RespostaUnica(query), context, "produto_7", 99)
        return query, context, responder

    def _assert_text_fallback(self, responder):
        texto, keyboard = responder.await_args.args[2], responder.await_args.args[3]
        self.assertIn("(Imagem indisponível)", texto)
        callback_data = [b.callback_data for row in keyboard.inline_keyboard for b in row]
        self.assertEqual(callback_data, ["encomendar_7", "cat_1"])

    async def test_missing_file_edits_old_message_to_text(self):
        query, context, responder = await self._show(os.path.join(self.existing_path + "_missing"))
        self.assertEqual(self.calls, ["responder"])
        self._assert_text_fallback(responder)
        query.answer.assert_awaited_once_with()

    async def test_upload_error_keeps_old_message_and_shows_text(self):
        query, _, responder = await self._show(self.existing_path, send_photo_error=BadRequest("bad photo"))
        self.assertEqual(self.calls, ["send_photo", "responder"])
        self._assert_text_fallback(responder)

    async def test_upload_timeout_also_falls_back_to_text(self):
        _, _, responder = await self._show(self.existing_path, send_photo_error=TimedOut())
        self.assertEqual(self.calls, ["send_photo", "responder"])
        self._assert_text_fallback(responder)

    async def test_success_sends_photo_before_deleting_old_message(self):
        query, _, responder = await self._show(self.existing_path)
        self.assertEqual(self.calls, ["send_photo", "delete"])
        responder.assert_not_awaited()
        query.answer.assert_awaited_once_with()

    async def test_failed_delete_after_photo_is_ignored(self):
        _, _, responder = await self._show(self.existing_path, delete_error=BadRequest("too old to delete"))
        self.assertEqual(self.calls, ["send_photo", "delete"])
        responder.assert_not_awaited()


def _raise(exc):
    raise exc


class AdminCancelHandlerTests(SimpleTestCase):
    """SEC-06: the Telegram admin cancel goes through the shared operation and reports Stripe outcomes."""

    async def _cancelar(self, **servico):
        query = MagicMock()
        query.answer = AsyncMock()
        responder, notificar = AsyncMock(), AsyncMock(return_value=True)
        with patch.object(admin.services, "cancelar_encomenda_db", AsyncMock(**servico)), \
             patch.object(admin.services, "registar_auditoria", AsyncMock()), \
             patch.object(admin, "responder", responder), patch.object(admin, "notificar_cliente", notificar):
            await admin._mudar_estado_e_notificar(query, MagicMock(), 1, 7, "cancelada", "CANCELAR")
        return responder.await_args.args[2], notificar

    async def test_refunded_cancel_sends_the_refund_message(self):
        encomenda = SimpleNamespace(id=7, total=Decimal("9.99"), utilizador=SimpleNamespace(nome="Ana"))
        texto, notificar = await self._cancelar(return_value=("reembolsada", encomenda))
        self.assertEqual(notificar.await_args.args[2], "reembolsada")
        self.assertIn("marcada como *cancelada*", texto)

    async def test_payment_just_received_is_not_cancelled(self):
        from core.services import PagamentoRecebido
        texto, notificar = await self._cancelar(side_effect=PagamentoRecebido(7))
        self.assertIn("acabou de pagar", texto)
        notificar.assert_not_awaited()

    async def test_stripe_error_changes_nothing(self):
        import stripe
        with self.assertLogs("telegram_bot.handlers.admin", level="ERROR"):
            texto, notificar = await self._cancelar(side_effect=stripe.error.APIConnectionError("sem rede"))
        self.assertIn("Não foi possível contactar a Stripe", texto)
        notificar.assert_not_awaited()

    async def test_order_that_cannot_be_cancelled(self):
        texto, notificar = await self._cancelar(return_value=("invalida", None))
        self.assertIn("não encontrada ou estado inválido", texto)
        notificar.assert_not_awaited()


class MarkdownEscapingTests(SimpleTestCase):
    """SEC-07: names and descriptions cannot break or inject legacy-Markdown formatting."""

    def test_inside_entity_matches_the_bot_api_examples(self):
        from telegram_bot.ui import md
        self.assertEqual("*" + md("2*2=4", "*") + "*", "*2*\\**2=4*")
        self.assertEqual("_" + md("snake_case", "_") + "_", "_snake_\\__case_")
        self.assertEqual(md("a_b[c`d", "*"), "a_b[c`d")  # entities do not nest: literal

    def test_outside_entity_escapes_every_markdown_character(self):
        from telegram_bot.ui import md
        self.assertEqual(md("x_[y]*`z"), "x\\_\\[y]\\*\\`z")
        self.assertEqual(md(42), "42")

    async def test_start_greets_a_name_with_markdown_characters(self):
        from telegram_bot.handlers import commands
        update = MagicMock()
        update.message.reply_text = AsyncMock()
        utilizador = SimpleNamespace(nome="Ana*_[x](y)")
        with patch.object(commands, "get_or_create_utilizador", AsyncMock(return_value=(utilizador, False))):
            await inspect.unwrap(commands.start)(update, MagicMock())
        texto = update.message.reply_text.await_args.args[0]
        self.assertTrue(texto.startswith("Olá, *Ana*\\**_[x](y)*! "))

    async def test_confirm_screen_escapes_the_product_name(self):
        query = MagicMock()
        responder = AsyncMock()
        produto = SimpleNamespace(id=3, nome="SSD *Pro*", preco=Decimal("9.99"), stock_atual=2)
        snap = SimpleNamespace(nonce=NONCE, linhas=[{"nome": "SSD *Pro*", "preco_unitario_centimos": 999}])
        with patch.object(customer.services, "get_produto", AsyncMock(return_value=produto)), \
             patch.object(customer.services, "criar_confirmacao_async", AsyncMock(return_value=("ok", snap))), \
             patch.object(customer, "responder", responder):
            await customer.encomendar(query, MagicMock(), "encomendar_3", 99)
        self.assertIn("Produto: *SSD *\\**Pro*\\***\n", responder.await_args.args[2])


class LogPrivacyTests(SimpleTestCase):
    """SEC-08: logs carry no raw Telegram ids, usernames or bot token."""

    def test_pseudonym_is_stable_keyed_and_hides_the_id(self):
        from django.test import override_settings
        from security.sanitization import pseudonimo
        rotulo = pseudonimo(123456789)
        self.assertRegex(rotulo, r"^[0-9a-f]{12}$")
        self.assertEqual(rotulo, pseudonimo("123456789"))
        self.assertNotEqual(rotulo, pseudonimo(123456780))
        with override_settings(SECRET_KEY="outra-chave"):
            self.assertNotEqual(rotulo, pseudonimo(123456789))

    async def test_action_log_has_pseudonym_not_id_or_username(self):
        from security.decorators import log_acao
        from security.sanitization import pseudonimo
        update = MagicMock(effective_user=SimpleNamespace(id=123456789, username="ana_secreta"))
        with self.assertLogs("security.decorators", level="INFO") as logs:
            await log_acao("START")(AsyncMock())(update, MagicMock())
        texto = "\n".join(logs.output)
        self.assertIn(pseudonimo(123456789), texto)
        self.assertNotIn("123456789", texto)
        self.assertNotIn("ana_secreta", texto)

    async def test_customer_notification_log_has_no_telegram_id(self):
        from telegram_bot.notifications import notificar_cliente
        context = MagicMock()
        context.bot.send_message = AsyncMock()
        encomenda = SimpleNamespace(id=7, total=Decimal("9.99"), utilizador=SimpleNamespace(telegram_id="5551234"))
        with self.assertLogs("telegram_bot.notifications", level="INFO") as logs:
            self.assertTrue(await notificar_cliente(context, encomenda, "enviada"))
        self.assertNotIn("5551234", "\n".join(logs.output))

    def test_webhook_notification_error_does_not_log_the_token_url(self):
        from django.test import override_settings
        from core import views
        encomenda = SimpleNamespace(id=7, utilizador=SimpleNamespace(telegram_id="5551234"))
        erro = RuntimeError("POST https://api.telegram.org/bot123:SEGREDO/sendMessage")
        with override_settings(TELEGRAM_TOKEN="123:SEGREDO"), \
             patch.object(views.httpx, "post", side_effect=erro), \
             self.assertLogs("core.views", level="INFO") as logs:
            views._enviar_telegram(encomenda, "olá")
        texto = "\n".join(logs.output)
        self.assertIn("RuntimeError", texto)
        self.assertNotIn("SEGREDO", texto)
        self.assertNotIn("5551234", texto)


class InProcessStateTests(SimpleTestCase):
    """SEC-09: rate-limit and session state stay bounded; limits and expiry behave as before."""

    async def test_rate_limiter_still_limits_and_forgets_idle_users(self):
        from security import decorators
        agora = [1000.0]
        alvo = AsyncMock()
        protegido = decorators.rate_limit(alvo)

        def pedido(uid):
            return MagicMock(effective_user=SimpleNamespace(id=uid), message=MagicMock(reply_text=AsyncMock()))

        with patch.dict(decorators._rate_data, clear=True), patch.object(decorators, "_ultima_limpeza", 0.0), \
             patch.object(decorators, "time", SimpleNamespace(monotonic=lambda: agora[0])):
            for _ in range(decorators.RATE_LIMIT + 1):
                bloqueado = pedido(1)
                await protegido(bloqueado, MagicMock())
            self.assertEqual(alvo.await_count, decorators.RATE_LIMIT)
            bloqueado.message.reply_text.assert_awaited_once()
            for uid in range(2, 52):
                await protegido(pedido(uid), MagicMock())
            self.assertEqual(len(decorators._rate_data), 51)
            agora[0] += decorators.LIMPEZA_INTERVALO
            await protegido(pedido(999), MagicMock())
            self.assertEqual(set(decorators._rate_data), {999})

    def test_session_store_drops_expired_users_without_changing_expiry(self):
        from telegram_bot import guards
        agora = [1000.0]
        with patch.dict(guards._ultima_atividade, clear=True), patch.object(guards, "_ultima_limpeza", 0.0), \
             patch.object(guards, "time", SimpleNamespace(monotonic=lambda: agora[0])):
            for uid in range(50):
                guards._registar_atividade(uid)
            agora[0] += guards.SESSION_TIMEOUT + 1
            self.assertTrue(guards._sessao_expirada(1))  # expired before any cleanup
            guards._registar_atividade(999)
            self.assertEqual(set(guards._ultima_atividade), {999})
            self.assertTrue(guards._sessao_expirada(1))
            self.assertFalse(guards._sessao_expirada(999))


class AdminStatusHandlerTests(SimpleTestCase):
    """SEC-10: no manual confirm button, and refused status steps are reported."""

    def test_pending_order_has_no_confirm_button_and_old_buttons_are_rejected(self):
        from security.sanitization import EntradaInvalida
        botoes = _botoes(keyboards.teclado_acoes_encomenda(SimpleNamespace(id=5, estado="pendente")))
        self.assertNotIn("admin_enc_confirmar_5", [dados for _, dados in botoes])
        self.assertIn("admin_enc_cancelar_5", [dados for _, dados in botoes])
        with self.assertRaises(EntradaInvalida):
            validar_callback("admin_enc_confirmar_5")

    async def test_refused_step_reports_invalid_state(self):
        query = MagicMock()
        query.answer = AsyncMock()
        responder, notificar = AsyncMock(), AsyncMock()
        with patch.object(admin.services, "atualizar_estado_encomenda", AsyncMock(return_value=None)), \
             patch.object(admin, "responder", responder), patch.object(admin, "notificar_cliente", notificar):
            await admin._mudar_estado_e_notificar(query, MagicMock(), 1, 5, "enviada", "ENVIAR")
        self.assertIn("não encontrada ou estado inválido", responder.await_args.args[2])
        notificar.assert_not_awaited()


class LogRetentionTests(SimpleTestCase):
    """SEC-08: bot logs rotate daily and keep 14 days; httpx stays quiet."""

    def test_bot_logs_rotate_daily_and_keep_14_days(self):
        import logging
        import shutil
        from logging.handlers import TimedRotatingFileHandler
        from pathlib import Path
        from telegram_bot.app import configurar_logs
        raiz, httpx_log = logging.getLogger(), logging.getLogger("httpx")
        antes = (raiz.handlers[:], raiz.level, httpx_log.level)
        pasta = Path(tempfile.mkdtemp()) / "logs"
        self.addCleanup(shutil.rmtree, pasta.parent, ignore_errors=True)
        try:
            configurar_logs(pasta)
            ficheiros = [h for h in raiz.handlers if isinstance(h, TimedRotatingFileHandler)]
            self.assertEqual(len(ficheiros), 1)
            self.assertEqual((ficheiros[0].when, ficheiros[0].backupCount), ("MIDNIGHT", 14))
            self.assertEqual(Path(ficheiros[0].baseFilename), pasta / "bot.log")
            self.assertEqual(httpx_log.level, logging.WARNING)
        finally:
            for handler in raiz.handlers:
                if handler not in antes[0]:
                    handler.close()
            raiz.handlers[:] = antes[0]
            raiz.setLevel(antes[1])
            httpx_log.setLevel(antes[2])


class AdminUserToggleTests(SimpleTestCase):
    """The block/unblock reply is plain text, so the name is shown exactly as stored."""

    async def test_name_with_markdown_characters_is_shown_as_is(self):
        responder = AsyncMock()
        utilizador = SimpleNamespace(nome="Ana *Silva*_x")
        with patch.object(admin.services, "toggle_utilizador_ativo", AsyncMock(return_value=(utilizador, False))), \
             patch.object(admin.services, "registar_auditoria", AsyncMock()), \
             patch.object(admin, "responder", responder):
            await admin.utilizador_toggle(MagicMock(), MagicMock(), "admin_utilizador_123", 1)
        texto = responder.await_args.args[2]
        self.assertTrue(texto.startswith("👤 Ana *Silva*_x\n"))
        self.assertIsNone(responder.await_args.kwargs["parse_mode"])


class CategoriaHandlerTests(SimpleTestCase):
    """Stage 1: category buttons carry database ids; old or inactive ones expire gracefully."""

    async def _categoria(self, data, categoria=None, produtos=()):
        responder = AsyncMock()
        with patch.object(customer.services, "get_categoria_ativa", AsyncMock(return_value=categoria)) as ativa, \
             patch.object(customer.services, "get_produtos_por_categoria", AsyncMock(return_value=list(produtos))), \
             patch.object(customer, "responder", responder):
            await customer.categoria(MagicMock(), MagicMock(), data, 1)
        return responder.await_args, ativa

    async def test_old_code_button_shows_expired_menu(self):
        for data in ("cat_ssd", "cat_", "cat_0", "cat_-1", "cat_01"):
            chamada, ativa = await self._categoria(data)
            self.assertEqual(chamada.args[2], MENSAGEM_MENU_EXPIRADO, data)
            self.assertEqual(self._dados(chamada.args[3]), ["menu_catalogo"])
            ativa.assert_not_awaited()

    async def test_unknown_or_inactive_id_shows_expired_menu(self):
        chamada, ativa = await self._categoria("cat_999", categoria=None)
        self.assertEqual(chamada.args[2], MENSAGEM_MENU_EXPIRADO)
        ativa.assert_awaited_once_with(999)

    async def test_active_category_lists_its_products(self):
        cat = SimpleNamespace(id=3, nome="Discos")
        produto = SimpleNamespace(id=7, nome="SSD", preco=Decimal("50.00"))
        chamada, _ = await self._categoria("cat_3", categoria=cat, produtos=[produto])
        self.assertEqual(chamada.args[2], "Discos\nEscolhe um produto:")
        self.assertEqual(self._dados(chamada.args[3]), ["produto_7", "menu_catalogo"])

    async def test_category_name_is_escaped_in_markdown(self):
        cat = SimpleNamespace(id=3, nome="Promo *_[")
        chamada, _ = await self._categoria("cat_3", categoria=cat)
        self.assertEqual(chamada.args[2], "😔 Sem produtos disponíveis em *Promo *\\**_[*.")

    def test_admin_product_text_escapes_category_name(self):
        produto = SimpleNamespace(nome="SSD", preco=Decimal("50.00"), stock_atual=3, ativo=True,
                                  categoria=SimpleNamespace(nome="a_b*c[d"))
        self.assertIn("📁 Categoria: a\\_b\\*c\\[d\n", admin._texto_produto(produto))

    def test_catalog_has_one_button_per_category_in_order(self):
        categorias = [SimpleNamespace(id=4, nome="Discos"), SimpleNamespace(id=2, nome="Memória")]
        teclado = keyboards.teclado_catalogo(categorias)
        botoes = [(b.text, b.callback_data) for row in teclado.inline_keyboard for b in row]
        self.assertEqual(botoes, [("Discos", "cat_4"), ("Memória", "cat_2"), ("🔙 Menu Principal", "menu_inicio")])

    @staticmethod
    def _dados(teclado):
        return [b.callback_data for row in teclado.inline_keyboard for b in row]
