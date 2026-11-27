# Project guidance

Status: imported baseline under overhaul. Done so far:
- a behavior-preserving split of bot.py;
- answering each Telegram callback query once;
- the product-photo fallback (both committed);
- duplicate-safe checkout with recoverable Stripe sessions.

All four are merged into `main`, as are the security fixes SEC-01 to SEC-13 and Stages 1–4 of the cart design.

The business rules decided for checkout are recorded below. This is a bounded portfolio project.

## Product purpose

A Telegram shop with a cart, connected to inventory and order management in Django/PostgreSQL and payments through Stripe Checkout. It is meant to be resold as one copy per client shop.

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
- **Expired sessions (D19, reversed by the cart design on 2026-11-25):** an expired, unpaid Stripe Checkout Session used to be replaced with a new one if the customer retried before the expiry webhook arrived. Now an expired session cancels the order. A completed session waiting for the webhook is never replaced; the customer is told not to pay again.
- **Expiry webhook (SEC-04, owner decision, 2026-11-24):** it cancels the unpaid order, returns its stock in the same transaction, and notifies the customer with the existing cancellation message. Orders that never got a session are cancelled, with their stock returned, by the 5-minute cleanup once they pass the cutoff (`cancelar_encomendas_abandonadas --horas 1`).
- **Cancellation (SEC-06, owner decisions, 2026-11-24):**
  - Cancelling a paid order refunds it in full through Stripe.
  - Cancelling an order with an open payment link expires the link first. If the customer already paid, the order is not cancelled.
  - A payment that arrives for a cancelled order is refunded in full, and the order stays cancelled.
  - The Django admin bulk cancel runs the same operation as the Telegram admin, including the customer message.
  - The project is still an unvalidated portfolio project, not production-ready: nothing has run against live Stripe.
- **Admin changes (SEC-10, owner decisions, 2026-11-24):**
  - Orders, lines and payments are read-only in the Django admin, except an order's notes.
  - Only the Stripe webhook confirms orders; there is no manual confirm.
  - Admins move orders forward only: confirmada → enviada → entregue.
  - A stock edit in the Django admin is applied as a difference and recorded as a stock movement.

## Decisions recorded for the cart and resellable shop (2026-11-24/25)

The design is [docs/designs/cart_resellable_shop.md](docs/designs/cart_resellable_shop.md). The brainstorm and review reports were removed on 2026-11-27; they remain in the git history.

- **Product:** the package is sold, with its rights, to a reseller. The reseller installs one separate copy per client shop, configured in Django admin without code changes. The part picker is dropped.
- **Cart:**
  - stored in the database;
  - at most 20 lines, 99 per line, and never above the current stock when changed;
  - stock is taken only at Confirm;
  - inactive or short-stocked lines are flagged and block checkout, and are not removed automatically;
  - a price change only marks the line, and the customer pays the price on the confirmation screen.
- **Confirmation screen:** saved as a snapshot that never changes. Confirm compares it with live data under locks, and writes nothing if anything changed.
- **One pending order per customer,** including orders from before the cart.
- **Payment window:** 60 minutes from the order, for every shop. Links are created only until the order is 28 minutes old.
- **Review decision A:** one reconciling cleanup every 5 minutes, verified during setup, instead of a drain command and a startup block. It cancels and returns stock for unpaid or expired orders, confirms paid ones, and leaves the order and retries on Stripe errors. The 65-minute limit on held stock is a target that assumes the cleanup and its dependencies work; the log is checked daily, with no automatic alert.
- **Review decision B:** keep "Repetir encomenda" for cancelled unpaid orders. A customer "cancel my pending order" button is deferred.
- **Review decision C:** the shop owner's Django account can add, edit and deactivate products and categories, but can't cancel or refund. This is enforced by server-side permission checks. Cancellation and refunds stay in the Telegram admin panel.
- **Telegram admins:** stored as `Utilizador.e_admin`, which only the reseller's superuser can change. `ADMIN_TELEGRAM_IDS` only bootstraps the first admin, and after handover holds the reseller's id or nothing.
- **Customer messages (owner decisions, 2026-11-25):** every order message is queued and resent by the 5-minute cleanup for 24 hours. A message Telegram refuses, or still undelivered after 24 hours, is given up and recorded as "Notificação falhada" in the audit log.
- **Retention:**
  - confirmation screens without an order are deleted after 45 minutes;
  - carts after 30 days without a change;
  - screens linked to an order are kept.
- **Personal data (owner decisions, 2026-11-27):**
  - audit descriptions name a customer only by internal id ("Cliente #12");
  - sent or given-up customer messages are deleted after 30 days;
  - orders, payments and the audit log are not deleted automatically: each shop sets its own period from its legal duties (DEPLOYMENT.md says how);
  - a customer's data is removed on request with the superuser-only "Anonimizar" action in Django admin; their orders stay for the accounts.
- **Release safety:** migrations only move forward, with a database backup before each one.
- **Legal:** invoicing, tax, shipping, withdrawal handling and legal texts stay outside the bot and are each shop's responsibility, stated in the reseller contract. Not legal advice; unverified.
- **Stage 0 outcome (2026-11-27):** the owner states the code's rights are theirs; `LICENSE` reserves all rights; the demo uses generated placeholder images. The invoicing clause is drafted in the reseller contract (kept outside the repository), pending a lawyer's review; the Stripe checklist is done. Details and the decision record: [docs/stage0_checklist.md](docs/stage0_checklist.md).
- **End-to-end run (G13):** Done on 2026-11-27 (demo database, Stripe test mode, real webhook through the Stripe CLI): a two-product order with a changed quantity was paid with a test card, confirmed by the webhook, and the customer got "Pagamento confirmado"; the admin marked it shipped and delivered (a message for each); an unpaid order was cancelled with its stock returned; stock buttons, product on/off and customer block/unblock worked and were audited; stock matched the paid orders. The rest ran the same day: a price changed while in a cart was flagged, the old confirmation screen was refused and the order was charged the new price; a paid order was cancelled through the admin cancel service and Stripe showed a full refund (succeeded, €69.90), with the stock returned and the refund message sent; an unpaid order left for 60 minutes was cancelled by Stripe's `checkout.session.expired` webhook with its stock returned. Fixes from the run: Django web logging, order lines on the admin order screen, a confirmation before cancelling, no blocking of admins from Telegram, and back buttons.

## Customer language (2026-11-27)

- Every bot user, customer or Telegram admin, sees the bot in Portuguese (pt-PT) or English. `Utilizador.idioma` is filled from Telegram's `language_code` on /start while empty (`pt*` → Portuguese, anything else → English, none → Portuguese) and changed only by the customer's 🌐 button.
- Order messages are queued in the customer's saved language.
- Not translated: Django admin, stored data (audit descriptions, product, category and customer names) and the shop's welcome text. Audit action names are translated. Stripe Checkout picks its language from the browser.
- English texts live in `telegram_bot/traducoes.py`, keyed by the Portuguese text; a test fails if a bot text has no English version.

## Business rules still open

- None in code. What customers are told about their data (the privacy notice) is each shop's responsibility.

Do not silently choose new policies in code.

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

The project began as the owner's course project; the owner states the rights are theirs (2026-11-27). `LICENSE` reserves all rights, and the original product images are not used. The repository is public since 2026-11-27; the licence still reserves all rights.
