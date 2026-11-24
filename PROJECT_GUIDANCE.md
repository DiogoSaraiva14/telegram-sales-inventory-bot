# Project guidance

Status: imported baseline under overhaul. Done so far:
- a behavior-preserving split of bot.py;
- answering each Telegram callback query once;
- the product-photo fallback (both committed);
- duplicate-safe checkout with recoverable Stripe sessions (on branch `codex/checkout-change-2`).

The business rules decided for checkout are recorded below. LocalReach remains the user's main development priority; this Telegram project is being reviewed as a bounded portfolio candidate.

## Product purpose

A Telegram commerce interface for a computer-parts catalog, connected to inventory and order management in Django/PostgreSQL and payments through Stripe Checkout.

The strongest potential portfolio story is an end-to-end order and inventory workflow with clear payment-state handling, an admin surface, auditability, automated tests, and reproducible deployment. These capabilities must be demonstrated before describing the application as complete or production-ready.

## Confirmed from the snapshot

- Telegram bot polling starts from bot.py.
- Django admin and a CSRF-exempt, Stripe-signature-verified webhook are configured.
- The database models cover users, products, orders, order lines, payments, stock movements, and audit records.
- The imported source used a single large callback handler in bot.py. It has since been split into the telegram_bot/ package (behavior-preserving refactor); Django admin operations remain separate.
- At import, core/tests.py contained only the default empty TestCase scaffold. It now holds the PostgreSQL checkout tests (see README.md).
- No deployment instructions, environment example, CI workflow, or license were present in the imported source.
- Product images and bytecode were excluded from this repository.

## Decisions recorded for duplicate-safe checkout (2026-11-22)

- **One order per screen:** one Confirm screen creates at most one order. A repeat tap shows "Esta encomenda já foi registada." and recovers the payment link.
- **Expiry:** unused Confirm buttons expire 30 minutes after the screen was drawn. Buttons from before the change are treated as expired. An existing order is never blocked by expiry.
- **Stock:** reserved when the order is created. Checkout is refused when stock is insufficient, never clamped at zero.
- **Audit:** written in the same transaction as the order.
- **Price:** charged as saved on the order (price snapshot), and the product name is snapshotted on the order line. Stripe gets no product description.
- **Expired sessions:** an expired, unpaid Stripe Checkout Session is replaced with a new one. A completed session waiting for the webhook is never replaced; the customer is told not to pay again.
- **Still open:** releasing stock for abandoned or expired orders (SEC-04).
- **Deferred to SEC-06 (owner decision, 2026-11-22):** expiring the Stripe session when an order is cancelled, refunds, and how the webhook treats payments for cancelled orders. Until then, a cancelled order may still be paid and confirmed, so the project stays an unvalidated portfolio project, not production-ready.

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
