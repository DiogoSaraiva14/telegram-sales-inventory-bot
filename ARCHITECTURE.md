# Architecture

Status: draft map based on the imported source snapshot. Runtime topology and production behavior have not been verified.

## System shape

Telegram users interact with a polling bot process. Django provides the admin site and Stripe webhook endpoint. Both processes use Django models and PostgreSQL.

Telegram user
→ Telegram Bot API polling
→ bot.py command and callback router
→ Django ORM
→ PostgreSQL

Customer payment
→ bot.py creates Stripe Checkout session and a pending order
→ customer pays on Stripe
→ Stripe POSTs a signed event to Django at /webhook/stripe/
→ core.views updates payment/order/audit rows
→ Django sends a Telegram notification through the Bot API

Staff can also mutate products, stock, orders, payments, and audit data through Django admin. Telegram admin callbacks provide a second management surface.

## Components and responsibilities observed

- bot.py: Django setup, Telegram polling, keyboards, callback dispatch, ORM helpers, Stripe Checkout creation, notifications, and most admin/customer workflows. The file has about 1,061 lines and combines routing with business operations.
- config/: Django settings, URL configuration, WSGI, and ASGI entrypoints.
- core/models.py: Utilizador, Produto, Encomenda, LinhaEncomenda, Pagamento, MovimentoStock, and RegistoAuditoria.
- core/views.py: signed Stripe webhook plus payment success/expiry handling and Telegram payment confirmation.
- core/admin.py: Django admin listings, edit forms, and bulk actions.
- security/sanitization.py: Telegram callback prefix checks and value parsers.
- security/decorators.py: process-local rate limiter, admin decorator, and action logging.
- core/migrations/: seven migrations are present.
- requirements.txt: pinned runtime packages; no separate development dependency manifest was present.

## Current domain flows

### Customer order

1. /start creates a Utilizador row if one does not exist.
2. The user selects a product and confirms a one-item order.
3. bot.py creates the order and line, reduces product stock, writes a stock movement, then requests a Stripe Checkout session and creates a Pagamento row.
4. Stripe completion marks payment paid and order confirmed. Expiration marks payment failed.

### Admin

Telegram admin callbacks check the configured ADMIN_TELEGRAM_IDS set in each route branch. Django admin uses the standard Django staff authentication system. The two surfaces currently perform some order/stock changes differently.

## State and persistence

PostgreSQL stores users, product data, orders and lines, payments, stock movements, and audit records. Session activity and rate-limit buckets are kept in process memory. Product images use Django ImageField and local MEDIA_ROOT by default.

The current order flow creates quantity one and a one-line order. This is observed behavior, not a confirmed long-term product requirement.

## Architecture review notes

- Keep state-changing domain logic out of the large callback router. A service layer should own order, inventory, payment, and audit transactions.
- Make order creation, stock reservation, payment-session persistence, and cancellation atomic or explicitly compensating.
- Represent payment and order state transitions in one place; both admin interfaces should call the same operations.
- Make webhook processing idempotent across concurrent/retried events and separate payment state changes from notification delivery.
- Split Telegram presentation from domain services so callbacks can be tested without Telegram or PostgreSQL.
- Select one deployment model and document how the polling bot, Django server, Stripe webhook, PostgreSQL, media storage, and worker/notification retries run together.

## Open questions

- Are the bot and Django server intended to run as separate processes in production?
- Is stock reserved at order creation, payment-session creation, or successful payment?
- What should happen to expired or abandoned unpaid orders?
- Are product images expected to be uploaded through Django admin, or committed as sample fixtures?
- Which parts of the GitLab project and history may be included in a public portfolio?
