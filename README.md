# Telegram Sales and Inventory Bot

A Telegram shop with a multi-product cart, backed by Django and PostgreSQL, with Stripe Checkout for customer payments. Each copy serves one shop, configured in Django admin without code changes; [DEPLOYMENT.md](DEPLOYMENT.md) is the guide for installing a copy.

## Status

**Not yet validated for production or CV claims.** This standalone private repository contains the source snapshot imported from GitLab, now being overhauled. main holds the imported project and later overhaul work.

Code changes since import, in order:

1. **Refactor (behavior-preserving):** split the original 1,061-line bot.py into the telegram_bot/ package and core/services.py.
2. **Callback answers:** each callback query is now answered exactly once. Before, the bot acknowledged every button press and then tried to answer again with an alert (no stock, access denied, invalid request), which Telegram rejects. Handlers that do slow work (Stripe checkout, product photo upload, admin status change with customer notification) answer before that work, then report the result by editing or sending a message.
3. **Photo fallback:** the product screen now survives a missing or failed photo: it sends the photo before deleting the old screen and otherwise shows the text screen with "(Imagem indisponível)" and the same buttons.
4. **Duplicate-safe checkout (migration 0008):** one Confirm screen creates at most one order, and order creation is a single transaction that also refuses a sale on insufficient stock. Retries recover or replace the order's Stripe Checkout Session safely (SECURITY.md SEC-02, SEC-03, SEC-13). If an order is cancelled while its payment session is being prepared, and the cancellation commits before the session is saved, no link is shown. A later admin cancellation expires the stored session, so a link that still reaches the customer cannot be paid (SEC-06). See SECURITY.md, SEC-03 and SEC-13.
5. **Security fixes:** SEC-01 to SEC-13 are fixed. SECURITY.md lists each fix and what it does not cover.
6. **Cart and resellable shop** ([design](docs/designs/cart_resellable_shop.md), migrations 0009–0015):
   - **Stage 1:** categories live in the database.
   - **Stage 2:** orders have several lines and are created from a saved confirmation screen. Each payment link expires 60 minutes after the order, and a cron job reconciles old pending orders.
   - **Stage 3:** a cart stored in the database, and "Repetir encomenda".
   - **Stage 4:** shop name and welcome text in the database, Telegram admin rights as a database flag, a limited "Dono da loja" group in Django admin, and the HTTPS settings.
   - **Stage 0 is still open:** code ownership, course rights, licence and demo-image rights.

**Tests:**
- `telegram_bot/tests.py` is mock-only. It covers callback routing, the admin gate, single answers, the checkout and cart screens, and the shop name and welcome text.
- `core/tests.py` runs on PostgreSQL. It covers:
  - order creation from saved screens;
  - the payment window and session recovery;
  - the webhook;
  - cancellation and refunds;
  - the reconciling cleanup;
  - cart services and "Repetir encomenda";
  - the owner's Django permissions;
  - the HTTPS settings;
  - threaded tests that prove the row locks and the absence of deadlocks.
- Stripe and Telegram are mocked; nothing has been run against a live bot or Stripe.

## Local setup

These steps match how the checks were run on Windows with Python 3.13 and PostgreSQL 17. Other versions are untested. Nothing has been run against a live Telegram bot or Stripe yet.

1. **Python environment:**
   ```powershell
   python -m venv .venv
   .venv\Scripts\Activate.ps1
   pip install -r requirements.txt
   ```
2. **PostgreSQL:** create a role with `LOGIN` and `CREATEDB` (not superuser), for example `telegram_bot_test`, either in pgAdmin or with `createuser --createdb --pwprompt`. To run the bot, also create a database it owns (`createdb -O telegram_bot_test telegram_bot`). The tests only need `CREATEDB`.
3. **Settings:** copy `.env.example` to `.env` and fill it in. `.env` is git-ignored.
   - Every command needs `SECRET_KEY` and `DB_NAME`.
   - The bot also needs `TELEGRAM_TOKEN`, `STRIPE_SECRET_KEY`, `STRIPE_SUCCESS_URL` and `STRIPE_CANCEL_URL`. It refuses to start and lists what is missing.
   - Use Stripe test-mode keys only.
4. **Database:**
   ```powershell
   python manage.py migrate
   python manage.py createsuperuser
   ```
5. **Django admin and Stripe webhook:** run `python manage.py runserver`. The admin is at `/admin/` and the webhook at `/webhook/stripe/`. For local Stripe tests, the Stripe CLI can forward events: run `stripe listen --forward-to localhost:8000/webhook/stripe/` and put the `whsec_…` secret it prints in `STRIPE_WEBHOOK_SECRET`.
6. **Bot:** run `python bot.py`.
7. **Tests:**
   - `python manage.py test core telegram_bot` needs the PostgreSQL role from step 2; Django creates and drops `test_<DB_NAME>` itself.
   - `python manage.py test telegram_bot` alone needs no database server.
8. **CI:** `.github/workflows/tests.yml` runs the same check, a migration drift check and the full suite on PostgreSQL 17 for pushes to `main` and for pull requests.

## Running and project layout

Start the bot with `python bot.py`. It loads .env, sets up Django, writes logs to the console and to `logs/bot.log` (rotated daily, 14 days kept), then starts polling. Run the Django admin and Stripe webhook separately with `python manage.py runserver`.

Schedule `python manage.py cancelar_encomendas_abandonadas --horas 1` every 5 minutes (cron or Task Scheduler; see [deploy/crontab.example](deploy/crontab.example)). It reconciles pending orders older than an hour: it cancels them and returns their stock, or confirms them if Stripe says they were paid. It also deletes unused confirmation screens after 45 minutes and idle carts after 30 days.

```
bot.py                     entry point (env, django.setup(), logging, start polling)
telegram_bot/
  app.py                   Application builder, handler registration, polling
  guards.py                session timeout, blocked-account checks
  constants.py             state emoji, message templates
  keyboards.py             inline keyboard builders
  ui.py                    responder() reply helper
  handlers/commands.py     /start, /admin
  handlers/callbacks.py    callback validation, routing, admin gate
  handlers/customer.py     catalog, product, orders, checkout, help
  handlers/admin.py        products, stock, orders, users, audit
  confirmacao.py           Confirm-button token: confirmar_encomenda_<nonce> of a saved screen
  tests.py                 mock-only tests: python manage.py test telegram_bot
core/                      Django app: models, admin, Stripe webhook, services.py (bot ORM operations)
security/                  callback allowlist, input parsers, rate limit, action logging
config/                    Django settings and URLs
```

**Running the tests:**
- `python manage.py test telegram_bot` needs no database; `SECRET_KEY` and `DB_NAME` must still be set, and any placeholder works.
- `python manage.py test core telegram_bot` needs PostgreSQL. Use a local role with only `LOGIN` and `CREATEDB` (for example `telegram_bot_test`), and put `SECRET_KEY` and the `DB_*` variables in a git-ignored `.env`. Django creates and drops `test_<DB_NAME>` itself.

**Deploying a change:**
- Take a database backup, stop the bot, run `python manage.py migrate`, then start the bot. Migrations only move forward; DEPLOYMENT.md covers backups and restores.
- Changing `STRIPE_SUCCESS_URL` or `STRIPE_CANCEL_URL` is safe at any time: they are part of each Stripe idempotency key, and sessions already recorded keep their URLs.

## Features

- Catalogue by category, with categories and products managed in Django admin
- A cart stored in the database: limits per line and per cart, and flags for price changes, low stock and unavailable products
- One order per confirmation screen, with the prices the customer saw. Stock is taken at Confirm and returned on cancellation or expiry
- Stripe Checkout with one line per product, a 60-minute payment window, webhook confirmation and full refunds
- "Repetir encomenda" for cancelled unpaid orders
- Telegram admin panel for orders, stock, users and the audit log, with admin rights stored in the database
- Django admin for the reseller, plus a limited "Dono da loja" group for the shop owner
- Stock movement and audit records

These features are covered by automated tests with Telegram and Stripe mocked. They have not been verified end to end against live services.

## Stack

Python 3.13, Django, PostgreSQL 17, python-telegram-bot, Stripe, httpx, Pillow, and python-dotenv. Deployment is described in [DEPLOYMENT.md](DEPLOYMENT.md).

## Updating dependencies

- `requirements.in` lists only the packages the code imports, each with a range that stays within one major version (one minor for Django and httpx).
- `requirements.txt` pins every installed package, including the ones those pull in (for example, `stripe` needs `requests`). Install from it; don't edit it by hand.

**When:** check monthly, and at once for a security advisory on any pinned package (`pip list --outdated` in the project's venv). Read the release notes of any direct package that changes.

**How:**
1. To move a package beyond its range, change the range in `requirements.in` first.
2. Regenerate the pins in a clean venv:
   ```powershell
   python -m venv $env:TEMP\deps
   & $env:TEMP\deps\Scripts\python -m pip install -r requirements.in
   & $env:TEMP\deps\Scripts\python -m pip freeze | Out-File -Encoding ascii requirements.txt
   ```
   (`Out-File -Encoding ascii`, because `>` in Windows PowerShell writes UTF-16, which pip can't read.)
   Then put back the two header lines at the top of `requirements.txt`.
3. Run the test suite and `manage.py makemigrations --check`, and open a PR. CI runs both.
4. Update the third-party licence table in [docs/stage0_checklist.md](docs/stage0_checklist.md) if a package was added or changed licence.

## Source and migration notes

- GitLab source project: project-2025-2026-p088
- Source head observed: 0039014ba96cc7d3a4946babad4e6716e47d3954
- GitLab lists 27 commits; this import is an archive snapshot and does not preserve the GitLab commit history.
- Python bytecode and media/produtos image files were excluded. The original source README was empty.
- Confirm course/team sharing permissions and replace or license demo assets before making the repository public.

## Configuration

All settings come from environment variables. `.env.example` lists every one with a placeholder and says which commands need it. `config/settings.py` refuses to start without `SECRET_KEY` and `DB_NAME`, and `python bot.py` refuses to start without its Telegram and Stripe settings.

Do not put real values in this repository. `DEBUG` defaults to False, and `ALLOWED_HOSTS` to an empty list. Set `DEBUG=True` only for local development, and list your host names in `ALLOWED_HOSTS` for any deployment. For a public deployment, set `DJANGO_HTTPS=True`, which turns on the HTTPS redirect, secure cookies and HSTS, and `CSRF_TRUSTED_ORIGINS` (SECURITY.md SEC-01).

## Review priorities

See ARCHITECTURE.md, SECURITY.md, and ROADMAP.md. Key areas include callback routing, order/stock transaction boundaries, checkout expiry and cancellation, webhook idempotency, admin consistency, and safe deployment configuration.
