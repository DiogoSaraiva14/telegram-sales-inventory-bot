# Architecture

Status: draft map based on the imported source snapshot. Runtime topology and production behavior have not been verified.

## System shape

Telegram users interact with a polling bot process. Django provides the admin site and Stripe webhook endpoint. Both processes use Django models and PostgreSQL.

Telegram user
→ Telegram Bot API polling
→ telegram_bot/ command handlers and callback router (started by bot.py)
→ Django ORM
→ PostgreSQL

Customer payment
→ the bot creates a pending order and a Stripe Checkout session (core/services.py)
→ customer pays on Stripe
→ Stripe POSTs a signed event to Django at /webhook/stripe/
→ core.views updates payment/order/audit rows
→ Django sends a Telegram notification through the Bot API

Staff can also mutate products, stock, orders, payments, and audit data through Django admin. Telegram admin callbacks provide a second management surface.

## Components and responsibilities observed

- bot.py: entry point. Loads .env, runs django.setup(), configures logging, then starts telegram_bot.app.main(). Django must be set up before any telegram_bot module is imported.
- telegram_bot/app.py: builds the python-telegram-bot Application, registers /start, /admin and the callback handler, and runs polling.
- telegram_bot/handlers/commands.py: /start and /admin.
- telegram_bot/handlers/callbacks.py: validates callback_data against the allowlist, looks up the route, and blocks admin routes for non-admin users before dispatch.
- telegram_bot/handlers/customer.py and admin.py: one function per screen, plus EXACT_ROUTES/PREFIX_ROUTES tables that map callback values to those functions.
- telegram_bot/keyboards.py, ui.py, constants.py: inline keyboards, the shared `responder` reply helper, and labels/message templates.
- telegram_bot/guards.py: ADMIN_TELEGRAM_IDS, session timeout, and blocked-account decorators.
- telegram_bot/notifications.py: order-status notifications sent through the running bot.
- telegram_bot/tests.py: callback routing and admin-gate tests (no database needed).
- core/services.py: ORM operations used by the bot.
  - Checkout is transactional: `reservar_encomenda` locks the product row and writes the order, line, stock, movement and audit together. `garantir_sessao_pagamento` recovers or creates the order's Stripe Checkout Session with idempotency keys and saves it before the link is shown.
  - The other helpers (admin status changes, cancellation, users, audit) were moved unchanged and are not yet transactional.
- telegram_bot/confirmacao.py: the Confirm-button token (nonce, screen time and product id) and its 30-minute expiry.
- config/: Django settings, URL configuration, WSGI, and ASGI entrypoints.
- core/models.py: Utilizador, Produto, Encomenda, LinhaEncomenda, Pagamento, MovimentoStock, and RegistoAuditoria.
- core/views.py: signed Stripe webhook plus payment success/expiry handling and Telegram payment confirmation.
- core/admin.py: Django admin listings, edit forms, and bulk actions.
- security/sanitization.py: Telegram callback prefix checks and value parsers.
- security/decorators.py: process-local rate limiter, admin decorator, and action logging.
- core/migrations/: eight migrations are present. 0008 adds `Encomenda.chave_confirmacao` (unique, nullable) and `LinhaEncomenda.nome_produto`.
- requirements.txt: pinned runtime packages; no separate development dependency manifest was present.

## Current domain flows

### Customer order

1. /start creates a Utilizador row if one does not exist.
2. The user selects a product and confirms a one-item order.
3. The Confirm button carries a one-time token.
   - `reservar_encomenda` creates the order, its line, the stock decrement, the stock movement and the audit row in one transaction, at most once per token.
   - Then `garantir_sessao_pagamento` creates the Stripe Checkout Session and saves the Pagamento row before the payment link is shown.
   - A repeat tap or retry finds the same order and recovers or replaces its session.
4. Stripe completion marks payment paid and order confirmed. Expiration marks payment failed, cancels the unpaid order and returns its stock in one transaction, then notifies the customer.

### Admin

Telegram admin callbacks check the configured ADMIN_TELEGRAM_IDS set in each route branch. Django admin uses the standard Django staff authentication system. The two surfaces currently perform some order/stock changes differently.

## State and persistence

PostgreSQL stores users, product data, orders and lines, payments, stock movements, and audit records. Session activity and rate-limit buckets are kept in process memory. Product images use Django ImageField and local MEDIA_ROOT by default.

The current order flow creates quantity one and a one-line order. This is observed behavior, not a confirmed long-term product requirement.

## Architecture review notes

- Bot checkout is now transactional in core/services.py. Admin order and stock changes are not yet: that layer should own them too (SEC-06, SEC-10).
- Make order creation, stock reservation, payment-session persistence, and cancellation atomic or explicitly compensating.
- Represent payment and order state transitions in one place; both admin interfaces should call the same operations.
- Webhook state changes are idempotent under row locks (SEC-05); notification delivery still has no retry.
- Split Telegram presentation from domain services so callbacks can be tested without Telegram or PostgreSQL.
- Select one deployment model and document how the polling bot, Django server, Stripe webhook, PostgreSQL, media storage, and worker/notification retries run together.

## Open questions

- Are the bot and Django server intended to run as separate processes in production?
- Is stock reserved at order creation, payment-session creation, or successful payment?
- What should happen to unpaid orders that never got a Stripe session? (Expired sessions cancel the order and return stock.)
- Are product images expected to be uploaded through Django admin, or committed as sample fixtures?
- Which parts of the GitLab project and history may be included in a public portfolio?
