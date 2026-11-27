# Roadmap

Status: draft sequencing from a static code review. Keep the work bounded; do not implement multiple phases at once.

## Progress (2026-11-24)

| Item | Phase | State |
| --- | --- | --- |
| Split bot.py into routing, keyboards/presentation and services | 3 | Done |
| Answer each Telegram callback once | — | Done |
| Product screen survives a missing or failed photo | 4 (partly) | Done |
| Transactional order creation with a product row lock (no oversell) | 2 | Done for checkout, admin cancellation, status steps and stock edits (SEC-10) |
| One order per Confirm screen, recoverable Stripe sessions | 2 | Done (SEC-13, SEC-03) |
| Tests for callback routing, admin gate, checkout | 1 | Done: mock tests plus PostgreSQL tests (`manage.py test core telegram_bot`) |
| Fix the order-detail callback mismatch (SEC-12) | 1 | Done |
| `.env.example`, environment validation, setup guide | 1 | Done (Python version documented as the one tested: 3.13) |
| CI | 1 | Added: GitHub Actions runs check, migration drift and the PostgreSQL suite |
| Expiry stock release (SEC-04) | 2 | Done for expired sessions; orders that never got a session still hold stock |
| Webhook idempotency (SEC-05) | 2 | Done with row locks; customer messages queued and retried for 24 h |
| Cancellation and refunds (SEC-06) | 2 | Done: one cancel operation for both admin surfaces, session expiry, full refunds; closes the checkout cancellation window |
| Safe settings defaults (SEC-01) | 4 | Done; HTTPS, cookie and HSTS settings added in cart Stage 4 (opt-in with `DJANGO_HTTPS`) |
| Escape dynamic Telegram Markdown (SEC-07) | 4 | Done; not checked against live Telegram |
| No raw Telegram ids, usernames or bot token in logs (SEC-08) | 4 | Done; bot logs rotate daily, 14 days kept |
| Release stock of abandoned orders without a payment session (SEC-03) | 2 | Done: `manage.py cancelar_encomendas_abandonadas`, to be scheduled |
| Process product images once, through storage (SEC-11) | 4 | Done |
| Bounded in-process rate and session state (SEC-09) | 3 | Done; single polling process documented |

## Cart and resellable shop (2026-11-25)

The plan is [docs/designs/cart_resellable_shop.md](docs/designs/cart_resellable_shop.md). Each stage is one PR, merged after CI.

| Stage | What | State |
| --- | --- | --- |
| 0 | Code ownership and course rights, licence, demo-image rights, invoicing clause, one Stripe test run | **Open.** It blocks the reseller goal; until it passes, the project stays a portfolio demo |
| 1 | Categories in the database (migrations 0009–0011) | Done |
| 2 | Multi-line orders from saved confirmation screens, 60-minute payment window, reconciling cleanup (0012) | Done |
| 3 | Cart, "Repetir encomenda", draft retention (0013) | Done |
| 4 | Shop settings, Telegram admin flag, "Dono da loja" group, HTTPS settings, DEPLOYMENT.md (0014–0015) | Done |
| — | End-to-end run in Stripe test mode with a real webhook (G13) | Not done |
| — | Customer "cancel my pending order" button, abandoned-cart reminders, a welcome image | Deferred |

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

- Define one explicit order/payment state machine. (Done: `Encomenda.TRANSICOES` and `Pagamento.TRANSICOES` list every allowed change, and every service sets a state through `services._mudar_estado`, which refuses any other.)
- Make order creation, stock reservation, order lines, movement records, and checkout persistence transactional or compensating.
- Use conditional stock updates/locking to prevent concurrent oversell.
- Release reserved stock when checkout expires or is cancelled according to the agreed policy.
- Make Stripe webhook updates idempotent and safe under retries and concurrent delivery.
- Make payment success/cancellation/refund and order fulfillment consistent across Telegram admin, Django admin, and Stripe.
- Prevent Django admin line edits from leaving order totals, payments, or stock movements inconsistent.
- Separate notification delivery from committed payment/order state and support retry. (Done: each order and payment service queues its message in the same transaction as the change; the caller sends it, and the cleanup run resends anything still queued for 24 hours.)

**Done when:** failure, retry, duplicate event, expiry, cancellation, and concurrent checkout scenarios preserve agreed invariants.

## Phase 3 — Refactor for maintainability

- Split bot.py into routing, keyboards/presentation, and domain services. (Done: telegram_bot/ and core/services.py. Bot checkout is transactional, and so are the Telegram admin product, stock and customer actions, each with its audit row in the same transaction.)
- Replace duplicated Telegram/Django admin business logic with shared operations. (Done: cancelling, the status steps, stock edits, product activation and customer blocking each use one service from both admin surfaces.)
- Give state-changing operations explicit inputs/outputs and audit behavior. (Done: every service that changes an order, payment, stock, product or customer returns its result and writes its audit row and customer message in the same transaction. Cart drafts and draft cleanup are not audited, by design.)
- Rate and session state are bounded and single-process (SEC-09). Externalize them only if the bot moves to webhook mode with several workers.
- Paginate product, customer, and order lists. (Done: the Telegram admin product, stock, order, customer and audit lists show 10 per page.)

**Done when:** handlers are thin and core business behavior can be tested without a live Telegram or Stripe account.

## Phase 4 — Security and operations

- Fail safely when SECRET_KEY, database, Telegram, or Stripe configuration is missing.
- Replace DEBUG=True and wildcard ALLOWED_HOSTS defaults with explicit environment-specific settings.
- Define HTTPS, trusted-host, cookie, and webhook deployment settings.
- Review PII in logs and audit descriptions; document retention and access. (Done: audit text uses internal customer ids, stored messages are deleted after 30 days, Django admin can anonymise a customer, and DEPLOYMENT.md lists what is kept, for how long and who sees it.)
- Escape dynamic Telegram Markdown, validate uploaded images, and avoid repeated lossy image conversion.
- Pin only intentional direct dependencies and document update policy. (Done: `requirements.in` lists the direct packages, `requirements.txt` is generated from it, and README.md has the update policy.)

**Done when:** security configuration is explicit, operational errors are observable without leaking secrets, and deployment procedures are documented.

## Phase 5 — Portfolio release

- Create a polished README, architecture diagram, screenshots or short demo, test results, and a truthful feature list.
- Replace third-party sample images with licensed assets or synthetic fixtures.
- Add license/attribution only after ownership is confirmed.
- Publish only after course/team sharing approval and the user explicitly chooses to make the repository public.

**Done when:** a reviewer can understand, run, and assess the project without access to private accounts or real customer data.
