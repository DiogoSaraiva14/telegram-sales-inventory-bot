# Roadmap

Status: draft sequencing from a static code review. Keep the work bounded; do not implement multiple phases at once.

## Phase 0 — Confirm scope and ownership

- Decide whether this project remains a portfolio prototype or targets a deployable demo.
- Confirm course/team permissions and attribution requirements for public release.
- Confirm order, stock reservation, payment expiry, cancellation, refund, and admin-surface rules.
- Recover GitLab history only if contribution history is required for the intended portfolio story.

**Done when:** decisions are recorded in PROJECT_GUIDANCE.md and the source baseline is accepted.

## Phase 1 — Establish a runnable, testable baseline

- Add environment validation and a placeholder .env.example.
- Document supported Python version, PostgreSQL setup, bot polling, Django server, and Stripe test-mode webhook setup.
- Fix the customer-order-detail callback namespace mismatch.
- Add focused tests for callback allowlisting, customer order ownership, admin authorization, and current order/payment behavior.
- Add CI after local checks are reproducible.

**Done when:** a new developer can follow setup steps locally and the customer/admin flows have repeatable automated coverage.

## Phase 2 — Make commerce state reliable

- Define one explicit order/payment state machine.
- Make order creation, stock reservation, order lines, movement records, and checkout persistence transactional or compensating.
- Use conditional stock updates/locking to prevent concurrent oversell.
- Release reserved stock when checkout expires or is cancelled according to the agreed policy.
- Make Stripe webhook updates idempotent and safe under retries and concurrent delivery.
- Make payment success/cancellation/refund and order fulfillment consistent across Telegram admin, Django admin, and Stripe.
- Prevent Django admin line edits from leaving order totals, payments, or stock movements inconsistent.
- Separate notification delivery from committed payment/order state and support retry.

**Done when:** failure, retry, duplicate event, expiry, cancellation, and concurrent checkout scenarios preserve agreed invariants.

## Phase 3 — Refactor for maintainability

- Split bot.py into routing, keyboards/presentation, and domain services.
- Replace duplicated Telegram/Django admin business logic with shared operations.
- Give state-changing operations explicit inputs/outputs and audit behavior.
- Bound and externalize rate/session state if multi-process deployment is selected.
- Paginate product, customer, and order lists.

**Done when:** handlers are thin and core business behavior can be tested without a live Telegram or Stripe account.

## Phase 4 — Security and operations

- Fail safely when SECRET_KEY, database, Telegram, or Stripe configuration is missing.
- Replace DEBUG=True and wildcard ALLOWED_HOSTS defaults with explicit environment-specific settings.
- Define HTTPS, trusted-host, cookie, and webhook deployment settings.
- Review PII in logs and audit descriptions; document retention and access.
- Escape dynamic Telegram Markdown, validate uploaded images, and avoid repeated lossy image conversion.
- Pin only intentional direct dependencies and document update policy.

**Done when:** security configuration is explicit, operational errors are observable without leaking secrets, and deployment procedures are documented.

## Phase 5 — Portfolio release

- Create a polished README, architecture diagram, screenshots or short demo, test results, and a truthful feature list.
- Replace third-party sample images with licensed assets or synthetic fixtures.
- Add license/attribution only after ownership is confirmed.
- Publish only after course/team sharing approval and the user explicitly chooses to make the repository public.

**Done when:** a reviewer can understand, run, and assess the project without access to private accounts or real customer data.
