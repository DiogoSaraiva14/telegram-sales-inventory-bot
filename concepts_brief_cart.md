# Multi-product cart and a resellable single-shop package

* **Author:** Jose Diogo Pereira Saraiva
* **Date:** 2026-11-24
* **Background:** [goldfish_cart_report.md](goldfish_cart_report.md) holds the full Goldfish review: 7 lenses, 5 blockers (B1–B5), 12 major findings (M1–M12) and 7 nits (N1–N7). Ids below refer to it.

## Overview

The bot sells one product per order today. The goal is a package the owner can sell, with its rights, to a reseller. The reseller then installs a separate copy of the bot for each client shop.

That needs two things:

- **A cart.** A customer collects several products, with quantities, and pays for them in one order.
- **Shop setup without code changes.** Each copy takes its shop name, welcome text and product categories from data the reseller enters in Django admin. Today these are hard-coded for a PC-parts shop called "Jarvis".

The PC part picker with compatibility checks and abandoned-cart reminders were dropped during ideation; reminders are kept as a future idea.

**Who benefits:**
- the buyer of the rights: a reseller, such as an agency or freelancer, who deploys and supports shops;
- the reseller's clients: small shops selling through Telegram;
- their customers.

For now the project is also a portfolio piece, so code quality, tests and documentation are part of what is being sold.

## Analysis

### Risks, stated first

- **The right to sell is unproven (B1).**
  - The owner states the code started as their solo university final project and has since changed substantially. The repository does not yet reflect this:
    - `PROJECT_GUIDANCE.md` still lists team authorship, course sharing rules and release permission as unconfirmed;
    - there is no LICENSE file;
    - the demo product images are not licensed.
  - A heavily changed version can still count as derived from the original. A university's intellectual-property rules can apply to degree work.
  - This decides whether a sale can happen at all, so it comes first, before the code.
- **Checking out several products adds two failures that only show up in production:**
  - **Deadlocks (B3).** Two carts that share products can lock product rows in opposite order and block each other. Lock every product in one query ordered by id, and prove it with a threaded test.
  - **Rejected payment retries (B4).** `parametros_checkout` calls `encomenda.linhas.get()` (`core/services.py:243`), which fails on any order with two lines. Stripe rejects a reused idempotency key sent with different parameters, and the new request differs anyway (it adds `expires_at` and builds one entry per line). So orders pending at deploy cannot be retried with their old keys; see "Pre-cart orders at deploy".
- **Holding stock without paying (M1).**
  - With quantity limited only by stock, one customer can confirm the whole stock and not pay.
  - The stock then stays out until the Stripe session expires (24 hours by default) or the abandoned-order command runs.
- **Rolling back the code (M8).** Once multi-line orders exist, older code crashes on them. Decided: the cart release is forward-only, with backups (see Key Decisions).
- **Two different sales carry different legal duties (M11).** This is not legal advice; each party checks its own position.
  - **The owner's sale of the software rights to a reseller** is a business-to-business sale of software. It needs a written contract and licence terms (ownership, what the reseller may do, warranty limits). Consumer rules do not apply to it.
  - **Each shop's sales to its customers** are the shop's own business, and the shop carries these duties:
    - **Invoices.** Portuguese businesses must issue invoices for their sales. Many must use AT-certified billing software, but the Tax Authority (AT) also lists other ways for those not required to use it. Stripe receipts are not invoices. The bot does not issue invoices; each shop arranges invoicing itself.
    - **Withdrawal right.** Consumers buying at a distance generally have 14 days to withdraw, with exceptions set by the EU Consumer Rights Directive and its Portuguese transposition. The bot does not handle withdrawals; refunds go through the admin cancel, which only covers unpaid or not yet shipped orders today.
    - **Terms of sale and a privacy notice** for the shop's customers.
  - **Personal data.** Each shop is normally the controller of its customers' data. Whether the reseller is a processor depends on what the reseller does: if it hosts or operates the copy for the shop, it processes data on the shop's behalf and needs a processor contract (GDPR Article 28). If it only hands over the software, it may not process data at all. The roles are decided per deployment, not in the code.
  - The reseller contract and the deployment guide must say that invoicing, withdrawals and legal texts stay outside the bot.
- **The package is unproven.** It has never run against live Stripe or Telegram. SEC-01's HTTPS, cookie and HSTS settings are still open, and a copy per shop means one database, one polling process, one webhook and one cron file per client (M12).

### Corrections to the starting assumptions

- **Stripe parameters.** They come from `linhas.get()`, not `linhas[0]`, so two lines break payment-link creation outright instead of quietly using the first line.
- **The Confirm token.** It is 64 bytes only at the largest product id. The fixed part is about 45 bytes, so taking `produto_id` out frees about 19 bytes for a reference to the cart or a snapshot.

### What already works for several lines, and what does not

These parts already work for orders with several lines and keep their logic:
- refunds (one full refund of the order's payment);
- webhook confirmation and expiry handling (they lock the order, not products);
- the order detail screen;
- the admin inline;
- `Pagamento.valor = encomenda.total`.

These parts change even though they run after an order is created:
- `parametros_checkout`: one Stripe line per order line, ordered by line id, plus `expires_at`;
- the payment screen, which lists every line instead of `linhas[0]`;
- `_devolver_stock` (`core/services.py:397`), used by cancellation, expiry and the abandoned-order command: it loops over the order lines in no fixed order. It must lock the order's products in id order first, the same order checkout uses (see "One lock order for every multi-product stock change").

### Ideal Final Result

- **The cart is a draft.** It holds (product, quantity) rows and reserves nothing.
- **The confirmation screen is a numbered snapshot of that draft.**
- **The order is a frozen copy of the snapshot the customer confirmed.**
- **Confirm runs as one transaction.** It locks every product in id order and compares price, stock and active status against the snapshot. Then it does one of two things:
  - writes every order line, the stock movements, one audit row and the total, and empties the cart;
  - creates nothing and redraws the screen with the changes marked.
- **The customer pays exactly what they saw.**
- **The reseller deploys a shop** with one `.env`, one `migrate`, one cron file and the admin data, without editing code.

### Secondary trade-offs

- **Shop-settings row or environment variable (M4).** A single settings row, fixed at `pk=1` and with add and delete blocked, lets the owner edit the name and welcome text in Django admin. An environment variable is smaller, but only the reseller can change it.
- **Moving categories to a table (M3)** touches about nine places in the code plus tests. It needs a three-step migration that keeps existing products, and old category buttons in chat history must fail gracefully.
- **Two admin surfaces (M5).** The Telegram admin is controlled by `ADMIN_TELEGRAM_IDS`, which only the reseller can change. Django staff accounts are separate. The brief must say which surface the shop owner uses.

## Goals vs. Non-Goals

* **In-Scope Goals:**
  * **A cart stored in the database that survives bot restarts.**
    * Quantity per line is limited by stock, with a fixed maximum per line and about 20 lines per cart (M6).
    * Plus, minus and remove buttons.
    * Inactive or sold-out products are flagged in the cart and block checkout (M2).
  * **Checkout is all or nothing.**
    * The confirmation screen shows current prices and marks lines whose price changed since they were added.
    * Confirm compares the screen against live data under locks. Any difference creates no order and redraws the screen.
    * The order keeps the prices the customer confirmed.
  * **One order with several lines** produces one multi-line Stripe Checkout session. Cancellation and refunds work as today: whole order only, full refund.
  * **One pending order per customer,** and the cart is emptied when the order is created (B5).
  * **Shop setup in Django admin:** shop name, welcome text and a `Categoria` table replacing the 8 fixed PC categories. pt-PT, EUR and Stripe stay fixed, and credentials stay in environment variables.
  * **A reseller deployment guide and a startup configuration check** (M12).
* **Out-of-Scope Non-Goals:**
  * A PC part picker or compatibility checks.
  * One deployment serving several shops.
  * Other languages, currencies or payment providers.
  * Reserving stock when an item is added to the cart.
  * Partial refunds or cancelling single lines.
  * Invoicing, tax documents and shipping costs.
  * Email collection, discounts and coupons.
  * A welcome image or other in-bot branding beyond the shop name and welcome text.
  * Abandoned-cart reminders (kept as a future idea).

## Alternatives Weighed

* **PC part picker with compatibility checks.** Rejected by author: it only matters to PC-parts shops, and it needs structured spec data for every product, which someone must keep accurate. That conflicts with selling a general shop package.
* **One deployment serving many shops.** Rejected by author: a copy per shop needs no shop id on every table and has no risk of one shop seeing another's data. It also keeps the single-polling-process model from SEC-09.
* **Reserving stock when an item is added to the cart.** Rejected by author: stock is taken at checkout. Reserving at add needs timed cleanup and makes hoarding worse.
* **A pending order (`estado='carrinho'`) used as the cart.** Rejected: it would leak into the abandoned-order cleanup, the admin actions and every SEC lock path. Separate cart tables avoid that.
* **Removing sold-out items from the cart automatically.** Rejected by author: the customer is told and removes the item, so the order never differs silently from what they chose.
* **A per-country price rule.** Rejected: one rule (show the final price at Confirm and charge exactly that) matches EU consumer rules without a setting. Resellers outside the EU check locally.
* **Abandoned-cart recovery by email or with discounts.** Rejected for now: the bot has no email addresses, and discounts need a coupon system and per-order Stripe prices.
* **Opt-in abandoned-cart reminders through the bot.** Rejected by author for this brief: they need consent, an opt-out, delivery safeguards and another scheduled job without strengthening the cart demo. Kept as a future idea.
* **Making the bot's logo configurable.** Rejected: bots cannot set their profile photo through the API. It is set by hand in BotFather. Inside the bot, a logo could only be a welcome image.
* **A hash of the cart lines in the Confirm button (B2 option C), or a cart revision number (option B).** Rejected by author: a hash can show that something changed but can't recover what the customer confirmed; a stored snapshot can.
* **Restoring the cart automatically, or an "Edit" button that cancels a pending order.** Rejected by author: automatic restore surprises the customer, and "Edit" races a payment that may be finishing at the same time. "Repeat order" on cancelled unpaid orders was chosen instead.
* **A payment window of 60 minutes per Stripe session instead of per order.** Rejected by author: retries would extend the hold, against the reason for a short window (protecting scarce stock). Each session would get a full hour, but stock could stay held well past 60 minutes after Confirm. Without a cap on how late the first session may be created, a session created just before the no-session cleanup could keep stock held for close to two hours.
* **A quantity picker on the existing one-product order instead of a cart.** Raised by zoom-out as a smaller first step. Rejected by author: it is a temporary feature the cart would replace, and the staged plan already limits the risk of the multi-line change.

## Key Decisions & Staging for Next Steps

### Decided by the author

- **Product scope.** The package is sold as software rights to a reseller, who deploys one copy per client shop. The part picker is dropped.
- **Stock and checkout.**
  - Stock is taken at checkout, not when an item is added.
  - Sold-out lines are flagged, and the customer removes them before checkout.
  - The maximum quantity per line is the available stock.
- **Prices.**
  - The cart shows prices for information only.
  - The confirmation screen shows the final price, with changed lines marked.
  - The customer is charged exactly what they confirmed.
- **The cart survives bot restarts.**
- **Shop setup.**
  - The shop name, welcome text and categories are edited by the reseller in Django admin.
  - pt-PT, EUR and Stripe stay fixed.
  - Credentials stay in environment variables.
  - The bot's profile photo is set in BotFather.
- **Abandoned carts.** No reminders in this brief. If added later, they are an opt-in bot message, not email or discounts (see Future ideas).
- **Confirmation screen (B2): option A, a stored snapshot that never changes.**
  - Drawing the confirmation screen writes one snapshot row: the nonce (unique), the user, the time, the currency, the total, and per line the product id, the name shown, the quantity and the unit price.
  - The Confirm button carries only the nonce and the screen time.
  - Confirm locks the products, compares the snapshot with live data and builds the order lines from the snapshot. The order keeps a link to its snapshot.
  - **Limit:** the snapshot records what the bot prepared. It does not prove Telegram delivered the screen or that the customer read it.
  - **Cost:** one database write every time the screen is drawn.
  - **Single use and stale buttons:**
    - Confirm locks the snapshot row (`select_for_update`) before anything else and checks that it belongs to the user who tapped and that its screen time is within 30 minutes.
    - A snapshot becomes an order at most once: the order holds a unique, one-to-one link to its snapshot, and a second tap on the same screen finds the existing order and recovers its payment link ("existing order always wins", as today).
    - Confirming an old screen never touches newer cart contents. The order is built from the snapshot, and only the cart lines that match the snapshot (same product, quantity not changed since) are removed. If the cart changed since the screen was drawn, the snapshot no longer matches it and Confirm redraws the screen instead of ordering.
    - **One pending order per customer** is enforced under concurrency: Confirm locks the customer's row and checks for a pending order in the same transaction, and a partial unique index (one `pendente` order per user) backs it up in the database. Threaded tests cover two Confirm taps from different screens and two snapshots of the same cart.
  - **Retention and who deletes what (decided, Open item 12):**
    - A new management command, scheduled in the same cron file every 5 minutes as the abandoned-order command, deletes snapshots that never became an order once they are older than 45 minutes (the 30-minute screen validity plus a 15-minute margin).
    - Snapshots linked to an order are never deleted by this command; they are kept as long as the order.
    - Carts idle for 30 days are deleted by the same command. "Idle" is measured from the cart's last activity: a last-activity time on the cart, updated whenever a line is added, changed or removed (including by "Repeat order"). Viewing the cart does not count.
- **After an unpaid order is cancelled or expires (B5): a "Repeat order" button.**
  - It appears only on cancelled orders that were never paid, and the old order never changes.
  - It copies the old order's products and quantities into the cart, then the cart shows current prices, availability and stock like any other cart. Items that can't be added (product inactive or out of stock) are listed.
  - The customer reviews and confirms a new order. Nothing reuses the old payment session, and nothing is ordered automatically.
  - **Merge into an existing cart (decided):** if the cart already has items, the old order's lines are added to it. A product already in the cart gets the quantities added, capped by stock and the per-line maximum. The customer is told when a quantity was reduced or an item could not be added.
- **Unpaid stock is held for 60 minutes (M1), fixed for the first version.**
  - **Hard deadline from order creation (decided, Open item 10).**
    - Every Stripe Checkout Session for an order gets `expires_at` = `data_encomenda` + 60 minutes, computed from the stored value so retries with one idempotency key send identical parameters.
    - Stripe requires `expires_at` to be 30 minutes to 24 hours after the session is created. A session is therefore created or replaced only while the order is at most 28 minutes old: 30 minutes of Stripe minimum plus a 2-minute margin for clock differences and request time.
    - Past that cutoff, no session is created or replaced. The order is cancelled through `cancelar_encomenda` and its stock returned, and the customer is told the order expired and can use "Repeat order".
    - Retries never extend the hold. An order with a Stripe link holds stock at most 60 minutes after Confirm. An order that never gets a link holds it up to 65 minutes, because the cleanup runs every 5 minutes.
    - The replacement-session path (D19) effectively ends: a session can only expire at the 60-minute deadline, which is past the cutoff, so an expired session always means the order is over.
    - Boundary tests: a first session at 27, 28 and 29 minutes after the order; a retry with the same key on each side of the cutoff; a replacement attempt after the session expired; and the cancellation and message when the cutoff has passed.
  - **Payment arriving around expiry (checked against the code):**
    - Payments are card-only (`payment_method_types=['card']`), so `checkout.session.completed` always means paid. There are no delayed bank payments to wait for.
    - Stripe never reports both completed and expired for the same session.
    - Both webhooks already take the order row lock (SEC-04, SEC-05). If the expiry cancels first and a completion still arrives for that order, the payment is refunded in full and the order stays cancelled (SEC-06). The stock released by the expiry is never sold twice: an order is either paid and keeps its stock, or cancelled with its stock returned and any payment refunded.
- **One lock order for every multi-product stock change (B3).**
  - Every path that changes the stock of several products locks those product rows in one query ordered by product id before writing: checkout, `_devolver_stock` (cancellation, expiry, the abandoned-order command), and any future multi-product admin action.
  - Single-product paths (`ajustar_stock`) keep locking one row, which cannot deadlock against id-ordered locks.
  - Threaded tests: two checkouts with overlapping products in opposite cart order, and a checkout racing a cancellation that returns stock for overlapping products.
- **Pre-cart orders at deploy (decided, Open item 11).** Orders created before the cart release keep their old idempotency keys, and the new `parametros_checkout` sends different parameters, so Stripe would reject their retries. Some also have `nome_produto` empty (migration 0008 left older rows null), which today stops payment-link creation. So they are drained before the switch:
  - **Which orders:** "legacy" means a pending order with no linked confirmation snapshot. Every order created by the new code has one, so the rule never touches them.
  - **How:** a one-off drain command runs each legacy pending order through `cancelar_encomenda`, which expires an open Stripe session and returns the stock.
  - **Payments in flight are left to the webhook:** if Stripe reports the session complete, `cancelar_encomenda` already refuses (`PagamentoRecebido`), and any Stripe error leaves the order unchanged. The drain command skips those orders, lists them, and is run again after the webhook has confirmed (or the session has expired and cancelled) them.
  - **Startup check:** the new code refuses to start while any legacy pending order remains, and names them. It ignores pending orders that have a snapshot, so normal pending orders never block a start.
  - Paid, cancelled and later orders are untouched.
  - **Orders that never got a payment session (decided):** run the abandoned-order command every 5 minutes with `--horas 1`, so they release stock after 60 to 65 minutes. Run hourly, the hold could approach 2 hours.
  - Configurable per shop only if a real client needs a different policy.
  - Rejected by author: an "Edit" button that cancels a pending order and restores its cart, because the cancel can race a payment finishing in another tab.
- **Two admin surfaces with a clear split (M5).**
  - **Telegram panel, daily operations:** view orders, move them forward or cancel them, adjust stock.
  - **Django admin, catalogue and profile:** products, categories and the shop-settings record. The shop owner gets a limited staff account; the reseller does the first setup and keeps the superuser account.
  - **Prerequisites before a shop owner gets the Django admin:**
    - secure hosting: HTTPS, secure cookies, HSTS and `CSRF_TRUSTED_ORIGINS`, the part of SEC-01 still open;
    - a permission group for shop owners with change rights on products, categories and shop settings, read-only orders and payments, and no access to staff accounts, audit rows or stock movements.
  - **Telegram admins become manageable data, not an env-only list.**
    - Decided: an "admin" flag on `Utilizador` that only the reseller's superuser can grant or remove in Django admin. The shop owner's Django account cannot manage staff accounts or promote Telegram admins.
    - `ADMIN_TELEGRAM_IDS` stays as the first admin when the database is empty, so a fresh install is not locked out.
    - Today the env list is read twice at import (`telegram_bot/guards.py` and `security/decorators.py`). Both checks must move to the one database source, and a change must take effect without restarting the bot.
- **No quantity picker; build straight to the cart.**
  - Rejected by author: a quantity picker on the one-product order would be a temporary feature replaced by the cart, and the staged plan already reduces the risk.
  - The first visible change can wait. The target is one complete demo: add items, change quantities, review current prices and stock, then place and pay for a multi-item order. It runs in Stripe test mode with a real webhook, which the Stage 0 test run sets up.
- **Release safety (M8): forward-only, no one-line setting.**
  - The demo is the first place the cart runs, and no real shop depends on the bot yet, so a rollback flag would add a release step for a risk that does not exist yet.
  - The demo uses its own database, Stripe test mode and a real webhook.
  - Before any shop relies on the bot, the deployment guide must cover backups and recovery for that shop: a database backup before every migration, and how to restore it. Forward-only means fixing with a new release, not running without backups.
- **Category buttons carry the database id (M3): `cat_<id>`.**
  - Rejected by author: a unique short-name field per category. Old catalogue buttons are not worth a permanent field owners must maintain, since there are no real customers yet.
  - A button for an inactive or unknown category shows the expired-menu message with a button that reopens the catalogue. That covers pre-migration buttons such as `cat_ssd`: the `cat_` prefix stays allowed, and any value that is not the id of an active category takes this path instead of the generic "invalid request" alert.
  - Category ids stay stable and are never reused. Categories are deactivated, not deleted (`on_delete=PROTECT` from products), and PostgreSQL sequences never hand out an id twice.
- **No welcome image in this version (M4).** The shop-settings record holds only the shop name and welcome text, and `/start` shows them. The bot's profile photo, set per shop in BotFather, gives the visual branding. Rejected by author for now: a separate welcome image, which needs upload processing, caching of Telegram's stored copy and a text fallback without making the cart demo stronger.
- **Stage 0 is a prerequisite.** The code ownership, course rules and image rights checks happen before any cart code is written. The project record is updated with the verified outcome (what was checked, with whom, and what it says), not only the owner's statement.

### Open: to settle before `/egm-draft`

1. ~~How the confirmation screen is carried (B2).~~ Decided: option A, see above.
2. ~~What happens to the cart after an unpaid order expires or is cancelled (B5).~~ Decided: "Repeat order", see above.
3. ~~How long confirmed but unpaid stock is held (M1).~~ Decided: 60 minutes, see above.
4. ~~Categories in callbacks (M3).~~ Decided: database id, see above.
5. ~~Whether the welcome image is worth building (M4).~~ Decided: deferred, see above.
6. ~~Which admin surface the shop owner uses (M5).~~ Decided: both, split by task, see above.
7. ~~Release safety (M8).~~ Decided: forward-only, see above.
8. ~~The first step.~~ Decided: no quantity picker, see above.
9. ~~Abandoned-cart reminders (M10).~~ Decided: dropped from this brief, kept as a future idea below.

Items 1–9 are settled, including the Telegram admin flag. A second review raised three more:

10. ~~The payment window at its boundary.~~ Decided: hard deadline from order creation, with a 28-minute cutoff for creating sessions. See "Unpaid stock is held for 60 minutes".
11. ~~Pre-cart orders at deploy.~~ Decided: drain legacy pending orders (no snapshot) before the switch, leave in-flight payments to the webhook, and block startup only on legacy pending orders.
12. ~~Retention periods.~~ Decided: unconfirmed snapshots deleted after 45 minutes, order-linked snapshots never deleted by the command, carts deleted after 30 days without activity.

All open decisions (1–12) are settled.

### Future ideas (not in this brief)

- **Abandoned-cart reminders.** Revisit if a reseller or client asks. They need stored consent with a timestamp, an opt-out inside the bot, one reminder per cart, turning the opt-in off when a user blocks the bot, skipping blocked accounts, a sending-rate limit, and another scheduled command. They are EU marketing messages, so consent rules apply. The cart tables already hold the data, so adding them later needs no redesign.
- **A welcome image** per shop.
- **A configurable payment window** per shop, if a client needs something other than 60 minutes.

### Proposed staging (all lenses agree)

- **Stage 0, decisions and proof, no code.**
  - Confirm the rights and choose a licence.
  - Replace or license the demo images.
  - Settle the invoicing position.
  - Resolve the authorship and course-rights discrepancy first (the university's rules, any supervisor or institutional claim), then update `PROJECT_GUIDANCE.md` with the verified outcome and these decisions.
  - Run one full purchase, refund and expiry in Stripe test mode with a real webhook.
- **Stage 1: the `Categoria` table** and its three-step migration. It lands first, before any order or cart change.
- **Stage 2: the multi-line order core, with no new screens.**
  - ordered locks;
  - multi-line `parametros_checkout`, identical for one-line orders;
  - the payment screen lists every line;
  - the new Confirm token.

  It is still fed by the one-product screen. Every existing threaded test must pass, plus a new deadlock test.
- **Stage 3: the cart tables, services and screens.**
  - one pending order per customer;
  - cart limits;
  - allowlist entries for the new buttons.
- **Stage 4: the shop-settings row, the Telegram admin flag, the shop-owner permission group,** `.env.example`, and the reseller deployment and scheduling guide. The guide covers HTTPS hosting of the Django admin and the 5-minute cleanup schedule.
Stages 0–4 are this brief's own sequence and are separate from ROADMAP.md's Phase 0–5. Keep "abandoned order" (holds stock, cancelled after 24 hours) and "abandoned cart" (holds nothing) as separate terms (N1).
