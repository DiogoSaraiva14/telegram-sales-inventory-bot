# Telegram Sales and Inventory Bot

A Telegram interface for a computer-parts catalog, backed by Django and PostgreSQL, with Stripe Checkout for customer payments.

## Status

**Review snapshot; not yet validated for production or CV claims.** This private branch contains the source snapshot imported from GitLab for review and overhaul planning. The original GitHub version remains on main.

The application code has not been changed as part of the documentation review. No tests or runtime checks were run. The current core/tests.py file is an empty scaffold.

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
- The import PR is draft. Do not merge it until the source baseline and overhaul scope are reviewed.
- Confirm course/team sharing permissions and replace or license demo assets before making the repository public.

## Configuration

The source reads Django, PostgreSQL, Telegram, and Stripe settings from environment variables. The names currently referenced include SECRET_KEY, DEBUG, DB_NAME, DB_USER, DB_PASSWORD, DB_HOST, DB_PORT, TELEGRAM_TOKEN, ADMIN_TELEGRAM_IDS, STRIPE_SECRET_KEY, STRIPE_WEBHOOK_SECRET, STRIPE_SUCCESS_URL, and STRIPE_CANCEL_URL.

There is no committed environment example or validated startup guide yet. Do not put real values in this repository. Configuration defaults need hardening before a public or production deployment.

## Review priorities

See ARCHITECTURE.md, SECURITY.md, and ROADMAP.md. Key areas include callback routing, order/stock transaction boundaries, checkout expiry and cancellation, webhook idempotency, admin consistency, and safe deployment configuration.
