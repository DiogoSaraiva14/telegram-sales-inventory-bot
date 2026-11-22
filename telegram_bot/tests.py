"""Callback routing checks. Run with: python manage.py test telegram_bot"""
import inspect
import os
import tempfile
from decimal import Decimal
from types import SimpleNamespace
from unittest import expectedFailure
from unittest.mock import AsyncMock, MagicMock, patch

from django.test import SimpleTestCase
from telegram.error import BadRequest, TimedOut

from security.sanitization import ALLOWED_EXACT, ALLOWED_PREFIXES, validar_callback
from telegram_bot import keyboards
from telegram_bot.handlers import admin, callbacks, customer
from telegram_bot.handlers.callbacks import encontrar_rota

# Allowed by the validator but never handled, same as the original bot.py.
# ver_encomenda_ is where the order-detail button should land; see KnownBugTests.
UNROUTED_PREFIXES = {"cancelar_encomenda_", "ver_encomenda_"}


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


class KnownBugTests(SimpleTestCase):
    @expectedFailure  # SEC-12: list emits user_encomenda_<id>, allowlist only has ver_encomenda_. Remove this decorator when fixed.
    def test_order_list_buttons_pass_validation(self):
        encomenda = SimpleNamespace(id=1, total=Decimal("9.99"), estado="pendente")
        for row in keyboards.teclado_minhas_encomendas([encomenda]).inline_keyboard:
            for button in row:
                validar_callback(button.callback_data)


class CheckoutAcknowledgementTests(SimpleTestCase):
    """The button spinner must stop before the slow Stripe request."""

    async def _checkout(self, stripe_side_effect):
        calls = []
        query = MagicMock()
        query.answer = AsyncMock(side_effect=lambda *a, **k: calls.append("answer"))
        produto = SimpleNamespace(id=7, nome="SSD", preco=Decimal("50.00"), stock_atual=3)
        encomenda = SimpleNamespace(id=1, total=Decimal("50.00"))

        async def stripe(*args):
            calls.append("stripe")
            if stripe_side_effect:
                raise stripe_side_effect
            return "https://checkout.example/test"

        wrapped = callbacks._RespostaUnica(query)
        with patch.object(customer.services, "get_produto", AsyncMock(return_value=produto)), \
             patch.object(customer.services, "get_or_create_utilizador", AsyncMock(return_value=(object(), False))), \
             patch.object(customer.services, "criar_encomenda", AsyncMock(return_value=encomenda)), \
             patch.object(customer.services, "registar_auditoria", AsyncMock()), \
             patch.object(customer.services, "criar_sessao_stripe", stripe), \
             patch.object(customer, "responder", AsyncMock(side_effect=lambda *a, **k: calls.append("responder"))):
            await customer.confirmar_encomenda(wrapped, MagicMock(), "confirmar_encomenda_7", 99)
            await wrapped.answer()  # the router's fallback must not send a second answer
        return query, calls

    async def test_answers_before_stripe(self):
        query, calls = await self._checkout(None)
        self.assertEqual(calls, ["answer", "stripe", "responder"])
        query.answer.assert_awaited_once_with()

    async def test_stripe_error_reported_by_message_not_second_answer(self):
        query, calls = await self._checkout(RuntimeError("stripe down"))
        self.assertEqual(calls, ["answer", "stripe", "responder"])
        query.answer.assert_awaited_once_with()


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
            categoria="ssd", imagem=SimpleNamespace(path=image_path),
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
        self.assertEqual(callback_data, ["encomendar_7", "cat_ssd"])

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
