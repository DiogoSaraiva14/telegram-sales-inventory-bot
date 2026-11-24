# Telegram Sales and Inventory Bot

A Telegram interface for a computer-parts catalog, backed by Django and PostgreSQL, with Stripe Checkout for customer payments.

## Status

**Not yet validated for production or CV claims.** This standalone private repository contains the source snapshot imported from GitLab, now being overhauled. main holds the imported project and later overhaul work.

Code changes since import, in order:

1. **Refactor (behavior-preserving):** split the original 1,061-line bot.py into the telegram_bot/ package and core/services.py.
2. **Callback answers:** each callback query is now answered exactly once. Before, the bot acknowledged every button press and then tried to answer again with an alert (no stock, access denied, invalid request), which Telegram rejects. Handlers that do slow work (Stripe checkout, product photo upload, admin status change with customer notification) answer before that work, then report the result by editing or sending a message.
3. **Photo fallback:** the product screen now survives a missing or failed photo: it sends the photo before deleting the old screen and otherwise shows the text screen with "(Imagem indisponível)" and the same buttons.
4. **Duplicate-safe checkout (migration 0008):** one Confirm screen creates at most one order, and order creation is a single transaction that also refuses a sale on insufficient stock. Retries recover or replace the order's Stripe Checkout Session safely (SECURITY.md SEC-02, SEC-03, SEC-13). If an order is cancelled while its payment session is being prepared, and the cancellation commits before the session is saved, no link is shown. There is still a short gap: a cancellation that commits after the session is saved but before the payment screen is sent does not stop the link (SECURITY.md SEC-03). See SECURITY.md, SEC-03 and SEC-13.

**Tests:** telegram_bot/tests.py (mock-only) covers callback routing, the admin gate, single answers, the checkout handler branches and the customer order-detail screen (SEC-12). core/tests.py runs on PostgreSQL. It covers order reservation, payment-session recovery and replacement, the unchanged webhook, two end-to-end handler tests, and three threaded concurrency tests. Those prove the product row lock and the order-row lock that a concurrent cancellation takes. Stripe and Telegram are mocked; nothing has been run against a live bot or Stripe.

## Running and project layout

Start the bot with `python bot.py`. It loads .env, sets up Django, then starts polling. Run the Django admin and Stripe webhook separately with `python manage.py runserver`.

```
bot.py                     entry point (env, django.setup(), logging, start polling)
telegram_bot/
  app.py                   Application builder, handler registration, polling
  guards.py                admin IDs, session timeout, blocked-account checks
  constants.py             categories, state emoji, notification templates
  keyboards.py             inline keyboard builders
  ui.py                    responder() reply helper
  notifications.py         order-status messages to customers
  handlers/commands.py     /start, /admin
  handlers/callbacks.py    callback validation, routing, admin gate
  handlers/customer.py     catalog, product, orders, checkout, help
  handlers/admin.py        products, stock, orders, users, audit
  confirmacao.py           Confirm-button token: format, parser, 30-minute expiry
  tests.py                 mock-only tests: python manage.py test telegram_bot
core/                      Django app: models, admin, Stripe webhook, services.py (bot ORM operations)
security/                  callback allowlist, input parsers, rate limit, action logging
config/                    Django settings and URLs
```

**Running the tests:**
- `python manage.py test telegram_bot` needs no database; `SECRET_KEY` and `DB_NAME` must still be set, and any placeholder works.
- `python manage.py test core telegram_bot` needs PostgreSQL. Use a local role with only `LOGIN` and `CREATEDB` (for example `telegram_bot_test`), and put `SECRET_KEY` and the `DB_*` variables in a git-ignored `.env`. Django creates and drops `test_<DB_NAME>` itself.

**Deploying this change:**
- Run `python manage.py migrate` (0008 adds two nullable columns) before starting the new bot code.
- Changing `STRIPE_SUCCESS_URL` or `STRIPE_CANCEL_URL` is safe at any time: they are part of each Stripe idempotency key, and sessions already recorded keep their URLs.

## Features observed in source

- Telegram product catalog grouped by hardware category
- One-product order creation and stock movement records
- Stripe Checkout session creation and webhook handling
- Customer order listing and status notifications
- Telegram and Django admin workflows
- Product, user, order, payment, stock-movement, and audit models

These features are described from source inspection and have not been verified end to end.

## Stack

Python, Django, PostgreSQL, python-telegram-bot, Stripe, Pillow, and python-dotenv. Exact pinned package versions are in requirements.txt. The supported Python version and deployment topology are not documented.

## Source and migration notes

- GitLab source project: project-2025-2026-p088
- Source head observed: 0039014ba96cc7d3a4946babad4e6716e47d3954
- GitLab lists 27 commits; this import is an archive snapshot and does not preserve the GitLab commit history.
- Python bytecode and media/produtos image files were excluded. The original source README was empty.
- Confirm course/team sharing permissions and replace or license demo assets before making the repository public.

## Configuration

The source reads Django, PostgreSQL, Telegram, and Stripe settings from environment variables. The names currently referenced include SECRET_KEY, DEBUG, DB_NAME, DB_USER, DB_PASSWORD, DB_HOST, DB_PORT, TELEGRAM_TOKEN, ADMIN_TELEGRAM_IDS, STRIPE_SECRET_KEY, STRIPE_WEBHOOK_SECRET, STRIPE_SUCCESS_URL, and STRIPE_CANCEL_URL.

There is no committed environment example or validated startup guide yet. Do not put real values in this repository. Configuration defaults need hardening before a public or production deployment.

## Review priorities

See ARCHITECTURE.md, SECURITY.md, and ROADMAP.md. Key areas include callback routing, order/stock transaction boundaries, checkout expiry and cancellation, webhook idempotency, admin consistency, and safe deployment configuration.
