# Project guidance

Status: review snapshot. The overhaul has not started. LocalReach remains the user's main development priority; this Telegram project is being reviewed as a bounded portfolio candidate.

## Product purpose

A Telegram commerce interface for a computer-parts catalog, connected to inventory and order management in Django/PostgreSQL and payments through Stripe Checkout.

The strongest potential portfolio story is an end-to-end order and inventory workflow with clear payment-state handling, an admin surface, auditability, automated tests, and reproducible deployment. These capabilities must be demonstrated before describing the application as complete or production-ready.

## Confirmed from the snapshot

- Telegram bot polling starts from bot.py.
- Django admin and a CSRF-exempt, Stripe-signature-verified webhook are configured.
- The database models cover users, products, orders, order lines, payments, stock movements, and audit records.
- The source uses a single large callback handler and separate Django admin operations.
- core/tests.py contains only the default empty TestCase scaffold.
- No deployment instructions, environment example, CI workflow, or license were present in the imported source.
- Product images and bytecode were excluded from the GitHub review branch.

## Business rules to confirm before redesign

- Whether one order may contain multiple products and quantities.
- When stock is reserved, released, or deducted.
- How checkout expiry, abandoned orders, payment failures, cancellations, and refunds affect inventory and order state.
- Whether a paid order may be cancelled and what refund operation is required.
- Whether checkout prices are snapshotted at order creation.
- Whether both Telegram admin and Django admin remain supported.
- Which data is retained, for how long, and what customers are told about it.

Do not silently choose these policies in code.

## Engineering requirements for a portfolio release

- Use one business-service path for bot callbacks, webhook events, and admin actions.
- Keep database state changes atomic and enforce inventory and order invariants at the database boundary where practical.
- Store provider event identifiers or otherwise prove idempotency for retried and concurrent webhook delivery.
- Separate order/payment state changes from Telegram notification delivery and provide retry behavior.
- Define and validate environment configuration; fail safely when required secrets or host settings are absent.
- Add tests around order creation, stock contention, cancellation/expiry, webhook signature and replay behavior, admin authorization, and callback routing.
- Provide a reproducible local demo with synthetic data and licensed assets.
- Add architecture/setup documentation, a clean dependency manifest, and automated checks before publication.

## Release and attribution

The GitLab source project is private and appears to be a course project. Team authorship, course sharing rules, and public-release permission have not been confirmed. Keep GitHub private until the owner confirms these points. Replace or license the excluded product images before publishing.
