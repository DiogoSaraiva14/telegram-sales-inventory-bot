# Multi-product cart and resellable single-shop setup

*   **Author:** Jose Diogo Pereira Saraiva
*   **Status:** Draft
*   **Date:** 2026-11-24
*   **Predecessor:** [concepts_brief_cart.md](concepts_brief_cart.md) (12 decisions settled), with the Goldfish review in [goldfish_cart_report.md](goldfish_cart_report.md).

--------------------------------------------------------------------------------

## Overview

The bot is a Portuguese (pt-PT) Telegram shop built on Django, PostgreSQL, python-telegram-bot and Stripe Checkout. Today it sells **one product per order**, and its shop identity is **hard-coded**: the name "Jarvis", a PC-parts welcome text, and 8 fixed PC categories.

This design changes it into a package that can be sold, with its rights, to a reseller. The reseller installs **one separate copy per client shop** and configures each copy without editing code. It covers four code stages, in this order:

1.  **Categories in the database.** A `Categoria` table replaces the 8 fixed categories.
2.  **Multi-line orders.** Order creation, Stripe payment links, stock locking and stock return work for orders with several lines. Customers still see today's one-product screens.
3.  **The cart.** A cart stored in the database, a confirmation screen saved as a snapshot that never changes, a 60-minute payment window, and "Repeat order".
4.  **Shop setup and handover.** A shop-settings record (name and welcome text), Telegram admin rights stored in the database, a permission group for shop owners in Django admin, and a reseller deployment guide.

A non-code **Stage 0** comes first and can stop the plan: confirming who owns the code, choosing a licence, clearing the demo images, settling the invoicing position, and one full payment, refund and expiry run in Stripe test mode with a real webhook.

## The Problem

**For the owner (seller of the rights).** A reseller buys code they can deploy for many clients. Today the code can't be handed over as a product:

*   It sells one product per order. No shop owner would accept that as a webshop, and it weakens the portfolio story.
*   Every shop would need code edits for its name, welcome text and categories.
*   Telegram admins are a list in `.env`, so changing who can manage the shop means editing the server's configuration and restarting the bot.

**For the shop's customers.** Buying three products means three separate orders, three payment links and three payments.

**For the shop owner.** They can't rename the shop, add a category, or give a member of staff the admin panel without the reseller's help. There is no limited Django account to manage the catalogue on their own.

**Why not just add a cart quickly.** The checkout path was hardened in SEC-02 to SEC-13, and it assumes one product per order:

*   the Confirm button carries one product id;
*   `reservar_encomenda` locks one product row;
*   `parametros_checkout` calls `encomenda.linhas.get()`, which fails on any order with two lines;
*   `_devolver_stock` loops over order lines in no fixed order.

Locking several products in no fixed order can deadlock two checkouts that share products. Changing the Stripe request breaks payment retries for orders pending at deploy. With quantities limited only by stock, one customer could hold all of a scarce product for up to 24 hours. A cart has to keep the guarantees that work made, not undo them.

## Goals

*   **G1, a cart that survives restarts.** It is stored in the database, with up to 20 lines and a quantity per line of at most the available stock and a fixed per-line maximum. It has plus, minus and remove buttons. Inactive or short-stocked products are flagged and block checkout.
*   **G2, the customer pays exactly what they confirmed.** The confirmation screen shows current prices, and marks lines whose price changed since they were added. Drawing it saves a snapshot that never changes. Confirm compares the snapshot with live data while the rows are locked: if anything differs, no order is created and the screen is redrawn.
*   **G3, one order per snapshot and one pending order per customer.** Both hold under concurrent taps and are tested with threads. Confirming an old screen never removes cart items added later.
*   **G4, no deadlocks.** Every code path that changes stock for several products locks those products in product-id order. Two threaded tests prove it: two checkouts that share products, and a checkout racing a cancellation.
*   **G5, a hard 60-minute stock hold.**
    *   An order with a payment link holds stock at most 60 minutes after Confirm.
    *   An order that never gets a link holds it at most 65 minutes, because the cleanup runs every 5 minutes.
    *   Payment links are created or replaced only while the order is at most 28 minutes old. Past that cutoff, the order is cancelled.
*   **G6, "Repeat order"** on cancelled unpaid orders. It merges the old lines into the current cart, with quantities limited to the stock and per-line maximum, and says what was reduced or left out.
*   **G7, multi-line orders keep every existing guarantee:** stable idempotency keys within an attempt, webhook confirmation under locks, full refunds, expiry cancellation, and the abandoned-order cleanup. All existing SEC tests still pass.
*   **G8, shop setup without code edits.** The reseller, or the owner with a limited Django account, sets the shop name, welcome text and categories in Django admin. Category buttons use database ids. Old or inactive category buttons show an "expired menu" message instead of an error.
*   **G9, safe admin handover.**
    *   Telegram admin rights are a database flag that only the reseller's superuser can grant or remove.
    *   The `.env` list stays as the first admin, so a fresh install isn't locked out.
    *   Changes take effect without restarting the bot.
    *   The shop owner's Django account can edit products, categories and shop settings, and can only read orders and payments.
*   **G10, a repeatable deployment.** One `.env`, one `migrate` run, one cron file (the cleanup command and the snapshot and cart cleanup, every 5 minutes) and one settings record per shop. The guide covers HTTPS for the Django admin and backups before each migration. A check before startup lists missing configuration.
*   **G11, clean data retention.**
    *   Snapshots that never became an order are deleted after 45 minutes.
    *   Snapshots linked to an order are never deleted by the cleanup.
    *   Carts are deleted after 30 days without activity. Activity means adding, changing or removing a line, including through "Repeat order"; viewing doesn't count.
*   **G12, a safe switch-over.** Pending orders from before the cart, which have no snapshot, are cancelled before the new code runs. Payments already in progress are left to the webhook. The bot refuses to start while any such order remains, and never blocks on orders the new code created.
*   **G13, success measure.** One complete demo on its own database, in Stripe test mode with a real webhook: add several products, change quantities, see a price change marked, confirm, pay, then cancel with a refund, and let a second order expire. The demo ends with stock, orders, payments and audit records consistent.

## Non-Goals

*   One deployment serving several shops. Each shop gets its own copy, database, bot and Stripe account.
*   A PC part picker or compatibility checks.
*   Other languages, currencies or payment providers. The package stays pt-PT, EUR and Stripe, card payments only.
*   Reserving stock when an item is added to the cart. Stock is taken only at Confirm.
*   Partial refunds, or cancelling single lines of an order.
*   Invoicing, tax documents, shipping costs, withdrawal handling, and legal texts such as terms and a privacy notice. Each shop arranges these itself, and the reseller contract says so.
*   Email collection, discounts and coupons.
*   Abandoned-cart reminders. They're kept as a future idea, and the cart data can support them later.
*   A welcome image or any in-bot branding beyond the shop name and welcome text. The bot's profile photo is set per shop in BotFather.
*   A guided setup inside the bot. Shops are set up in Django admin.
*   A one-line-per-cart release setting or a code rollback path. The release only moves forward, with database backups.
*   A different payment window per shop. It is fixed at 60 minutes.
*   Keeping old catalogue buttons working. They show the expired-menu message.

## Open Questions

*   **Q1, Stage 0 outcome.** The ownership and course-rights check, the licence choice and the image rights aren't settled yet. If any of them fails, the reseller goal stops and the design falls back to a portfolio demo.
*   **Q2, per-line maximum quantity.** Set in the Technical Plan (Stage 3): 99 per line and 20 lines per cart. Change it there if a different value is wanted.

## Critical User Journeys (CUJs)

### CUJ 1: A customer buys several products in one order

*   **Given**: products A (stock 3) and B (stock 1) are active, and the customer has no pending order.
*   **When**: the customer adds 2 of A and 1 of B to the cart, opens the confirmation screen, and taps Confirm within 30 minutes.
*   **Then**: one order with two lines is created in one transaction, stock becomes A=1 and B=0, the cart is empty, and the payment screen lists both lines with one Stripe link that expires 60 minutes after the order. Paying confirms the order through the existing webhook.

### CUJ 2: Something changed between the screen and Confirm

*   **Given**: the customer's confirmation screen shows A at 49.99 €. Before they tap Confirm, the admin changes A to 54.99 € and another customer buys the last unit of B.
*   **When**: the customer taps Confirm.
*   **Then**: no order is created and no stock changes. The screen is redrawn showing A's new price and B as out of stock, and checkout stays blocked until B is removed.

### CUJ 3: An unpaid order expires and the customer repeats it

*   **Given**: the customer confirmed an order but didn't pay within 60 minutes.
*   **When**: Stripe reports the link expired, and later the customer taps "Repeat order" on the cancelled order while their cart holds other items.
*   **Then**: the order is cancelled and its stock returned when it expires. Later, its lines are merged into the cart at current prices, quantities are reduced to fit stock and the per-line maximum, the customer is told what changed, and nothing is ordered until they confirm again.

### CUJ 4: A reseller sets up a new shop

*   **Given**: a fresh server, a Telegram bot token from BotFather, and Stripe keys.
*   **When**: the reseller fills `.env`, runs `migrate`, installs the cron file, creates the superuser, and in Django admin enters the shop name, welcome text and categories, then creates a limited account for the shop owner and marks the owner's Telegram user as admin.
*   **Then**: `/start` shows the shop's own name and welcome text, the catalogue shows its categories, the owner can manage orders and stock from the Telegram panel and the catalogue from Django admin, and the owner can't grant admin rights or see staff accounts.

### CUJ 5: Switching an existing copy to the cart version

*   **Given**: a running copy with pending one-product orders from before the cart, one of them already paid in Stripe but not yet confirmed by the webhook.
*   **When**: the operator takes a backup, runs the migrations and the drain command, then starts the new bot.
*   **Then**: unpaid legacy orders are cancelled, with their links expired and stock returned. The paid one is skipped and listed, and the bot refuses to start until the webhook confirms it. Once it does, the bot starts. Pending orders created by the new code never block a start.

## Technical Plan (Architecture)

### Principles

*   **One shop per copy.** No shop id on any table. Each copy has its own database, bot token, Stripe account, polling process and cron file. This keeps SEC-09's one-process rule.
*   **Draft, snapshot, order.**
    *   The **cart** is a draft and reserves nothing.
    *   The **confirmation snapshot** records what the screen showed and never changes.
    *   The **order** is created from one snapshot, in one locked transaction.
    *   Everything after order creation keeps its current logic. Only `parametros_checkout`, the payment screen and `_devolver_stock` change.
*   **Lock rules (no deadlocks).**
    *   **Checkout:** snapshot, then cart, then customer (`Utilizador`), then products. It then creates a new order, which no other transaction can be waiting on.
    *   **Cancellation, expiry and the cleanup commands:** order, then payment (as today), then the order's products.
    *   **Products are always locked in one query ordered by id.** That is the only place two transactions can wait on each other's rows, and a single ascending order removes the cycle.
    *   **No path waits on an existing order's row while holding product locks.** Checkout never touches an existing order, so "order then products" in cancellation can't form a cycle with "products then new order" in checkout.
*   **The payment window is measured from the order:** `data_encomenda` + 60 minutes, with sessions created only while the order is at most 28 minutes old.

### Data flow

```text
 Product screen ──"Adicionar ao carrinho"──► Carrinho + LinhaCarrinho  (draft, revision++)
                                                   │
                                   "Ver carrinho" / "Finalizar"
                                                   ▼
                              build snapshot: lock nothing, read live data
                              ConfirmacaoCarrinho(nonce, user, revision, lines JSON, total)
                                                   │  screen shows lines, prices, ⚠ changes
                               Confirm button: confirmar_carrinho_<nonce>_<ts>
                                                   ▼
  ┌──────────────── one transaction: criar_encomenda_de_confirmacao ────────────────┐
  │ lock snapshot → existing order for it? return it ("existente")                  │
  │ check owner, age ≤ 30 min        → else "expirada"                              │
  │ lock cart → revision == snapshot revision?        → else "alterado" (redraw)     │
  │ lock user → another pending order?                → else "pendente" (show it)    │
  │ lock products by id → price, active, stock match? → else "alterado" (redraw)     │
  │ create Encomenda (+ link to snapshot), N lines, N stock movements (F()),         │
  │ one audit row listing every line; delete cart lines; revision++                  │
  └──────────────────────────────────────────────────────────────────────────────────┘
                                                   │ commit
                                                   ▼
            garantir_sessao_pagamento (unchanged shape; Stripe never called under a lock)
               order age ≤ 28 min → Session.create(line per order line, expires_at)
               order age > 28 min, no session  → cancelar_encomenda → "expirada"
               stored session expired           → cancelar_encomenda → "expirada"
                                                   │
                         Stripe webhook (unchanged): completed → confirmar_pagamento
                                                     expired   → cancelar_por_expiracao
                                                   │
                   cancelled & unpaid → "Repeat order" merges lines back into the cart
```

### Stage 1: categories in the database

*   **New model `Categoria`:** `nome` (≤ 60 characters), `ordem` (integer, for display order), `ativo`. Products refer to it with `on_delete=PROTECT`. Categories are deactivated, never deleted, and ids are never reused.
*   **Migration in three steps:**
    1.  Add `Categoria` and a nullable `Produto.categoria_nova` foreign key.
    2.  A data migration creates one category for each code products actually use, named with today's labels ("Processadores", and so on, from `CATEGORIA_CHOICES`), and points each product at it.
    3.  Remove the old `categoria` text field and rename `categoria_nova` to `categoria`, now required.

    Reversing is forward-fix only: the reverse step raises an error with a message instead of guessing old codes.
*   **The bot:**
    *   The catalogue lists active categories ordered by `ordem`, then name, and buttons carry `cat_<id>`.
    *   `CATEGORIAS` in `telegram_bot/constants.py` is removed.
    *   The `cat_` prefix stays on the allowlist. The handler reads the value as an id. If it isn't a number, or isn't an active category, the handler shows the expired-menu message with a "reopen catalogue" button instead of the generic "invalid request" alert. That covers old `cat_ssd` buttons.
*   **Admin:** `Categoria` is registered in Django admin, with list ordering, an active filter and no delete. `ProdutoAdmin` filters by category, and the admin product screen shows the category name.

### Stage 2: multi-line order core (no new screens yet)

*   **Snapshot model `ConfirmacaoCarrinho`:**
    *   Fields:
        *   `nonce`: 16 hex characters, unique;
        *   `utilizador`: foreign key;
        *   `criada_em`;
        *   `revisao_carrinho`: integer, null when the screen didn't come from the cart;
        *   `moeda`: always "eur";
        *   `total`: decimal;
        *   `linhas`: JSON list of `{produto_id, nome, quantidade, preco_unitario}`.
    *   **JSON, not a child table:** the lines are written once, never changed, and always read together, so a separate table would add joins without adding any guarantee.
    *   Rows are never updated after insert. The model refuses `save()` on an existing row, and the admin shows them read-only.
*   **The order link:** `Encomenda.confirmacao` is a one-to-one, nullable link to the snapshot. The order also stores the snapshot's nonce in the existing `chave_confirmacao` field, so `chave_primeira_tentativa` keeps its current format. "Legacy order" means a pending order whose `confirmacao` is null.
*   **One pending order per customer:** a partial unique constraint on `Encomenda(utilizador)`, applied only when `estado='pendente'` and `confirmacao` is set. Legacy rows are excluded, so the migration can't fail on old data.
*   **Order creation:** `criar_encomenda_de_confirmacao(telegram_user, nonce, emitida_em)` replaces `reservar_encomenda`. It runs the transaction in the diagram above and returns one of:
    *   `"criada"`;
    *   `"existente"`: this snapshot already has an order;
    *   `"de_outro_utilizador"`;
    *   `"expirada"`;
    *   `"pendente"`: the customer has another pending order, which is returned;
    *   `"alterado"`: a new snapshot is built and returned, with the changed lines marked.

    Stock is checked and decremented for every line only after all products are locked. Nothing is written if any line falls short.
*   **The one-product screen in this stage:** "Encomendar" builds a one-line snapshot with `revisao_carrinho` null, and the cart check is skipped. Stage 3 replaces this button.
*   **Confirm token:** the format becomes `confirmar_carrinho_<nonce>_<ts>`, at most 44 bytes. `telegram_bot/confirmacao.py` keeps the 30-minute expiry check and still reads old `confirmar_encomenda_<nonce>_<ts>_<id>` buttons, but only to find an existing order by `chave_confirmacao` ("existing order always wins"). Otherwise an old button shows the expired screen.
*   **Stripe parameters:** `parametros_checkout(encomenda)` builds:
    *   one `line_items` entry per order line, ordered by line id;
    *   `unit_amount` = the unit price, rounded half-up to cents with `quantize`, then converted to an integer;
    *   `expires_at` = `data_encomenda` + 60 minutes, as a Unix timestamp.

    Everything comes from stored data, so retries with one key send identical parameters. Orders are capped at 20 lines, well under Stripe's 100.
*   **Session cutoff in `garantir_sessao_pagamento`:**
    *   Before creating a first session, if the order is more than 28 minutes old, no session is created. The order is cancelled through `cancelar_encomenda` and the call returns `"expirada"`.
    *   When the stored session is `expired`, it calls `cancelar_encomenda`, which sees the expired session and cancels with stock returned, instead of creating a replacement (D19). This also covers a webhook that never arrives.
    *   The function never calls Stripe while holding a lock, as today.
*   **Stock return:** `_devolver_stock` locks the order's products in one query ordered by id, then applies the `F()` increments. Cancellation, expiry and the abandoned-order command all call it.
*   **Payment screen:** it lists every line as `quantity × name — subtotal`, with names escaped for Markdown, then the total.
*   **Audit:** `CRIAR_ENCOMENDA` lists every line. A blocked Confirm (`"alterado"`) writes a log line, not an audit row: it isn't a change to stored data.

### Stage 3: the cart

*   **Models:**
    *   **`Carrinho`:** one per customer (one-to-one with `Utilizador`), with `revisao` (integer, increased on every change) and `atualizado_em` (last activity).
    *   **`LinhaCarrinho`:** `carrinho`; `produto` (`on_delete=CASCADE`); `quantidade`; and `preco_ao_adicionar`, the price when the line was added or last changed, used to mark price changes.
    *   **Constraints:** one line per product per cart, and `quantidade > 0`.
*   **Limits (set here, Open Question Q2):**
    *   20 lines per cart;
    *   99 per line;
    *   a line's quantity never above the product's current stock when it is changed.

    The plus button stops at whichever limit comes first and says which.
*   **Services, each in one transaction that locks the cart row:**
    *   `adicionar_ao_carrinho(user, produto_id)`: adds 1, or creates the line;
    *   `alterar_quantidade(user, produto_id, delta)`;
    *   `remover_do_carrinho(user, produto_id)`;
    *   `ver_carrinho(user)`: no lock. It returns the lines with live price, stock and active status, and flags for "price changed", "insufficient stock" and "inactive".
    *   `preparar_confirmacao(user)`: builds a snapshot from the cart. It refuses while any line is flagged insufficient or inactive, and returns the flagged lines.
    *   `repetir_encomenda(user, encomenda_id)`: only for the customer's own cancelled order with no payment marked `pago` or `reembolsado`. It merges the lines and returns what was reduced or skipped.

    Every change increases `revisao` and sets `atualizado_em`. Viewing changes neither.
*   **Screens and buttons:**
    *   The product screen gets "🛒 Adicionar ao carrinho", which replaces "Encomendar".
    *   The main menu gets "🛒 Carrinho (N)".
    *   The cart screen has + / − / 🗑 for each line and "Finalizar compra".
    *   The confirmation screen has "Confirmar" and "Voltar ao carrinho".
    *   Cancelled unpaid orders show "🔁 Repetir encomenda" in the order details.
    *   **New callback prefixes**, each added to both `ALLOWED_PREFIXES` and `PREFIX_ROUTES`:
        *   `carrinho_add_<produto_id>`
        *   `carrinho_mais_<produto_id>`
        *   `carrinho_menos_<produto_id>`
        *   `carrinho_remover_<produto_id>`
        *   `confirmar_carrinho_<nonce>_<ts>`
        *   `repetir_encomenda_<id>`
    *   **New exact routes:** `carrinho_ver` and `carrinho_finalizar`.
*   **Messages:** the cart and confirmation texts stay under Telegram's 4096-character limit at 20 lines with 200-character names. Names are cut to 60 characters on screen, and the snapshot keeps the full name.

### Stage 4: shop setup and handover

*   **`Loja` (shop settings):** a single row with `pk=1`, holding `nome` (≤ 80 characters) and `texto_boas_vindas` (≤ 1000 characters).
    *   `obter_loja()` uses `get_or_create`, defaulting to "Loja" and a neutral welcome text, so an empty database still starts.
    *   The admin blocks add and delete.
    *   `/start` shows the name and text, with the name escaped for Markdown, and the admin panel title uses the name instead of "Jarvis".
*   **Telegram admins:** `Utilizador.e_admin` (boolean).
    *   `e_admin(user_id)` is async and returns true when the id is in the bootstrap set from `ADMIN_TELEGRAM_IDS` or `e_admin` is set in the database. It queries on every check, so changes take effect immediately.
    *   It replaces both `guards.is_admin` and the unused `security.decorators.admin_only` list.
    *   `teclado_menu_principal` receives the admin flag as a parameter instead of calling the check itself.
    *   In Django admin, `e_admin` is read-only unless the user is a superuser.
*   **Shop-owner permissions:** a data migration creates the group "Dono da loja":
    *   change and view: `Produto`, `Categoria`, `Loja`;
    *   view only: `Encomenda`, `LinhaEncomenda`, `Pagamento`;
    *   nothing on `Utilizador`, `MovimentoStock`, `RegistoAuditoria`, `ConfirmacaoCarrinho`, `Carrinho` or Django's users and groups.

    The reseller creates the owner's staff account and adds it to this group.
*   **Remaining SEC-01 settings:** HTTPS redirect, secure cookies, HSTS and `CSRF_TRUSTED_ORIGINS`. They're read from the environment and switched on when `DJANGO_HTTPS=True`, so local development is unaffected.

### Scheduled jobs and switch-over (spans Stages 2–4)

*   **Cron file (every 5 minutes):**
    *   `cancelar_encomendas_abandonadas --horas 1`: existing, re-run every 5 minutes;
    *   `limpar_rascunhos`: new. It deletes snapshots older than 45 minutes with no order, and carts whose `atualizado_em` is more than 30 days ago. Snapshots linked to an order are never deleted.
*   **`drenar_encomendas_antigas`:** a new, one-off command. It runs each legacy pending order through `cancelar_encomenda`. Orders refused with `PagamentoRecebido` or a Stripe error are skipped and listed. Run it again after the webhook settles them.
*   **Startup checks:** `python bot.py` refuses to start while any legacy pending order exists, and lists their ids. It also refuses when required settings are missing (existing check).
*   **Deploy order for an existing copy:**
    1.  database backup;
    2.  stop the bot;
    3.  `migrate`;
    4.  `drenar_encomendas_antigas`, repeated until empty;
    5.  start the bot.

    For a fresh copy: `migrate`, create the superuser, enter the `Loja` and categories, start the bot.

### What stays the same

*   The webhook handlers `confirmar_pagamento` and `cancelar_por_expiracao`, refunds, `cancelar_encomenda`'s Stripe logic, `avancar_estado_encomenda`, `ajustar_stock`, the Telegram admin order and stock screens, logging, rate limiting and sessions.
*   Card-only payments: a completed session always means paid.

## Alternatives Considered

### From the brainstorm (concepts_brief_cart.md, "Alternatives Weighed")

#### A1: PC part picker with compatibility checks

*   **Pros**: It sets a PC-parts shop apart from generic Telegram shop bots.
*   **Cons**: It only matters to PC-parts shops. It needs structured spec data for every product (sockets, RAM type, power draw, form factors), and wrong data produces confident wrong answers.
*   **Reason for Rejection**: Rejected by author: it narrows the package to one kind of shop, which conflicts with selling general shop software, and the data upkeep falls on every client.

#### A2: One deployment serving many shops

*   **Pros**: The reseller runs one server for all clients, and upgrades happen once.
*   **Cons**: A shop id on every table and every query, where one missed filter shows one shop's orders to another. Several bots in one process, which breaks SEC-09's single-process assumption, plus routing each Stripe webhook to the right shop.
*   **Reason for Rejection**: Rejected by author: a copy per shop needs none of this and can't leak data between shops.

#### A3: Reserve stock when an item is added to the cart

*   **Pros**: An item in the cart is guaranteed at checkout.
*   **Cons**: It needs a timer to release idle reservations, and it lets any customer block stock just by browsing.
*   **Reason for Rejection**: Rejected by author: stock is taken at Confirm, as today, which keeps one place where stock changes for orders.

#### A4: Use a pending order (`estado='carrinho'`) as the cart

*   **Pros**: No new tables.
*   **Cons**: Cart rows would show up in the abandoned-order cleanup, the admin order screens and actions, and every SEC lock path, each of which would need a filter.
*   **Reason for Rejection**: Rejected: separate cart tables keep orders meaning "something the customer confirmed".

#### A5: Remove sold-out items from the cart automatically

*   **Pros**: One tap less for the customer.
*   **Cons**: The cart changes without the customer acting, so the order may differ from what they chose.
*   **Reason for Rejection**: Rejected by author: the line is flagged and checkout is blocked until the customer removes it.

#### A6: A per-country price rule

*   **Pros**: Resellers could adapt to local consumer law.
*   **Cons**: A setting with no known case that needs it, and every reseller would have to interpret the law themselves.
*   **Reason for Rejection**: Rejected: one rule (show the final price on the confirmation screen and charge exactly that) fits EU consumer rules. Resellers outside the EU check locally.

#### A7: Abandoned-cart recovery by email, with discounts, or by bot reminders

*   **Pros**: A familiar selling point that recovers some lost sales.
*   **Cons**:
    *   Email needs addresses the bot never collects.
    *   Discounts need a coupon system and per-order Stripe prices.
    *   Bot reminders are EU marketing messages: they need stored consent, an opt-out, one reminder per cart, handling of users who block the bot, a sending-rate limit, and another scheduled job.
*   **Reason for Rejection**: Rejected by author for this design: none of it strengthens the cart demo. Bot reminders are kept as a future idea; the cart tables already hold the data they would need.

#### A8: A configurable bot logo, or a welcome image

*   **Pros**: Visual branding per shop.
*   **Cons**: Bots can't set their profile photo through the API; it is set by hand in BotFather. A welcome image needs upload processing (SEC-11), caching of Telegram's copy, and a text fallback.
*   **Reason for Rejection**: Rejected by author: the profile photo set in BotFather gives the branding, and the shop name and welcome text are enough for the demo.

#### A9: Carry the confirmation screen as a hash in the button, or as a cart revision number

*   **Pros**:
    *   A hash needs no new table and no write when the screen is drawn.
    *   A revision number keeps the current token layout.
*   **Cons**:
    *   A hash can show that something changed but can't recover what the customer confirmed.
    *   A revision number still writes on every screen and says nothing about prices on its own.
*   **Reason for Rejection**: Rejected by author: a stored snapshot that never changes keeps a record of what the bot prepared for the customer, which helps settle a disputed charge. The design still uses the cart's revision number, but only inside the snapshot comparison.

#### A10: Restore the cart automatically, or an "Edit" button on a pending order

*   **Pros**: The customer gets their items back without searching for the old order.
*   **Cons**: An automatic restore surprises the customer. "Edit" cancels a pending order and can race a payment finishing in another tab, which then needs a refund.
*   **Reason for Rejection**: Rejected by author: "Repeat order" on cancelled unpaid orders gives the recovery path without touching an order that might still be paid.

#### A11: A 60-minute window per Stripe session instead of per order

*   **Pros**: Every payment link gets a full hour, and a customer whose first attempts failed doesn't lose the order.
*   **Cons**: Retries extend the hold. Without a cap on when the first link may be created, a link created just before the no-link cleanup could hold stock for close to two hours.
*   **Reason for Rejection**: Rejected by author: the short window exists to protect scarce stock, so retries must not extend it.

#### A12: A quantity picker on the one-product order before building the cart

*   **Pros**: A smaller first visible change with no new locking.
*   **Cons**: A temporary feature that the cart replaces.
*   **Reason for Rejection**: Rejected by author: the staged plan already limits the risk of the multi-line change, and the goal is one complete cart demo.

#### A13: A one-line-per-cart release setting so the code can roll back

*   **Pros**: Older code could be redeployed while the cart is being proven.
*   **Cons**: An extra setting and a second release for a risk that doesn't exist yet, since no shop runs the bot.
*   **Reason for Rejection**: Rejected by author: the release only moves forward, on a separate demo database, with backups before every migration once real shops exist.

#### A14: A unique short code (slug) per category in buttons

*   **Pros**: Old catalogue buttons such as `cat_ssd` would keep working after the migration.
*   **Cons**: A permanent field that owners must fill in, keep unique and never change.
*   **Reason for Rejection**: Rejected by author: there are no real customers with old buttons. Unknown or inactive categories show the expired-menu message instead.

#### A15: Keep retrying old pre-cart orders instead of cancelling them at deploy

*   **Pros**: No pending order is lost at the switch.
*   **Cons**: Their Stripe idempotency keys were used with the old request shape, so retries with the new parameters are rejected. Some old rows also lack `nome_produto`.
*   **Reason for Rejection**: Rejected by author: legacy pending orders are cancelled before the switch, with in-flight payments left to the webhook. The bot refuses to start only while legacy orders remain.

### Decided while drafting

#### A16: Snapshot lines in a child table instead of JSON

*   **Pros**: Each line could be queried or indexed on its own.
*   **Cons**: Joins and more rows for data that is written once, never changed and always read as a whole.
*   **Reason for Rejection**: Rejected by author when approving the Technical Plan: the snapshot is a record, not something to query line by line.

#### A17: Keep a "buy now" button beside "Add to cart"

*   **Pros**: One-product purchases keep their current two-tap path.
*   **Cons**: A second way to build snapshots and orders, with its own tests, beside the cart.
*   **Reason for Rejection**: Rejected by author when approving the Technical Plan: one checkout path through the cart.

#### A18: Cache the Telegram admin list in memory

*   **Pros**: No database query on admin checks, and the sync `is_admin` signature could stay.
*   **Cons**: Changes apply only after the cache refreshes or the bot restarts, and the cache needs its own expiry logic.
*   **Reason for Rejection**: Rejected by author when approving the Technical Plan: a change in Django admin must apply immediately, and one indexed query per admin check is cheap at one shop's traffic.

## Detailed Implementation Plan

Each stage is one branch and one PR. CI (`.github/workflows/tests.yml`) must pass before the next stage starts. Stage 0 has no code and must be finished first (see Open Questions Q1). The migration numbers assume no other migration lands in between.

### Target Files to Create/Modify

#### Stage 1: categories in the database

##### 1. `core/models.py` (Modify)

*   **Type / Scope**: Django models.
*   **Proposed Changes**:
    *   Add `Categoria(nome: CharField(60), ordem: PositiveIntegerField(default=0), ativo: BooleanField(default=True))`, with `Meta.ordering = ['ordem', 'nome']` and `__str__` returning `nome`.
    *   Replace `Produto.categoria` (a text field with `CATEGORIA_CHOICES`) with `categoria = ForeignKey(Categoria, on_delete=PROTECT, related_name='produtos')`.
    *   Keep `CATEGORIA_CHOICES` only as a module constant that the data migration imports for the old labels. It's no longer used on a field.
    *   **Why:** categories become shop data that the owner edits (G8).

##### 2. `core/migrations/0009_categoria.py` (Create)

*   **Type / Scope**: Schema migration.
*   **Proposed Changes**: Create `Categoria`. Add the nullable `Produto.categoria_nova` foreign key with `PROTECT`.

##### 3. `core/migrations/0010_categoria_dados.py` (Create)

*   **Type / Scope**: Data migration.
*   **Proposed Changes**:
    *   Forward: for each distinct `Produto.categoria` code in use, create a `Categoria`. Its name is the label from `CATEGORIA_CHOICES`, or the code itself if it is unknown, and `ordem` is the code's position in `CATEGORIA_CHOICES`. Then set `categoria_nova` on every product. A fresh install gets no categories.
    *   Reverse: raise `IrreversibleError` with "forward-fix only".

##### 4. `core/migrations/0011_categoria_troca.py` (Create)

*   **Type / Scope**: Schema migration.
*   **Proposed Changes**: Remove `Produto.categoria` (the text field). Rename `categoria_nova` to `categoria`. Make it required.

##### 5. `core/services.py` (Modify)

*   **Proposed Changes**:
    *   Add `get_categorias_ativas()` (async).
    *   Change `get_produtos_por_categoria(categoria_id)` to filter `categoria_id=…, categoria__ativo=True, ativo=True`, ordered by name.
    *   Change `get_todos_produtos()` to order by `categoria__ordem`, `categoria__nome`, `nome`, with `select_related('categoria')`.
    *   **Why:** this removes the hard-coded codes and keeps the sorting meaningful.

##### 6. `core/admin.py` (Modify)

*   **Proposed Changes**:
    *   Register `CategoriaAdmin` with `list_display` (nome, ordem, ativo), `list_editable` (ordem, ativo), and no delete permission.
    *   `ProdutoAdmin.list_filter` uses `categoria`.

##### 7. `telegram_bot/constants.py` (Modify)

*   **Proposed Changes**:
    *   Delete `CATEGORIAS`.
    *   Add `MENSAGEM_MENU_EXPIRADO = "Este menu já não está disponível. Abre o catálogo novamente."`.

##### 8. `telegram_bot/keyboards.py` (Modify)

*   **Proposed Changes**:
    *   `teclado_catalogo(categorias)` builds one `cat_<id>` button per category it receives.
    *   `teclado_produto` and `teclado_falha_registo` use `cat_{produto.categoria_id}`.
    *   Add `teclado_menu_expirado()`, with one "🛍️ Ver catálogo" button that sends `menu_catalogo`.

##### 9. `telegram_bot/handlers/customer.py` (Modify)

*   **Proposed Changes**:
    *   `menu_catalogo` loads the active categories.
    *   `categoria` parses the value after `cat_`. If it isn't a positive integer, or isn't an active category, it shows `MENSAGEM_MENU_EXPIRADO` with `teclado_menu_expirado()`. Otherwise it lists that category's products, with the category name taken from the row.
    *   **Why:** old `cat_ssd` buttons fail gracefully (G8).

##### 10. `telegram_bot/handlers/admin.py` (Modify)

*   **Proposed Changes**: `_texto_produto` shows `p.categoria.nome`, and the services fetch products with `select_related('categoria')`.

##### 11. `core/tests.py` and `telegram_bot/tests.py` (Modify)

*   **Type / Scope**: Tests.
*   **Proposed Changes**:
    *   The `criar_produto` helper creates or reuses a `Categoria("Armazenamento")`.
    *   Bot test fixtures with `categoria="ssd"` use `categoria_id=1` and `categoria=SimpleNamespace(nome="Armazenamento")`.
    *   New tests:
        *   the data migration maps existing codes to categories, using `MigrationExecutor` to migrate from 0009 to 0011 on a database with products in two codes;
        *   `cat_<id>` lists products;
        *   `cat_ssd`, `cat_999` and an inactive category show the expired-menu message;
        *   category order follows `ordem`.

#### Stage 2: multi-line order core

##### 12. `core/models.py` (Modify)

*   **Proposed Changes**:
    *   Add `ConfirmacaoCarrinho`:
        *   `nonce`: CharField(16), unique;
        *   `utilizador`: FK to Utilizador, `CASCADE`;
        *   `criada_em`: `auto_now_add`;
        *   `revisao_carrinho`: IntegerField, null;
        *   `moeda`: CharField(3), default "eur";
        *   `total`: Decimal(10,2);
        *   `linhas`: JSONField.

        `save()` raises an error when the row already has a pk, which makes the row immutable.
    *   Add `Encomenda.confirmacao` as a `OneToOneField(ConfirmacaoCarrinho, on_delete=PROTECT, null=True, related_name='encomenda')`.
    *   Add `Meta.constraints` on `Encomenda`: `UniqueConstraint(fields=['utilizador'], condition=Q(estado='pendente', confirmacao__isnull=False), name='uma_encomenda_pendente_por_cliente')`.

##### 13. `core/migrations/0012_confirmacao_carrinho.py` (Create)

*   **Proposed Changes**: Create the snapshot model, the `Encomenda.confirmacao` field and the partial unique constraint. Legacy rows have a null `confirmacao`, so the constraint can't fail on old data.

##### 14. `core/services.py` (Modify)

*   **Proposed Changes**:
    *   **Constants:** `JANELA_PAGAMENTO = timedelta(minutes=60)`, `CORTE_SESSAO = timedelta(minutes=28)`, `MAX_LINHAS = 20`, `MAX_QUANTIDADE = 99`, `VALIDADE_CONFIRMACAO = 30 min` (imported from `confirmacao.py`).
    *   **`_bloquear_produtos(ids)`:** `Produto.objects.select_for_update().filter(pk__in=ids).order_by('pk')`, returned as a dict by id. This is the only place products are locked for several lines.
    *   **`criar_confirmacao(utilizador, linhas, revisao_carrinho=None)`:** takes a list of (produto_id, quantidade). It reads live, unlocked product data and refuses (returns `None` plus the flagged lines) if any product is inactive or its stock is short. Otherwise it writes the snapshot with the names and unit prices shown, and returns it.
    *   **`criar_encomenda_de_confirmacao(telegram_user, nonce, emitida_em)`** runs in one transaction:

        ```text
        snap = lock ConfirmacaoCarrinho(nonce)           -- missing → ("invalida", None)
        if snap has encomenda → ("existente", that order) after owner check
        if snap.utilizador != caller → ("de_outro_utilizador", None)
        if confirmacao.expirada(emitida_em) → ("expirada", None)
        if snap.revisao_carrinho is not None:
            lock caller's Carrinho; if revisao != snap.revisao_carrinho → ("alterado", new snapshot)
        lock caller's Utilizador row; pending order with a snapshot exists → ("pendente", it)
        produtos = _bloquear_produtos(ids in snap)
        any product inactive, price != snapshot price, or stock < quantity → ("alterado", new snapshot)
        create Encomenda(estado='pendente', total=snap.total, confirmacao=snap, chave_confirmacao=snap.nonce)
        for each line ordered by produto_id: LinhaEncomenda (nome_produto from snapshot),
            stock update with F(), MovimentoStock 'saida'
        RegistoAuditoria CRIAR_ENCOMENDA listing "qty× name @ price" for every line
        if snap.revisao_carrinho is not None: delete the cart's lines, revisao += 1, atualizado_em = now
        return ("criada", encomenda)
        ```

        The partial unique constraint backs up the pending check. An `IntegrityError` on it is re-read and returned as `"pendente"`.
    *   **Remove `reservar_encomenda`, `reservar_encomenda_async` and `chave_substituicao`.** The order comes only from a snapshot, and replacement sessions end (D19).
    *   **`parametros_checkout(encomenda)`:** one `line_items` entry per line, ordered by `pk`, with `unit_amount = int((preco_unitario * 100).quantize(Decimal('1'), ROUND_HALF_UP))`, and `expires_at = int((data_encomenda + JANELA_PAGAMENTO).timestamp())`. It raises when a line has no `nome_produto`; only legacy rows can lack it, and they are drained.
    *   **`garantir_sessao_pagamento`:**
        *   With no `Pagamento` and `now - data_encomenda > CORTE_SESSAO`: call `cancelar_encomenda(id, 'prazo de pagamento esgotado')` and return `("expirada", None)`.
        *   When the stored session is `expired`: call `cancelar_encomenda(id, 'sessão expirada')` and return `("expirada", None)`, instead of creating a replacement.
        *   Everything else stays as it is: Stripe is never called under a lock, and the lock and re-check happen before saving.
    *   **`_devolver_stock(encomenda, observacoes)`:** first `_bloquear_produtos(ids of the order's lines)`, then the `F()` increments and the movements, in id order.
    *   **New async wrappers:** `criar_confirmacao_async` and `criar_encomenda_de_confirmacao_async`.

##### 15. `telegram_bot/confirmacao.py` (Modify)

*   **Proposed Changes**:
    *   `PREFIXO = "confirmar_carrinho_"`, so the token is `confirmar_carrinho_<nonce>_<ts>`.
    *   `callback_confirmar(chave, emitida_em=None)` keeps the 64-byte assert.
    *   `ler_callback` returns `("token", Confirmacao(chave, emitida_em))`, `("antigo", chave or None)` for the legacy `confirmar_encomenda_<nonce>_<ts>_<id>` and `confirmar_encomenda_<id>` formats, or `("invalido", None)`.
    *   `Confirmacao` loses `produto_id`.

##### 16. `telegram_bot/handlers/customer.py` (Modify)

*   **Proposed Changes**:
    *   **`encomendar` (Stage 2 only):** `criar_confirmacao(user, [(p.id, 1)])`, then shows the confirmation screen.
    *   **`confirmar_carrinho`** is the new handler. It maps each result:
        *   `criada` and `existente`: `_mostrar_pagamento`;
        *   `alterado`: redraw the confirmation screen with the changed lines marked "⚠️ preço alterado", "⚠️ sem stock suficiente" or "⚠️ indisponível";
        *   `pendente`: "Já tens uma encomenda por pagar", with buttons to that order;
        *   `expirada`: the expired screen;
        *   `de_outro_utilizador` and `invalida`: "❌ Pedido inválido".
    *   **Legacy `confirmar_encomenda_` buttons:** look up an existing order by `chave_confirmacao` and recover its payment. Otherwise show `MENSAGEM_ECRA_EXPIRADO`.
    *   **`_mostrar_pagamento`:** lists every line (`quantidade× nome — subtotal`, names cut to 60 characters and escaped with `md()`), then the total. It handles `"expirada"`: "O prazo de pagamento desta encomenda terminou." with a button to the order.
    *   **`PREFIX_ROUTES`** adds `("confirmar_carrinho_", confirmar_carrinho)` and keeps `("confirmar_encomenda_", confirmar_encomenda_antigo)`.

##### 17. `telegram_bot/keyboards.py` (Modify)

*   **Proposed Changes**: `teclado_confirmar_encomenda(callback_confirmar, voltar)`, where `voltar` sends the customer back to the product (Stage 2) or the cart (Stage 3).

##### 18. `security/sanitization.py` (Modify)

*   **Proposed Changes**: Add `confirmar_carrinho_` to `ALLOWED_PREFIXES`. Keep `confirmar_encomenda_` for legacy buttons.

##### 19. `core/admin.py` (Modify)

*   **Proposed Changes**: Register `ConfirmacaoCarrinho` view-only, using the existing `SoLeitura` mixin. The `EncomendaAdmin` detail view shows `confirmacao` read-only.

##### 20. `core/tests.py` (Modify)

*   **Proposed Changes**:
    *   **Replace the `reservar_encomenda` calls** in every test (about 23 places) with a helper `encomenda_teste(user, [(produto, qty)])` that builds a snapshot and confirms it.
    *   **Remove or rewrite** the tests that assume replacement sessions (D19): S9–S14, S19 and S22 become "expired session → order cancelled, stock returned, no new session". Test db4 becomes "two screens → second Confirm returns `pendente`".
    *   **New tests:**
        *   multi-line order: lines, stock and audit;
        *   `alterado` when price, stock, active status or cart revision changed, with nothing written;
        *   a snapshot used twice returns `existente`;
        *   another user's snapshot is refused;
        *   an expired snapshot;
        *   `parametros_checkout` line order, rounding and `expires_at`;
        *   session cutoff at 27, 28 and 29 minutes (with a patched clock);
        *   retry with the same key on each side of the cutoff;
        *   expired stored session → cancelled, no `Session.create`;
        *   `_devolver_stock` returns every line.
    *   **Threaded tests:**
        *   **T6:** two checkouts with products {A,B} and {B,A}. Both finish, with no deadlock error, and the stock is right.
        *   **T7:** a checkout racing a cancellation that returns stock for shared products.
        *   **T8:** two Confirm taps from two different snapshots of the same customer. One `criada` and one `pendente`.

##### 21. `telegram_bot/tests.py` (Modify)

*   **Proposed Changes**: Adapt the `CheckoutHandlerTests` H1–H14 to the new token and results. Add tests for each `confirmar_carrinho` outcome, for legacy button handling, and for the multi-line payment screen staying under 4096 characters at 20 lines.

#### Stage 3: the cart

##### 22. `core/models.py` (Modify)

*   **Proposed Changes**:
    *   Add `Carrinho(utilizador: OneToOneField(Utilizador, CASCADE), revisao: IntegerField(default=0), atualizado_em: DateTimeField(default=now))`.
    *   Add `LinhaCarrinho(carrinho: FK CASCADE related_name='linhas', produto: FK(Produto, CASCADE), quantidade: PositiveIntegerField, preco_ao_adicionar: Decimal(10,2))`, with `UniqueConstraint(carrinho, produto)` and `CheckConstraint(quantidade__gt=0)`.

##### 23. `core/migrations/0013_carrinho.py` (Create)

*   **Proposed Changes**: Create both cart models and their constraints. This only adds tables.

##### 24. `core/services.py` (Modify)

*   **Proposed Changes**:
    *   **`_carrinho_bloqueado(utilizador)`:** get or create the cart, then `select_for_update`.
    *   **`adicionar_ao_carrinho(telegram_user, produto_id)`:**
        *   refuses an inactive or out-of-stock product, and more than `MAX_LINHAS` lines;
        *   adds 1, up to the lower of `MAX_QUANTIDADE` and the current stock;
        *   sets `preco_ao_adicionar` to the current price;
        *   increases `revisao` and sets `atualizado_em`.

        It returns `("adicionado"|"limite_stock"|"limite_quantidade"|"limite_linhas"|"indisponivel", quantidade)`.
    *   **`alterar_quantidade(telegram_user, produto_id, delta)`:** same limits. Reaching 0 removes the line. Any change refreshes `preco_ao_adicionar`.
    *   **`remover_do_carrinho(...)`**.
    *   **`ver_carrinho(telegram_user)`:** no lock and no write. It returns lines with live price, stock and `ativo`, and the flags `preco_alterado` (live price ≠ `preco_ao_adicionar`), `stock_insuficiente` and `indisponivel`, plus the total at live prices.
    *   **`preparar_confirmacao_carrinho(telegram_user)`:** locks the cart and reads `revisao`, then calls `criar_confirmacao(user, lines, revisao)`. It refuses while any line is flagged. An empty cart returns `"vazio"`.
    *   **`repetir_encomenda(telegram_user, encomenda_id)`:** checks the order's owner, `estado='cancelada'`, and either no `Pagamento` or one not in (`pago`, `reembolsado`). It then locks the cart and merges each line, capped by stock and `MAX_QUANTIDADE` and skipping inactive products. It returns lists of what was added, reduced and skipped.
    *   **`contar_itens_carrinho(telegram_user)`:** for the menu button.

##### 25. `telegram_bot/handlers/customer.py` (Modify)

*   **Proposed Changes**:
    *   **New handlers:** `carrinho_ver`, `carrinho_add`, `carrinho_mais`, `carrinho_menos`, `carrinho_remover`, `carrinho_finalizar` and `repetir_encomenda`.
    *   **`encomendar`** is replaced by `carrinho_add`. The product screen button becomes "🛒 Adicionar ao carrinho".
    *   **The confirmation screen** is drawn from the snapshot, with "Confirmar" and "Voltar ao carrinho".
    *   **`encomenda_detalhe`** shows "🔁 Repetir encomenda" when `repetir_encomenda` would accept the order.
    *   **Routes** for the new prefixes and the exact routes `carrinho_ver` and `carrinho_finalizar`.

##### 26. `telegram_bot/keyboards.py` (Modify)

*   **Proposed Changes**:
    *   `teclado_carrinho(linhas)` has + / − / 🗑 per line and "Finalizar compra" (hidden while a line is flagged) plus "Continuar a comprar".
    *   `teclado_produto` uses `carrinho_add_<id>`.
    *   `teclado_menu_principal(user_id, admin, itens_carrinho)` adds "🛒 Carrinho (N)".
    *   Add `teclado_repetir(encomenda_id)`.

##### 27. `telegram_bot/constants.py` (Modify)

*   **Proposed Changes**: Add the cart texts in pt-PT:
    *   empty cart;
    *   each limit reached;
    *   flagged-line labels;
    *   pending order exists;
    *   payment deadline passed;
    *   the "Repeat order" result, listing reduced and skipped items.

##### 28. `security/sanitization.py` (Modify)

*   **Proposed Changes**:
    *   Add the prefixes `carrinho_add_`, `carrinho_mais_`, `carrinho_menos_`, `carrinho_remover_` and `repetir_encomenda_`.
    *   Add the exact values `carrinho_ver` and `carrinho_finalizar`.
    *   Remove `encomendar_` after Stage 3, so old buttons get the expired-menu path in their handler.

##### 29. `core/tests.py` and `telegram_bot/tests.py` (Modify)

*   **Proposed Changes**:
    *   **Cart service tests:**
        *   each add limit;
        *   minus to 0 removes the line;
        *   price change is flagged;
        *   an inactive product is flagged;
        *   viewing doesn't change `revisao` or `atualizado_em`;
        *   Confirm empties only that cart, and a cart changed after the screen was drawn gives `alterado`.
    *   **"Repeat order" tests:**
        *   merge with an existing line, capped;
        *   inactive and out-of-stock products skipped;
        *   refused for paid, refunded, pending or another user's order.
    *   **Routing tests:** every new callback passes `validar_callback` and reaches its handler, extending the SEC-12 test.
    *   **Message-size test** for the cart screen at 20 lines.

#### Stage 4: shop setup, admin rights and handover

##### 30. `core/models.py` (Modify)

*   **Proposed Changes**:
    *   Add `Loja(nome: CharField(80, default="Loja"), texto_boas_vindas: TextField(max_length=1000, default=<neutral pt-PT text>))`, whose `save()` forces `pk=1`.
    *   Add `Utilizador.e_admin = BooleanField(default=False)`.

##### 31. `core/migrations/0014_loja_e_admin.py` (Create)

*   **Proposed Changes**: Create `Loja` and add `Utilizador.e_admin`.

##### 32. `core/migrations/0015_grupo_dono_loja.py` (Create)

*   **Type / Scope**: Data migration.
*   **Proposed Changes**: Create the group "Dono da loja" with the permissions from the Technical Plan. It looks permissions up by codename after `create_permissions` has run, and the reverse deletes the group.

##### 33. `core/services.py` (Modify)

*   **Proposed Changes**:
    *   Add `obter_loja()`, which does `get_or_create(pk=1)`, plus its async wrapper.
    *   Add `async e_admin(user_id)`, true when `user_id` is in `ADMIN_TELEGRAM_IDS` (bootstrap, parsed once) or `Utilizador.objects.filter(telegram_id=str(user_id), e_admin=True, ativo=True).exists()`.

##### 34. `telegram_bot/guards.py` (Modify)

*   **Proposed Changes**: Remove `ADMIN_IDS` and `is_admin`. Callers use `await services.e_admin(user_id)`.

##### 35. `telegram_bot/handlers/callbacks.py`, `telegram_bot/handlers/commands.py`, `telegram_bot/keyboards.py` (Modify)

*   **Proposed Changes**:
    *   `callback_handler` awaits `e_admin` for routes marked admin-only.
    *   `/start` uses `obter_loja()` for the name (escaped with `md()`) and the welcome text, and passes the admin flag and cart count to `teclado_menu_principal`.
    *   `/admin` and `admin_menu` use the shop name instead of "Jarvis".
    *   `keyboards.py` no longer imports `is_admin`.

##### 36. `telegram_bot/handlers/admin.py` (Modify)

*   **Proposed Changes**: The admin menu title uses `obter_loja().nome`.

##### 37. `security/decorators.py` (Modify)

*   **Proposed Changes**: Delete the unused `ADMIN_IDS` and `admin_only`. There is only one admin source now.

##### 38. `core/admin.py` (Modify)

*   **Proposed Changes**:
    *   `LojaAdmin`: no add or delete; the change list redirects to the single row.
    *   `UtilizadorAdmin.get_readonly_fields` adds `e_admin` unless `request.user.is_superuser`, and `list_display` shows `e_admin`.

##### 39. `config/settings.py` and `.env.example` (Modify)

*   **Proposed Changes**:
    *   When `DJANGO_HTTPS=True`, set:
        *   `SECURE_SSL_REDIRECT`;
        *   `SESSION_COOKIE_SECURE` and `CSRF_COOKIE_SECURE`;
        *   `SECURE_HSTS_SECONDS=31536000`;
        *   `SECURE_PROXY_SSL_HEADER`, only if `DJANGO_BEHIND_PROXY=True`.
    *   Add `CSRF_TRUSTED_ORIGINS` from a comma-separated environment variable.
    *   `.env.example` documents both variables, plus the cron file.
    *   **Why:** this finishes SEC-01 before an owner gets a Django login (G9).

##### 40. `core/tests.py` and `telegram_bot/tests.py` (Modify)

*   **Proposed Changes**:
    *   `obter_loja` defaults on an empty database.
    *   `/start` shows the configured name and text, escaped.
    *   `e_admin` is true from the bootstrap list and from the database flag. Turning the flag off takes effect on the next check.
    *   A non-superuser staff user can't change `e_admin`.
    *   The "Dono da loja" group has exactly the listed permissions.
    *   Settings with `DJANGO_HTTPS=True`.

#### Scheduled jobs and switch-over

##### 41. `core/management/commands/limpar_rascunhos.py` (Create)

*   **Type / Scope**: Management command.
*   **Proposed Changes**:
    *   Delete `ConfirmacaoCarrinho` rows with no order whose `criada_em` is more than 45 minutes ago.
    *   Delete `Carrinho` rows whose `atualizado_em` is more than 30 days ago (their lines cascade).
    *   Print both counts. Never touch snapshots linked to an order (`encomenda__isnull=True` filter).
*   **Tests**:
    *   snapshots at 44 and 46 minutes;
    *   a linked snapshot older than 45 minutes is kept;
    *   carts at 29 and 31 days;
    *   viewing a cart doesn't refresh its timer.

##### 42. `core/management/commands/drenar_encomendas_antigas.py` (Create)

*   **Proposed Changes**:
    *   Run every pending order with `confirmacao IS NULL` through `cancelar_encomenda(id, 'migração para o carrinho')`.
    *   Catch `PagamentoRecebido` and `stripe.error.StripeError` for each order, and list those orders as skipped.
    *   Notify the customers of cancelled orders through `notificar_estado`.
    *   Print counts.
*   **Tests**:
    *   unpaid legacy orders are cancelled with their stock returned;
    *   a completed session is skipped;
    *   a Stripe error is skipped;
    *   orders with a snapshot are untouched;
    *   a second run changes nothing.

##### 43. `telegram_bot/app.py` (Modify)

*   **Proposed Changes**: `main()` adds `encomendas_antigas_pendentes()`, which returns the ids of pending orders with no snapshot. If the list isn't empty, it raises `SystemExit("Encomendas antigas por resolver: #… Corre manage.py drenar_encomendas_antigas.")`.
*   **Tests**: the bot refuses to start with one legacy pending order, and starts with a pending order that has a snapshot.

##### 44. `deploy/crontab.example` (Create)

*   **Proposed Changes**: Two entries, every 5 minutes: `manage.py cancelar_encomendas_abandonadas --horas 1` and `manage.py limpar_rascunhos`. Both run with the project's virtualenv and `.env`.

#### Documentation

##### 45. `DEPLOYMENT.md` (Create)

*   **Proposed Changes**: The reseller guide:
    *   one copy per shop;
    *   BotFather setup, including the profile photo;
    *   Stripe keys and webhook endpoint, with test mode first;
    *   `.env`;
    *   `migrate`;
    *   creating the superuser;
    *   entering `Loja` and categories;
    *   the owner's staff account in "Dono da loja";
    *   marking Telegram admins;
    *   installing the cron file;
    *   HTTPS settings;
    *   a database backup before every migration, and how to restore it;
    *   the switch-over order for an existing copy;
    *   a checklist to confirm the webhook works before taking real payments, since a broken webhook blocks the start after a drain.

##### 46. `README.md`, `ARCHITECTURE.md`, `SECURITY.md`, `ROADMAP.md`, `PROJECT_GUIDANCE.md` (Modify)

*   **Proposed Changes**:
    *   `README.md`: features and a link to `DEPLOYMENT.md`.
    *   `ARCHITECTURE.md`: the order flow (cart → snapshot → order), new tables, lock rules, scheduled commands, and the removal of D19 replacement.
    *   `SECURITY.md`: a new entry for cart abuse limits, snapshot tokens, one pending order per customer and admin rights in the database.
    *   `ROADMAP.md`: Stages 1–4 as rows.
    *   `PROJECT_GUIDANCE.md`:
        *   the cart decisions;
        *   retention: snapshots after 45 minutes, carts after 30 days, order-linked snapshots kept;
        *   the 60-minute window;
        *   the verified Stage 0 outcome.
    *   **Why:** the docs are part of what is sold.

##### 47. `concepts_brief_cart.md`, `goldfish_cart_report.md`, `design_draft.md` (Keep)

*   **Proposed Changes**: None in code. The final design is saved under `docs/designs/` at Step 6. The brief and report stay as background, and get committed or archived by the author.

### Verification Plan

*   **Compilation/Build**: `python manage.py check`; `python manage.py makemigrations --check --dry-run` (no drift after each stage).
*   **Unit Tests**: `python manage.py test core telegram_bot --noinput` on PostgreSQL 17, locally and in CI. Every existing SEC test either passes unchanged or is rewritten as listed under item 20.
*   **Concurrency**: the threaded tests T1–T5, plus the new T6–T8 (deadlock with opposite product order, checkout against cancellation, two Confirm taps). Each new lock test gets a mutation check: remove the `order_by('pk')` or the user lock and confirm that the test fails.
*   **Static Analysis & Linting**: none is configured in the project. Not added by this design.
*   **Test Coverage Requirement**: no percentage target. Every result branch of `criar_encomenda_de_confirmacao`, `garantir_sessao_pagamento`, each cart service and each new command has at least one test.
*   **E2E/Integration (G13)**: a manual run on a separate demo database, in Stripe test mode with `stripe listen` forwarding to the webhook:
    *   a two-product cart, paid, and the order confirmed;
    *   a price change shown on the confirmation screen;
    *   a paid order cancelled from the Telegram admin, then refunded;
    *   an unpaid order expired after 60 minutes (or with `expires_at` shortened only in a test copy), then "Repeat order";
    *   a final check that stock, orders, payments and audit rows add up.

    The results are recorded in `PROJECT_GUIDANCE.md`.

--------------------------------------------------------------------------------

*Drafted with EGM*
