# Telegram Sales and Inventory Bot

A Telegram interface for a computer-parts catalog, backed by Django and PostgreSQL, with Stripe Checkout for customer payments.

## Status

**Not yet validated for production or CV claims.** This standalone private repository contains the source snapshot imported from GitLab, now being overhauled. main holds the imported project and later overhaul work.

The only code change since import is a behavior-preserving split of the original 1,061-line bot.py into the telegram_bot/ package and core/services.py. One bug fix rides with it: each callback query is now answered exactly once. Before, the bot acknowledged every button press and then tried to answer again with an alert (no stock, access denied, invalid request), which Telegram rejects. Handlers that do slow work (Stripe checkout, product photo upload, admin status change with customer notification) answer before that work, then report the result by editing or sending a message. Known gaps carried over unchanged from the original bot: tapping Confirm again while checkout is running can create a second order, and failures outside the Stripe call (creating the user, order or audit row) leave the customer without a result message. Checkout also fails when a product's image file is missing (planned fix: the duplicate-checkout change for SEC-13). The product screen itself now survives a missing or failed photo: it sends the photo before deleting the old screen and otherwise shows the text screen with "(Imagem indisponível)" and the same buttons. See SECURITY.md, SEC-03 and SEC-13. telegram_bot/tests.py covers callback routing, the admin gate, single answers, and the known order-detail bug (SEC-12, an expected failure). No end-to-end runtime checks have been run against Telegram, Stripe, or PostgreSQL. core/tests.py is still an empty scaffold.

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
  tests.py                 routing tests: python manage.py test telegram_bot
core/                      Django app: models, admin, Stripe webhook, services.py (bot ORM operations)
security/                  callback allowlist, input parsers, rate limit, action logging
config/                    Django settings and URLs
```

The routing tests need no database, but Django settings still need `SECRET_KEY` and `DB_NAME` to be set (any placeholder works).

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
