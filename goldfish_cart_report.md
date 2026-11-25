# Goldfish Brainstorm Report: Multi-line cart and resellable single-shop package

**Mode:** BRAINSTORM
**Concept:** Turn the pt-PT Telegram sales and inventory bot into a resellable single-shop package. Phase 1 adds a cart, Phase 2 adds shop setup, Phase 3 adds an abandoned-cart reminder. Confirming the rights to sell is handled outside the code.
**Codebase:** `D:\Projects\telegram-sales-inventory-bot` (read-only)

## Reviewer Status

| Reviewer | Role | Status |
| --- | --- | --- |
| `egm-design` | Skeptical Architect Brainstormer | completed |
| `egm-codebase-details` | Codebase Accuracy Brainstormer | completed |
| `egm-zoom-out` | Simplicity and Scope Brainstormer | completed |
| `egm-comprehension` | Naive Reader Brainstormer | completed |
| `egm-implementation-readiness` | Implementation Readiness Brainstormer | completed |
| `egm-structural` | Structural Brainstormer | completed |
| `egm-inventive-catalyst` | Inventive Catalyst Brainstormer | completed |

All seven reviewers returned a report, and none needed a retry. Several reviewers did not read every governing document; their notes are under "Coverage gaps" at the end.

---

## 1. The concept's description of the code needs two corrections

These come from `egm-codebase-details` and were confirmed by `egm-design`, `egm-zoom-out` and `egm-implementation-readiness`.

- **Stripe parameters do not come from `linhas[0]`.** `parametros_checkout` calls `encomenda.linhas.get()` (`core/services.py:245`). Once an order has two lines, this raises `MultipleObjectsReturned`, so creating the payment link fails every time. It does not quietly use the first line. `linhas[0]` appears only in the text of the payment screen (`telegram_bot/handlers/customer.py:188`).
- **The Confirm token is 64 bytes only for the largest possible product id.** The fixed part takes about 45 bytes (`telegram_bot/confirmacao.py:3-6`, check at `:54`). Taking `produto_id` out frees about 19 bytes, which is enough for a short reference to the cart or a snapshot.

The other constraints are stated correctly: the single product-row lock (`services.py:193`), the idempotency keys (`:236-241`), full refunds (`:388-394`), the 24-hour abandoned-order command (`services.py:457-476`) and the threaded PostgreSQL tests (`core/tests.py:515, 799, 994`).

**Already able to handle several lines, so reuse them as they are:** `_devolver_stock` (`services.py:397-404`), `_reembolsar`, `cancelar_encomenda`, `cancelar_por_expiracao`, `confirmar_pagamento`, the order detail screen (`customer.py:119-123`), the admin inline (`core/admin.py:101-105`) and `Pagamento.valor = encomenda.total` (`services.py:303`).

---

## 2. Prioritised findings

### Blockers

#### B1. Who owns the code, and under what licence can it be sold
*Lenses: zoom-out, structural, comprehension, design, implementation-readiness, codebase-details, inventive-catalyst (all seven)*
- **The concept and the repository disagree.** The concept calls this the owner's "solo final project". The repository says something else:
  - `PROJECT_GUIDANCE.md` ("Release and attribution", about line 75) says team authorship, course sharing rules and public-release permission are unconfirmed. It also says to keep the GitHub repository private.
  - The README names a course GitLab project (p088).
  - No LICENSE file exists (`PROJECT_GUIDANCE.md:26`).
  - The demo product images are not licensed (`SECURITY.md:205`).
- **What to settle before any resale work starts:**
  - the university's intellectual-property rules for student final projects;
  - any claims from co-supervisors or teammates;
  - the licence model (one sale per client, or a reseller licence that covers many copies);
  - the notices that dependencies require, such as the LGPL notice for python-telegram-bot.
- **Recommendation:** make this Step 0. It decides whether the sale can happen at all, so it should come before the code, not run "outside the code" alongside it. `ROADMAP.md` lists replacing the images and choosing a licence as Phase 5 polish; they become release blockers.

#### B2. What price the customer is charged, and how the Confirm token refers to the cart
*Lenses: comprehension, design, codebase-details, implementation-readiness, zoom-out, structural, inventive-catalyst*
- **The concept contradicts itself.** "Prices are re-checked at checkout" and "the order snapshots what the customer confirmed" can conflict. A confirmation screen stays valid for 30 minutes (`confirmacao.py:18`). During that time the admin can change a price, or the customer can change the cart.
- **Every lens reached the same rule:** when Confirm is tapped, compare what the screen showed with the live data while the rows are locked. If anything differs (price, quantity, stock, or the product was deactivated), create no order. Redraw the screen with the changed lines marked. Never charge a price the customer did not see.
- **The lenses proposed three ways to carry the screen's state. Pick one:**

| Option | How it works | For | Against | Proposed by |
| --- | --- | --- | --- | --- |
| A. Stored snapshot table | When the screen is drawn, save the nonce, the user, the lines and prices (as JSON) and the time. The token becomes `confirmar_carrinho_<nonce>_<ts>`. | Proves exactly what was shown, and the order can be built from it directly. No product ids reach the client. | One new table, and one write every time the screen is drawn. | design, comprehension, codebase-details, implementation-readiness |
| B. Cart revision number plus `preco_confirmado` | The cart has a `revisao` integer that goes up on every change. Drawing the screen writes `preco_confirmado` on each cart line. The token carries `revisao` where `produto_id` is today. | No new table, and the token keeps its current layout. | Drawing the screen still writes to the database. Revision numbers are per cart, so the design must handle two screens from the same cart. | inventive-catalyst, structural |
| C. Digest in the token | Put a 16-hex hash of the sorted (product, quantity, price) values where `produto_id` is today. Recompute it under the locks when Confirm is tapped. | No new table, no write when the screen is drawn, and one check covers cart edits, price changes and stock shortfalls. | Cannot reproduce what was shown afterwards. The order is built from live prices, which are known to match the hash. | zoom-out |

  The synthesis leans towards **A**, which most lenses chose and which gives an audit trail of "why was I charged X". **C** is the smallest change if no audit trail is needed. In every option, the 64-byte check and the `ALLOWED_PREFIXES` allowlist in `security/sanitization.py:26-52` must change too.

#### B3. Locking several product rows can deadlock
*Lenses: design, codebase-details, zoom-out, structural, implementation-readiness, comprehension, inventive-catalyst*
- **The problem:** `reservar_encomenda` locks one product row today (`services.py:193`). A cart locks N rows, so two carts holding {A, B} and {B, A} will deadlock.
- **Requirement:**
  1. Lock every product in one `select_for_update().filter(pk__in=…).order_by('pk')` query.
  2. Check `stock >= quantity` for every line before writing anything. If any line falls short, return the full list of short lines and write nothing.
  3. Then decrement with `F()`.
- **Test:** a new threaded PostgreSQL test in which two carts lock overlapping products in opposite order.
- **The same ordered locking applies to** cancellation, expiry and the 24-hour command when they return stock for several lines.

#### B4. `parametros_checkout` and idempotency on orders that are already in progress
*Lenses: codebase-details, design, implementation-readiness, zoom-out, inventive-catalyst, comprehension*
- **Replace `linhas.get()`.** Build one Stripe `line_items` entry per order line, ordered by line pk, so the request is identical on every retry.
- **Do not break orders already in progress.** The keys `chave_primeira_tentativa` and `chave_substituicao` are reused across retries. Stripe rejects a reused key that arrives with different parameters. Two rules follow:
  - The rewritten function must produce byte-identical parameters for single-line orders that are pending when the new code is deployed.
  - Order lines must never change after the order is created. Enforce this, for example with read-only admin inlines.
- **Tests to extend:** s8, s14 and s16 (`core/tests.py:271, 338, 356`).
- **Round before converting to cents.** `int(preco_unitario * 100)` silently drops sub-cent fractions, so use `quantize` first (inventive-catalyst).
- **Stripe allows at most 100 line items per session.** Cap the cart well below that (see M6).

#### B5. What happens to the cart after Confirm, and two confirmation screens from one cart
*Lenses: comprehension, implementation-readiness, design, codebase-details, structural, inventive-catalyst*
- **The risk:** today two screens can create two orders (test db4, `core/tests.py:103`). With a cart, that means two pending orders holding the same stock.
- **Decisions to make:**
  - **Empty the cart in the same transaction that creates the order.** All lenses agree.
  - **Refuse a second screen** whose snapshot no longer matches the cart.
  - **Decide whether the cart comes back** when an unpaid order expires or is cancelled. The lenses disagree:
    - implementation-readiness: never restore it. This is the simplest option; say so in the bot's messages.
    - comprehension: add a "repeat this order" button on cancelled unpaid orders.
    - inventive-catalyst: an "Edit" button on a pending order with no payment session cancels it through the existing `cancelar_se_abandonada` lock path and copies its lines back into the cart. This needs a race test against an incoming payment.
- **Recommendation:** allow at most one pending order per user, and do not restore the cart automatically. "Repeat order" is the cheapest way to win the items back later.

### Major

#### M1. A customer can hold stock without paying
*Lenses: design, comprehension*
- **The problem:** quantity is limited only by stock, and stock is taken at Confirm (`services.py:212`). One user can confirm the whole stock and never pay. The stock then stays out for 24 hours: either until the abandoned-order command runs (no payment session) or until the Stripe session expires (24 hours by default).
- **Fixes:**
  - at most one pending order per user;
  - a short Stripe `expires_at`, for example 30 to 60 minutes. Derive it from `data_encomenda` so the parameters sent with a reused idempotency key stay the same;
  - optionally, a shorter abandoned-order window.
- **Document it either way:** stock is held from Confirm until payment, expiry or cancellation.

#### M2. Cart lines whose product is deactivated or deleted
*Lenses: design, codebase-details, comprehension, implementation-readiness, structural, inventive-catalyst*
- **Today:** products are fetched with `ativo=True` (`customer.py:226`), so a stale cart line raises `DoesNotExist` instead of showing "removed".
- **Fix:** treat inactive products like sold-out lines. Flag them and block checkout.
- **Deletion rules:** cart lines use `on_delete=CASCADE`. Order lines stay `PROTECT` (`models.py:131`).
- **Constraints:**
  - unique (cart, product), so a double tap on "add" does not create two lines;
  - a CHECK that `quantidade > 0`;
  - `F()` increments for the plus and minus buttons.

#### M3. Moving categories to a `Categoria` table reaches more code than it looks
*Lenses: all seven*
- **Places that hard-code categories today:**
  - `CATEGORIA_CHOICES` (`core/models.py:35-48`)
  - `telegram_bot/constants.py:3-12`
  - `customer.py:34-37` and `:241` (`cat_<slug>` callbacks)
  - `keyboards.py:37, 56, 84`
  - `handlers/admin.py:27`
  - `services.py:58-61, 69-72`: `order_by('categoria')` would silently start sorting by foreign-key id
  - `ProdutoAdmin.list_filter` (`core/admin.py:41-45`)
  - the `cat_` entry in the `security/sanitization.py` allowlist
  - tests that use `categoria="ssd"` (`telegram_bot/tests.py:165, 292, 364, 385`; `core/tests.py:38`)
- **Migration plan:** three steps. Add a nullable foreign key, run a data migration, then drop the old column. Create rows only for the categories that products actually use, so a fresh install has no PC categories.
- **Model:** `on_delete=PROTECT`, an `ativo` flag instead of deletion, and an ordering field.
- **Open decision: should callbacks use the id or a slug?**
  - Slug (implementation-readiness): old `cat_ssd` buttons in customers' chats keep working.
  - Id (zoom-out): shorter and simpler.
  - Either way, old buttons must fail gracefully. The synthesis leans to a unique slug.
- **Rollback:** reversing the migration only works while every slug still matches an old code. Once admins add categories, treat rollback as forward-fix only.
- **Upgrades:** reseller copies upgrade by running migrations, so the migration must be safe on a live shop's data.

#### M4. The shop-settings record
*Lenses: design, structural, comprehension, implementation-readiness, zoom-out, inventive-catalyst, codebase-details*
- **Make it a true single row:** fix `pk=1` and use `get_or_create`, and block add and delete in the admin.
- **Defaults:** the bot must start with sensible defaults when the row is empty.
- **Hard-coded text it replaces:**
  - the shop name "Jarvis" (`commands.py:32`, `handlers/admin.py:39`);
  - the welcome text (`commands.py:18-20`);
  - "PC shop" wording in `constants.py` and the README.
- **Welcome image:** process it through the SEC-11 upload path (`models.py:57-82`), and cache the Telegram `file_id` after the first send.
- **Scope challenge from zoom-out:** drop the welcome image unless a buyer asks for it, since BotFather already sets the bot's profile photo.

#### M5. Which "admin"?
*Lens: comprehension*
- There are two admin surfaces: `ADMIN_TELEGRAM_IDS`, an environment variable only the reseller can change, and Django staff.
- Say which one the shop owner uses and which one the reseller keeps.

#### M6. Limits on the cart and on messages
*Lenses: design, implementation-readiness, structural, zoom-out*
- Stripe allows 100 line items per session.
- Telegram allows 4096 characters per message and roughly 100 buttons per keyboard.
- Cap the cart at about 20 lines and set a maximum quantity per line. Validate quantity as a bounded integer, the way `sanitizar_delta_stock` does.

#### M7. New callbacks must be added to the allowlist
*Lenses: implementation-readiness, codebase-details*
- Add every new cart callback (add, plus, minus, remove, confirm) to both `ALLOWED_PREFIXES` (`security/sanitization.py:35`) and `PREFIX_ROUTES` (`customer.py:274`). This stops the SEC-12 drift from happening again.
- **Old Confirm buttons in chat history:**
  - Keep parsing the old three-part format, but only to look up an existing order through `chave_confirmacao` ("existing order always wins", `customer.py:211-219`).
  - Otherwise show the "expired" screen, following the existing `antigo` path (`customer.py:202`).

#### M8. Rolling back the code is unsafe once multi-line orders exist
*Lens: implementation-readiness*
- **The problem:** older code crashes in `linhas.get()` on any multi-line order.
- **Choose one:**
  - declare Phase 1 forward-only; or
  - ship behind a setting that limits carts to one line until the release is proven.
- The cart tables are additive, so rolling back the schema is harmless.

#### M9. Audit records and visibility
*Lens: codebase-details*
- `CRIAR_ENCOMENDA` (`services.py:217-220`) names one product. Record every line (product, quantity, unit price).
- Add an audit or log line when checkout is blocked by a price change or missing stock.
- Customer notifications through `_enviar_telegram` (`views.py:88-109`) are sent once with no retry. The SEC-05 outbox idea was never built, and reminders would inherit this.
- The reminder command should print sent, skipped and failed counts, as `cancelar_encomendas_abandonadas.py:26` does.

#### M10. The abandoned-cart reminder (Phase 3)
*Lenses: all seven. Zoom-out challenges whether it is needed at all.*
- **Decisions the brief must make:**
  - where consent is stored: a `Utilizador` field plus a timestamp;
  - how the customer opts out from inside the bot;
  - what "abandoned" means (cart idle for N hours);
  - at most one reminder per cart (a `lembrete_enviado_em` marker);
  - when Telegram returns 403 (the user blocked the bot), turn the opt-in off;
  - skip users marked `ativo=False`;
  - a global send rate of about 30 messages per second or less;
  - the message must not imply that stock is held;
  - how long idle carts are kept (the retention bullet in `PROJECT_GUIDANCE.md:58`).
- **Legal:** these are marketing messages to EU customers, so they fall under ePrivacy and GDPR consent rules.
- **How to schedule it. The lenses disagree:**
  - A cron management command, the existing pattern. Recommended by inventive-catalyst, structural and design. It runs as a second Bot API sender outside the polling process.
  - python-telegram-bot's JobQueue. Raised by comprehension. It needs nothing new scheduled, but requires the `[job-queue]` extra, and jobs are lost while the bot is down.
  - Drop it until a client asks (zoom-out, YAGNI). The cart table already holds everything it would need.
- **Recommendation:** build it last, as a cron command run on the same schedule as `cancelar_encomendas_abandonadas`, and only once there is demand.

#### M11. Portuguese legal and tax requirements are missing
*Lenses: zoom-out, comprehension*
- **What a pt-PT business-to-consumer shop is normally required to have:**
  - invoices from AT-certified billing software (Stripe receipts do not count);
  - prices that include VAT;
  - terms and conditions;
  - the 14-day right of withdrawal;
  - a privacy notice;
  - a GDPR controller and processor arrangement between the reseller and each client.
- **Decision:** state in the sale terms that invoicing stays outside the bot.
- **Unverified (comprehension):** the order states include "enviada" and "entregue", but no reviewer found address or shipping-cost fields. Decide whether the cart total includes shipping.

#### M12. Running one copy per shop costs operations work
*Lens: design*
- Every client needs its own PostgreSQL database, polling process, Stripe webhook (with public HTTPS and its own secret), cron entries, and migration run on every release.
- **Requirement:**
  - one checklist of environment variables (`.env.example`);
  - one deployment recipe;
  - a startup check that fails fast when the Stripe or webhook configuration is missing.
- **Before selling** (zoom-out): run at least one full purchase, refund and expiry on Stripe test mode with a real webhook. `PROJECT_GUIDANCE.md` still calls this an unvalidated portfolio project, and SEC-01 (HTTPS, cookie and HSTS settings) is still open.

### Nits

- **N1. "Abandoned" names two different things.** Abandoned orders hold stock and are cancelled after 24 hours. Abandoned carts hold no stock and only get a reminder. Give them distinct names in code, commands and docs (structural).
- **N2. "Checkout" names two different things.** Use three terms consistently: cart (carrinho), confirmation screen, payment page (comprehension).
- **N3. Phase numbers collide with `ROADMAP.md` Phases 0 to 5.** Rename them, for example R1 Cart, R2 Shop setup, R3 Reminder (structural).
- **N4. The documentation is out of date.** `ARCHITECTURE.md` still says other services are "not yet transactional", although SEC-10 is done. Its line 68 note about one-line orders needs rewriting (zoom-out, structural).
- **N5. Compute the total from the lines.** The order total must be the sum of the line subtotals, computed inside the locked transaction (design).
- **N6. Keep a single checkout path.** "Buy now" can become a one-line cart through the same `reservar_encomenda(lines)`, so there is one code path and one set of threaded tests (design, codebase-details).
- **N7. State that refunds are all or nothing.** There is no cancellation of single lines and no partial refund. Write this down, including in the admin help text, so nobody adds them later (design, structural, comprehension, implementation-readiness).

---

## 3. Contradictions and how they resolve

| Apparent contradiction | Resolution |
| --- | --- |
| No reservation when adding to the cart, but all-or-nothing checkout | No real conflict. The cart is a draft and is never reserved. The order is frozen and is reserved inside the Confirm transaction with ordered locks. The code already reserves at order creation. |
| The 64-byte callback limit, but orders with several lines | Keep the screen's state on the server and send only a pointer: a snapshot nonce, a revision number or a digest. See B2. |
| Stable idempotency keys, but a cart that keeps changing | The keys depend on the order lines, which are frozen, and never on the cart. This holds as long as order lines cannot change after creation. |
| Prices re-checked at checkout, but the order "snapshots what the customer confirmed" | Compare under the locks and redraw if anything differs. The order equals the confirmed screen or no order is created. |
| "Resellable", but `PROJECT_GUIDANCE.md` calls it an "unvalidated portfolio project" | Ownership (B1) and a live Stripe test run (M12) come before the sale. |
| Emptying the cart at Confirm, but not losing the items if payment fails | One pending order per user and no automatic restore. Optionally add "repeat order" or "edit cancels and restores" (B5). |

---

## 4. Alternatives considered

- **Use a pending `Encomenda` (`estado='carrinho'`) as the cart.** Rejected by design, zoom-out and structural. It would leak into the abandoned-order cleanup, the admin actions (`core/admin.py:117`) and every SEC lock path. Use separate cart tables instead. Zoom-out suggests a single `ItemCarrinho` table (user, product, quantity); a `Carrinho` parent is only needed if you choose option B in B2.
- **Reserve stock when adding to the cart.** Rejected. It needs cleanup on a timer and makes stock hoarding worse.
- **A quantity picker on the existing one-product order instead of a cart.** Zoom-out proposes this as step 1a: change `quantidade=1`, the stock check and the Stripe quantity, with no cart and no change to locking. Build the full cart only if buyers need orders with several products.
- **Put the shop name in an environment variable** instead of the settings row (zoom-out). This is a valid simplification if the welcome text and image are also dropped.
- **JobQueue or cron for reminders.** See M10.

---

## 5. Staging advice

All lenses agree on this order: settle the rights first, do shop setup before the cart, build the multi-line core before the cart screens, and leave reminders for last.

0. **Documentation and decisions only.**
   - Rights, licence and asset licensing; invoicing and the legal position (B1, M11).
   - Record the cart rules (B2, B5, M1, M6) in `PROJECT_GUIDANCE.md` and remove the questions they answer.
   - Add the R-track to `ROADMAP.md`.
   - Run a live Stripe test-mode pass of the current flow.
1. **R2a: the Categoria table** with its three-step migration and updated `cat_` routes. This is low risk and removes the hard-coded category callbacks before the cart adds more callbacks to the same keyboards.
2. **R1a: the multi-line order core, with no new UI.**
   - `reservar_encomenda(lines)` with ordered locks;
   - multi-line `parametros_checkout` that produces identical parameters for single-line orders;
   - a payment screen that lists every line;
   - the new Confirm token and snapshot.

   It is still fed by the existing one-product screen. All existing SEC threaded tests must pass, plus the new deadlock test.
3. **R1b: the cart tables, service functions and bot screens.**
   - one pending order per user;
   - a short `expires_at`;
   - cart limits;
   - allowlist entries for the new callbacks.

   Optionally ship behind a one-line limit first (M8).
4. **R2b: the shop-settings row, `.env.example`, and a reseller deployment and scheduling guide** (M12).
5. **R3: reminders**, only if a client wants them.

Update the documentation alongside the code:
- `ARCHITECTURE.md`: the order flow, the new tables and the scheduled commands.
- `SECURITY.md`: a new entry covering cart abuse limits and the new token.
- `README.md`: the feature list and the reseller guide.

---

## 6. Ideal Final Result

- **Data model.** The cart is a draft: rows of (product, quantity). The confirmation screen is a stored, numbered snapshot of that draft. The order is a frozen copy of the snapshot the customer confirmed.
- **Confirm.** A single `reservar_encomenda` transaction does all of the following:
  - locks every product in pk order;
  - compares price, stock and active status against the snapshot;
  - either writes N lines, N stock movements, one audit row and the total, and empties the cart, or redraws the screen showing what changed and why.
- **After Confirm, nothing changes.** The Stripe session, the webhook, cancellation, refund, the 24-hour cleanup and the idempotency keys already work for orders with several lines and never touch the cart.
- **The customer** taps Confirm once and pays exactly what they saw.
- **The owner** edits the shop name, welcome text and categories in Django admin.
- **The reseller** deploys each shop with one `.env` file, one `migrate` run, one cron file and one settings record, with no code edits. Ownership, licensing and invoicing are settled in writing.

---

## Coverage gaps (what reviewers did not read)

- **design:** did not read `ARCHITECTURE.md`, `SECURITY.md`, `PROJECT_GUIDANCE.md`, the webhook view or the tests.
- **codebase-details:** read `ARCHITECTURE.md`, `PROJECT_GUIDANCE.md` and the test bodies only through keyword searches.
- **comprehension:** did not read `SECURITY.md`, `PROJECT_GUIDANCE.md` or the handlers. Its shipping-fields point is unverified.
- **structural:** did not read the services or the Stripe code.
- **zoom-out:** did not read `core/views.py`, so it did not check whether the webhook verifies the amount paid. It also did not read the tests.
- **implementation-readiness:** did not read the migration contents or the text in `constants.py`.
- **inventive-catalyst:** did not check its "one pending order per user" and "edit cancels" proposals against the admin status flows.

The two failures that would show up only in production are deadlocks between product locks (B3) and Stripe rejecting a reused idempotency key on orders in progress at deploy time (B4).
