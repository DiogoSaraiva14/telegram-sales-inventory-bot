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
→ the customer fills a cart and confirms a saved confirmation screen
→ the bot creates a pending order and a Stripe Checkout session (core/services.py)
→ customer pays on Stripe
→ Stripe POSTs a signed event to Django at /webhook/stripe/
→ core.views updates payment/order/audit rows
→ Django sends a Telegram notification through the Bot API

Staff can also mutate products, stock, orders, payments, and audit data through Django admin. Telegram admin callbacks provide a second management surface.

## Components and responsibilities observed

- bot.py: entry point. Loads .env, runs django.setup(), configures logging, then starts telegram_bot.app.main(). Django must be set up before any telegram_bot module is imported.
- telegram_bot/app.py: builds the python-telegram-bot Application, registers /start, /admin and the callback handler, and runs polling.
- telegram_bot/handlers/commands.py: /start (shop name and welcome text from `Loja`) and /admin.
- telegram_bot/handlers/callbacks.py: validates callback_data against the allowlist, looks up the route, and blocks admin routes for non-admins (`services.e_admin`) before dispatch.
- telegram_bot/handlers/customer.py and admin.py: one function per screen, plus EXACT_ROUTES/PREFIX_ROUTES tables that map callback values to those functions.
- telegram_bot/keyboards.py, ui.py, constants.py: inline keyboards, the shared `responder` reply helper, and labels/message templates.
- telegram_bot/guards.py: session timeout and blocked-account decorators.
- telegram_bot/tests.py: mock-only tests for routing, the admin gate, and the checkout and cart screens.
- core/notificacoes.py: customer order messages. Each is saved in `Notificacao`, sent at once through the Bot API, and resent by the cleanup run for 24 hours. A message Telegram refuses (400, 403) or that is still undelivered after 24 hours is given up with a `NOTIFICACAO_FALHADA` audit row.
- core/services.py: ORM operations used by the bot, the webhook and both admin surfaces. The main ones:
  - Telegram admin check `e_admin`: the bootstrap ids from `ADMIN_TELEGRAM_IDS`, or `Utilizador.e_admin`, read on every call.
  - Cart services, each locking the cart row.
  - `criar_confirmacao` saves a confirmation screen.
  - `criar_encomenda_de_confirmacao` creates the order in one transaction.
  - `garantir_sessao_pagamento` handles the Stripe session and the 28-minute creation cutoff.
  - `cancelar_encomenda` cancels and refunds.
  - `reconciliar_encomenda` is the cleanup.
  - `avancar_estado_encomenda` and `ajustar_stock` for the admin steps and stock edits.
- telegram_bot/confirmacao.py: the Confirm-button token `confirmar_encomenda_<nonce>`; the screen's age is checked on the server.
- config/: Django settings (including the opt-in HTTPS settings), URL configuration, WSGI, and ASGI entrypoints.
- core/models.py:
  - Loja, Utilizador, Categoria and Produto;
  - Carrinho and LinhaCarrinho;
  - ConfirmacaoCarrinho;
  - Encomenda, LinhaEncomenda and Pagamento;
  - MovimentoStock and RegistoAuditoria.
- core/views.py: signed Stripe webhook plus payment success/expiry handling and Telegram payment confirmation.
- core/admin.py: Django admin listings, edit forms, and bulk actions. The order actions declare permissions that Django checks on the server.
- core/management/commands/cancelar_encomendas_abandonadas.py: the 5-minute cleanup (see below).
- security/sanitization.py: Telegram callback prefix checks and value parsers.
- security/decorators.py: process-local rate limiter and action logging.
- core/migrations/: 0001–0015.
  - 0009–0011: categories.
  - 0012: confirmation screens.
  - 0013: cart.
  - 0014: shop settings and the admin flag.
  - 0015: the "Dono da loja" group.
- DEPLOYMENT.md and deploy/crontab.example: installing one copy per shop.
- requirements.txt: pinned runtime packages; no separate development dependency manifest was present.

## Current domain flows

### Customer order

Cart → snapshot → order:

1. /start creates a Utilizador row if one does not exist.
2. The customer adds products to the cart. The cart reserves nothing.
3. "Finalizar compra" saves a `ConfirmacaoCarrinho`, a snapshot that never changes. It holds the names, quantities and prices in cents that the screen shows, and the cart's revision.
4. Confirm runs `criar_encomenda_de_confirmacao` in one transaction:
   - it locks the snapshot, then the cart, then the products in id order;
   - it refuses if the screen is over 30 minutes old, if the customer has any pending order, or if the cart, a price, the stock or a product changed. Nothing is written in those cases;
   - otherwise it creates the order, its lines, the stock decrements, the movements and the audit row, and empties the cart.
5. `garantir_sessao_pagamento` creates the Stripe Checkout Session, with one line per order line and `expires_at` 60 minutes after the order. It saves the Pagamento row before the link is shown. Sessions are created only while the order is at most 28 minutes old. An expired session cancels the order instead of being replaced; the earlier replacement rule, D19, is reversed.
6. Stripe completion marks the payment paid and the order confirmed. Expiration cancels the unpaid order and returns its stock.
7. Every 5 minutes, `cancelar_encomendas_abandonadas --horas 1` reconciles each pending order older than 60 minutes, so a lost webhook doesn't hold stock or leave a paid order pending.

**Lock rules:**
- Checkout locks the snapshot, then the cart, then the products, and never an existing order or the customer row.
- Cancellation, expiry, the cleanup and the webhook lock the order, then its payment, then its products.
- Products are always locked in one query ordered by id, including the Django admin bulk activate and deactivate.

### Admin

Telegram admin rights come from `services.e_admin`: the bootstrap ids in `ADMIN_TELEGRAM_IDS`, or the `Utilizador.e_admin` flag, which only a superuser can change. Both admin surfaces use the same operations in core/services.py: `cancelar_encomenda`, `avancar_estado_encomenda` (forward only) and `ajustar_stock`. In Django admin, orders, lines and payments are read-only (SEC-10).

The shop owner's staff account is in the "Dono da loja" group:
- it can edit the catalogue and the shop settings;
- it can only read orders and payments;
- the ship, deliver and cancel actions declare permissions that the group lacks, so Django refuses them on the server.

## State and persistence

PostgreSQL stores the shop settings, users, categories and products, carts, confirmation snapshots, orders and lines, payments, stock movements, and audit records. Unused snapshots are deleted after 45 minutes and idle carts after 30 days; snapshots linked to an order are kept. Session activity and rate-limit buckets are kept in process memory, bounded by a periodic cleanup. This works because the polling bot always runs as a single process (SEC-09). A restart resets them. Product images use Django ImageField and local MEDIA_ROOT by default.

## Architecture review notes

- Bot checkout and admin order and stock changes are transactional in core/services.py (SEC-06, SEC-10).
- Make order creation, stock reservation, payment-session persistence, and cancellation atomic or explicitly compensating.
- Represent payment and order state transitions in one place; both admin interfaces should call the same operations.
- Webhook state changes are idempotent under row locks (SEC-05). Customer messages are queued just after the order change commits and retried for 24 hours; a crash between that commit and the queue write still loses the message.
- Split Telegram presentation from domain services so callbacks can be tested without Telegram or PostgreSQL.
- Select one deployment model and document how the polling bot, Django server, Stripe webhook, PostgreSQL, media storage, and worker/notification retries run together.

## Open questions

Settled by the cart design (see PROJECT_GUIDANCE.md):
- The bot and the Django server run as separate processes.
- Stock is taken at Confirm.
- Every old pending order is reconciled by the cleanup.

Still open:
- Are product images expected to be uploaded through Django admin, or committed as sample fixtures?
- Which parts of the GitLab project and history may be included in a public portfolio?
