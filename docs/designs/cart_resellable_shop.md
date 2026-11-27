# Multi-product cart and resellable single-shop setup

*   **Author:** Jose Diogo Pereira Saraiva
*   **Status:** Proposed
*   **Date:** 2026-11-24 (revised 2026-11-25 after the five-stage review)
*   **Predecessor:** the cart brainstorm (12 decisions settled) and its review. The brainstorm and review reports were removed on 2026-11-27 and remain in the git history.

--------------------------------------------------------------------------------

## Overview

The bot is a Portuguese (pt-PT) Telegram shop built on Django, PostgreSQL, python-telegram-bot and Stripe Checkout. Today it sells **one product per order**, and its shop identity is **hard-coded**: the name "Jarvis", a PC-parts welcome text, and 8 fixed PC categories.

This design changes it into a package that can be sold, with its rights, to a reseller. The reseller installs **one separate copy per client shop** and configures each copy without editing code. It covers four code stages, in this order:

1.  **Categories in the database.** A `Categoria` table replaces the 8 fixed categories.
2.  **Multi-line orders.** Order creation, Stripe payment links, stock locking, stock return and the reconciling cleanup work for orders with several lines. Customers still see today's one-product screens.
3.  **The cart.** A cart stored in the database, a confirmation screen saved as a snapshot that never changes, a 60-minute payment window, and "Repeat order".
4.  **Shop setup and handover.** A shop-settings record (name and welcome text), Telegram admin rights stored in the database, a permission group for shop owners in Django admin, and a reseller deployment guide.

A non-code **Stage 0** comes first and can stop the plan (see [Stage 0](#stage-0-prerequisites-no-code)).

## The Problem

**For the owner (seller of the rights).** A reseller buys code they can deploy for many clients. Today the code can't be handed over as a product:

*   It sells one product per order. No shop owner would accept that as a webshop, and it weakens the portfolio story.
*   Every shop would need code edits for its name, welcome text and categories.
*   Telegram admins are a list in `.env`, so changing who can manage the shop means editing the server's configuration and restarting the bot.

**For the shop's customers.** Buying three products means three separate orders, three payment links and three payments.

**For the shop owner.** They can't rename the shop, add a product or a category, or give a member of staff the admin panel without the reseller's help. There is no limited Django account to manage the catalogue on their own.

**Why not just add a cart quickly.** The checkout path was hardened by the security fixes SEC-02 to SEC-13 (see [Background](#background)), and it assumes one product per order:

*   the Confirm button carries one product id;
*   `reservar_encomenda` locks one product row;
*   `parametros_checkout` calls `encomenda.linhas.get()`, which fails on any order with two lines;
*   `_devolver_stock` loops over order lines in no fixed order.

That has several consequences:

*   Locking several products in no fixed order can deadlock two checkouts that share products.
*   Changing the Stripe request breaks payment retries for orders pending at deploy.
*   The abandoned-order cleanup only handles orders without a payment link (`core/services.py:460`), so a lost Stripe webhook can hold stock indefinitely.
*   With quantities limited only by stock, one customer could hold all of a scarce product.

A cart has to keep the guarantees that work made, not undo them.

## Background

### Security fixes this design builds on

The full record is in `SECURITY.md`.

| Id | What it guarantees today |
| --- | --- |
| SEC-01 | Safe Django defaults: `DEBUG` off, `ALLOWED_HOSTS` from the environment. The HTTPS, cookie and HSTS settings are still open; Stage 4 finishes them. |
| SEC-02 | Order creation is one transaction under a product row lock, so stock is never oversold. |
| SEC-03 | A failed checkout can be retried: the order's Stripe session is recovered with a stable idempotency key, and the payment row is saved before a link is shown. |
| SEC-04 | An expired Stripe session cancels the unpaid order and returns its stock (webhook). |
| SEC-05 | Payment confirmation by webhook is idempotent under row locks. |
| SEC-06 | One cancellation operation for every admin surface. It expires an open session or refunds a paid one under the order lock, then returns stock. |
| SEC-07 | Dynamic text in Telegram Markdown is escaped with `md()`. |
| SEC-09 | The bot is one polling process. Rate and session state live in memory. |
| SEC-11 | Product images are processed once, on upload. |
| SEC-12 | Every callback prefix is on an allowlist and routed to a handler. Anything else gets a generic "invalid request" alert before any handler runs. |
| SEC-13 | One Confirm screen creates at most one order, through a nonce stored in `Encomenda.chave_confirmacao`. |

### Earlier decision this design reverses

**D19**, recorded in `PROJECT_GUIDANCE.md` and the earlier brief: when a stored Stripe session expired, the bot created a replacement session with a new idempotency key. This design removes replacement sessions. An expired session ends the order (see Stage 2).

### Terms

*   **Idempotency key:** the key Stripe uses to return the same session for a repeated request. `chave_primeira_tentativa` builds it from `Encomenda.chave_confirmacao`. Stripe rejects a reused key sent with different parameters.
*   **Portuguese identifiers:**
    *   `Encomenda` = order;
    *   `LinhaEncomenda` = order line;
    *   `Utilizador` = customer or Telegram user;
    *   `Pagamento` = payment;
    *   `MovimentoStock` = stock movement;
    *   `RegistoAuditoria` = audit row;
    *   `Produto` = product;
    *   `Categoria` = category;
    *   `Loja` = shop;
    *   `estado` = state;
    *   `ativo` = active;
    *   `pendente`, `confirmada`, `cancelada` = pending, confirmed, cancelled;
    *   `pago`, `falhado`, `reembolsado` = paid, failed, refunded;
    *   `PagamentoRecebido` = "payment already received" (raised by the cancel when Stripe reports a completed session).
*   **Test series in `core/tests.py` and `telegram_bot/tests.py`:**
    *   S = payment-session recovery;
    *   H = checkout handler;
    *   db = order creation;
    *   E = end-to-end handler;
    *   X = expiry;
    *   C = cancellation;
    *   T = threaded concurrency;
    *   A = admin rules.

## Goals

*   **G1, a cart that survives restarts.** It is stored in the database, with up to 20 lines and a quantity per line of at most 99 and at most the available stock. It has plus, minus and remove buttons. Inactive, removed or short-stocked products are flagged and block checkout.
*   **G2, the customer pays exactly what they confirmed.**
    *   The confirmation screen shows current prices, and marks lines whose price changed since they were added.
    *   Drawing the screen saves a snapshot that never changes.
    *   Confirm compares the snapshot with live data while the rows are locked. If anything differs, nothing is written and the customer sees the cart again with the changes marked.
*   **G3, one order per snapshot and one pending order per customer.** Both hold under concurrent taps and are tested with threads. Confirming an old screen never removes cart items added later.
*   **G4, no deadlocks.** Every code path that locks several products locks them in product-id order. Checkout takes no lock on the customer row. Threaded tests cover:
    *   two checkouts that share products;
    *   a checkout racing a cancellation;
    *   an admin cancel racing the same customer's checkout.
*   **G5, stock held at most 65 minutes after Confirm, as a target.** It holds while the cleanup runs and Stripe and the database answer.
    *   Each payment link expires 60 minutes after the order was created.
    *   Links are created only while the order is at most 28 minutes old, because Stripe requires `expires_at` to be at least 30 minutes after a session is created: 60 − 28 = 32 leaves a 2-minute margin.
    *   The cleanup runs every 5 minutes and reconciles every pending order older than 60 minutes, so a run that succeeds releases or confirms it by 65 minutes, even when Stripe's webhook is lost.
    *   When Stripe or the database fails, the cleanup leaves the order unchanged (G12), and its stock stays held until a later run succeeds. Repeated failures show in the cleanup's summary line and need the operator's attention (see Risks & Mitigations).
*   **G6, "Repeat order"** on cancelled unpaid orders. It merges the old lines into the current cart, within the 20-line, per-line and stock limits, and says what was reduced or left out.
*   **G7, multi-line orders keep every existing guarantee (SEC-02 to SEC-06, SEC-13):**
    *   stable idempotency keys within an attempt;
    *   webhook confirmation under locks;
    *   full refunds;
    *   expiry cancellation.

    Every existing test either passes unchanged or is rewritten as listed in the Detailed Implementation Plan.
*   **G8, shop setup without code edits.**
    *   The reseller, or the owner with a limited Django account, sets the shop name, welcome text, categories and products in Django admin.
    *   Category buttons use database ids.
    *   Old or inactive category buttons show an "expired menu" message instead of an error.
*   **G9, safe admin handover.**
    *   **Telegram admins.** Admin rights are a database flag that only the reseller's superuser can grant or remove, and changes take effect without restarting the bot. The `.env` list bootstraps the first admin; after handover it is reduced to the reseller's id (see `DEPLOYMENT.md`).
    *   **The shop owner's Django account.** It can add, edit and deactivate products and categories, and edit the shop settings. It can read orders and payments, but it can't ship, deliver, cancel or refund orders from Django admin. The server enforces this through action permissions, not by hiding controls.
    *   **Cancellation and refunds** happen in the Telegram admin panel.
*   **G10, a repeatable deployment.** Each shop needs:
    *   one `.env`;
    *   one `migrate` run;
    *   one cron entry: the reconciling cleanup, every 5 minutes;
    *   one settings record.

    The guide covers HTTPS for the Django admin, backups before each migration, and a check during setup that the scheduled cleanup really runs. The existing startup check of required settings stays as it is.
*   **G11, clean data retention.** The same cleanup command:
    *   deletes snapshots that never became an order after 45 minutes;
    *   never deletes snapshots linked to an order;
    *   deletes carts after 30 days without activity. Activity means adding, changing or removing a line, including through "Repeat order"; viewing doesn't count.
*   **G12, one reconciling cleanup instead of a switch-over procedure.** Every 5 minutes, `cancelar_encomendas_abandonadas --horas 1` reconciles each pending order older than 60 minutes, whether or not it has a payment link or a snapshot:
    *   **no payment row:** cancel and return the stock;
    *   **stored Stripe session `complete`:** confirm it through `confirmar_pagamento`, the same path as the webhook, and notify the customer;
    *   **session `expired` or `open`:** cancel through `cancelar_encomenda`, which expires an open session and returns the stock;
    *   **Stripe or database error:** leave the order unchanged, log it, and retry on the next run.

    A lost webhook therefore never leaves a paid order pending or releases its stock. Orders from before the cart are reconciled the same way, with no drain command and no startup block.
*   **G13, success measure.** One complete demo on its own database, in Stripe test mode with a real webhook:
    *   add several products and change quantities;
    *   see a price change marked;
    *   confirm and pay;
    *   cancel a paid order with a refund;
    *   let a second order expire.

    At the end, stock = initial stock − quantities of paid orders. Every order is either paid and confirmed, or cancelled with its stock returned and any payment refunded.

## Non-Goals

*   **One deployment serving several shops.** Each shop gets its own copy, database, bot and Stripe account.
*   **A PC part picker** or compatibility checks.
*   **Other languages, currencies or payment providers.** The package stays pt-PT, EUR and Stripe, with card payments only.
*   **Reserving stock when an item is added to the cart.** Stock is taken only at Confirm.
*   **Partial refunds,** or cancelling single lines of an order.
*   **Legal and tax handling:** invoicing, tax documents, shipping costs, withdrawal handling, and legal texts such as terms and a privacy notice. Each shop arranges these itself, and the reseller contract says so. This is not legal advice and has not been verified by a lawyer.
*   **Email collection, discounts and coupons.**
*   **Abandoned-cart reminders.** They're kept as a future idea.
*   **A customer-facing "cancel my pending order" button.** Deferred (see Future Improvements).
*   **In-bot branding beyond the shop name and welcome text.** This includes a welcome image. The bot's profile photo is set per shop in BotFather.
*   **A guided setup inside the bot.** Shops are set up in Django admin.
*   **A one-line-per-cart release setting, or a code rollback path.** The release only moves forward, with database backups.
*   **A different payment window per shop.** It is fixed at 60 minutes.
*   **Keeping old catalogue buttons working.** They show the expired-menu message.

## Open Questions

*   **Q1, Stage 0 outcome.** The items marked "blocks the plan" in [Stage 0](#stage-0-prerequisites-no-code) aren't settled yet. If any of them fails, the reseller goal stops and the design falls back to a portfolio demo.

## Stage 0: prerequisites (no code)

| Item | What it checks | Blocks the plan |
| --- | --- | --- |
| Code ownership | That the owner can sell the code. This includes "course rights": rights the university or course may hold over the final-project code the bot started from. | Yes |
| Licence | Which licence the reseller gets, for one copy or for many copies, plus the notices dependencies require (for example python-telegram-bot's LGPL notice). | Yes |
| Demo images | That the product images are licensed for redistribution, or replaced. | Yes |
| Invoicing position | Written in the reseller contract: invoicing stays outside the bot and is each shop's responsibility. | No (it's a contract clause) |
| Stripe test run | One full payment, refund and expiry in Stripe test mode with a real webhook, on today's code. | No (it proves the base, and is repeated for G13) |

The outcome is recorded in `PROJECT_GUIDANCE.md`: what was checked, with whom, and the result.

## Critical User Journeys (CUJs)

### CUJ 1: A customer buys several products in one order

*   **Given**: products A (stock 3) and B (stock 1) are active, and the customer has no pending order.
*   **When**: the customer adds 2 of A and 1 of B to the cart, opens the confirmation screen, and taps Confirm within 30 minutes.
*   **Then**:
    *   One order with two lines is created in one transaction, stock becomes A=1 and B=0, and the cart is empty.
    *   The payment screen lists both lines with one Stripe link that expires 60 minutes after the order.
    *   Paying confirms the order through the existing webhook.

### CUJ 2: Something changed between the screen and Confirm

*   **Given**: the customer's confirmation screen shows A at 49.99 €. Before they tap Confirm, the admin changes A to 54.99 € and another customer buys the last unit of B.
*   **When**: the customer taps Confirm.
*   **Then**:
    *   Nothing is written: no order, and no stock change.
    *   The customer sees the cart again, with A marked "price changed: 49.99 € → 54.99 €" and B marked "out of stock".
    *   "Finalizar compra" stays hidden until B is removed. Then a new confirmation screen shows A at 54.99 €.

### CUJ 3: An unpaid order expires and the customer repeats it

*   **Given**: the customer confirmed an order but didn't pay within 60 minutes.
*   **When**: the order is cancelled (by Stripe's expiry webhook, or by the cleanup if the webhook is lost), and later the customer taps "Repeat order" on it while their cart holds other items.
*   **Then**:
    *   The order is cancelled and its stock returned.
    *   Its lines are merged into the cart at current prices, within the 20-line, per-line and stock limits.
    *   The customer is told what was reduced or left out, and nothing is ordered until they confirm again.

### CUJ 4: A reseller sets up a new shop

*   **Given**: a fresh server, a Telegram bot token from BotFather, and Stripe keys.
*   **When**: the reseller:
    *   fills `.env`, runs `migrate`, installs the cron entry, and checks that it runs;
    *   creates the superuser;
    *   in Django admin, enters the shop name, welcome text, categories and products;
    *   creates a limited account for the shop owner, and marks the owner's Telegram user as admin;
    *   reduces `ADMIN_TELEGRAM_IDS` to the reseller's own id.
*   **Then**:
    *   `/start` shows the shop's own name and welcome text, and the catalogue shows its categories.
    *   The owner manages orders, stock and cancellations from the Telegram panel, and the catalogue from Django admin.
    *   The owner can't grant admin rights, see staff accounts, or run order actions in Django admin. The server refuses them.

### CUJ 5: Switching an existing copy to the cart version

*   **Given**: a running copy with pending one-product orders from before the cart, one of them already paid in Stripe but not yet confirmed by a webhook that was lost.
*   **When**: the operator takes a backup, stops the bot, runs `migrate` and starts the bot.
*   **Then**:
    *   The bot starts at once.
    *   Within 5 minutes of each old order passing 60 minutes, the cleanup reconciles it: the paid one is confirmed, and the unpaid ones are cancelled with their links expired and stock returned.
    *   A Stripe error leaves that order unchanged until the next run.

## Technical Plan (Architecture)

### Principles

*   **One shop per copy.** No shop id on any table. Each copy has its own database, bot token, Stripe account, polling process and cron entry. This keeps SEC-09's one-process rule.
*   **Two processes per copy.** The bot (`python bot.py`, polling) and the Django web process (admin and the Stripe webhook at `/webhook/stripe/`) run separately. The webhook keeps working while the bot is stopped.
*   **Draft, snapshot, order.**
    *   The **cart** is a draft and reserves nothing.
    *   The **confirmation snapshot** records what the screen showed and never changes.
    *   The **order** is created from one snapshot, in one locked transaction.
    *   After order creation, these change: `parametros_checkout`, the payment screen, `_devolver_stock`, the session cutoff in `garantir_sessao_pagamento`, and the cleanup. The webhook handlers and `cancelar_encomenda` keep their logic.
*   **Lock rules (no deadlocks):**
    *   **Checkout:** snapshot, then cart (when the snapshot came from the cart), then products. It takes **no lock on `Utilizador`**. It then creates a new order, which no other transaction can be waiting on.
    *   **One pending order per customer** is enforced in two layers, both mapped to the `"pendente"` result:
        *   a plain read inside the checkout transaction refuses the checkout while the customer has any pending order, legacy orders included;
        *   a partial unique constraint covers two cart orders racing each other: the insert runs inside a savepoint, and a violation is mapped to `"pendente"`.

        The read needs no lock: legacy orders can no longer be created, so that set only shrinks, and every new order is covered by the constraint.
    *   **Cancellation, expiry, the cleanup and the webhook:** order, then payment (as today), then the order's products.
    *   **Products are always locked in one query ordered by id.** This includes the Django admin bulk activate and deactivate actions.
    *   **No path waits on an existing order's row while holding product locks.** Checkout never touches an existing order.
    *   **Why the customer row is not locked:** an order or audit insert takes a foreign-key share lock on `Utilizador`. Locking that row `FOR UPDATE` while holding product locks would form a cycle with a cancellation that holds product locks and inserts an audit row.
*   **Stripe calls:**
    *   Checkout and session creation never call Stripe while holding a lock.
    *   Cancellation (`cancelar_encomenda`) holds the order lock across its Stripe calls, by design (SEC-06). The webhook handlers wait on that lock and then see the final state.
*   **The payment window is measured from the order:** `data_encomenda` + 60 minutes, with sessions created only while the order is at most 28 minutes old (see G5 for why 28).

### Data flow

```text
 Product screen ──"Adicionar ao carrinho"──► Carrinho + LinhaCarrinho  (draft, revisao++)
                                                   │
                                   "Ver carrinho" / "Finalizar compra"
                                                   ▼
                       build snapshot from the cart (no lock on products; reads live data)
                       ConfirmacaoCarrinho(nonce, user, revisao_carrinho, lines JSON in cents, total)
                                                   │  screen shows lines, prices, ⚠ changes
                                   Confirm button: confirmar_encomenda_<nonce>
                                                   ▼
  ┌──────────────── one transaction: criar_encomenda_de_confirmacao ─────────────────┐
  │ lock snapshot                      — missing                 → "invalida"          │
  │ snapshot already has an order      — caller is its owner     → "existente"         │
  │ snapshot belongs to someone else                             → "de_outro_utilizador"│
  │ now − criada_em > 30 min                                     → "expirada"          │
  │ caller has any pending order (legacy included)               → "pendente" (theirs) │
  │ lock cart; cart revisao ≠ snapshot's                         → "alterado" (changes)│
  │ lock products by id; price / active / stock / exists differ  → "alterado" (changes)│
  │ savepoint: create Encomenda (+ link to snapshot)                                   │
  │     unique "one pending per customer" violated               → "pendente" (theirs) │
  │ N lines, N stock movements (F()), one audit row listing every line                 │
  │ delete the cart's lines; revisao++                           → "criada"            │
  └────────────────────────────────────────────────────────────────────────────────────┘
       "alterado" writes nothing: the transaction rolls back and the handler shows the cart
       with the changes after the commit.
                                                   │ commit
                                                   ▼
            garantir_sessao_pagamento (Stripe never called under a lock)
               order age ≤ 28 min, no session → Session.create(line per order line, expires_at)
               order age > 28 min, no session → cancelar_encomenda → "expirada"
               stored session expired          → cancelar_encomenda → "expirada"
                                                   │
                         Stripe webhook (Django process): completed → confirmar_pagamento
                                                          expired   → cancelar_por_expiracao
                                                   │
              every 5 min: cleanup reconciles pending orders > 60 min (see G12)
                                                   │
                   cancelled & unpaid → "Repeat order" merges lines back into the cart
```

### Stage 1: categories in the database

*   **New model `Categoria`:** `nome` (≤ 60 characters), `ordem` (integer, for display order), `ativo`.
    *   Products refer to it with `on_delete=PROTECT`.
    *   Categories are deactivated, never deleted, and ids are never reused.
*   **Migration in three steps:**
    1.  Add `Categoria` and a nullable `Produto.categoria_nova` foreign key.
    2.  A data migration creates one category for each code products actually use, then points each product at it.
        *   The names are today's model labels ("Processadores", and so on), copied into the migration from `Produto.CATEGORIA_CHOICES`.
        *   Codes not in that list get the code itself as their name and `ordem = 99`.
    3.  Remove the old `categoria` text field and rename `categoria_nova` to `categoria`, now required.

    Reversing is forward-fix only: the reverse of step 2 raises an error with a message instead of guessing old codes.
*   **The bot:**
    *   The catalogue lists active categories ordered by `ordem`, then name, and buttons carry `cat_<id>`.
    *   Category names are escaped with `md()` wherever they appear in a Markdown message.
    *   `CATEGORIAS` in `telegram_bot/constants.py` is removed.
    *   The `cat_` prefix stays on the allowlist. The handler reads the value as an id. If it isn't a number, or isn't an active category, the handler shows the expired-menu message with the existing "back to catalogue" keyboard instead of the generic "invalid request" alert. That covers old `cat_ssd` buttons.
*   **Admin:**
    *   `Categoria` is registered in Django admin, with list ordering, an active filter and no delete.
    *   `ProdutoAdmin` filters by category, and the admin product screen shows the category name.

### Stage 2: multi-line order core (no new screens yet)

*   **Snapshot model `ConfirmacaoCarrinho`.** Rows are never updated after insert: the model refuses `save()` on an existing row.
    *   **Fields:**
        *   `nonce`: 16 hex characters, unique;
        *   `utilizador`: foreign key;
        *   `criada_em`;
        *   `revisao_carrinho`: integer. It is null only for Stage 2's one-product snapshots, which don't come from a cart;
        *   `total_centimos`: integer;
        *   `linhas`: a JSON list of `{produto_id, nome, quantidade, preco_unitario_centimos}`.
    *   **Prices are integer cents.** That avoids `Decimal` in JSON and rounding drift. They are read back with `Decimal(c) / 100`.
    *   **JSON, not a child table:** the lines are written once, never changed, and always read together.
*   **The order link:**
    *   `Encomenda.confirmacao` is a one-to-one, nullable link to the snapshot.
    *   The order also stores the snapshot's nonce in `chave_confirmacao`, because the Stripe idempotency key `chave_primeira_tentativa` is built from that field. Storing the nonce there keeps one key per order attempt.
    *   A "legacy order" is a pending order whose `confirmacao` is null.
*   **One pending order per customer:** a partial unique constraint on `Encomenda(utilizador)`, applied only when `estado='pendente'` and `confirmacao` is set. Legacy rows are excluded, so the migration can't fail on old data. Legacy pending orders are covered instead by the pending-order read inside order creation, so a customer with a pre-cart order still pending gets `"pendente"` until the cleanup settles it.
*   **Order creation:** `criar_encomenda_de_confirmacao(telegram_user, nonce)` replaces `reservar_encomenda`. It runs the transaction in the diagram above and returns `(resultado, valor)`:

    | Result | When | Value |
    | --- | --- | --- |
    | `"criada"` | The order was created. | the order |
    | `"existente"` | This snapshot already has an order and the caller owns it. | that order |
    | `"de_outro_utilizador"` | The snapshot belongs to another customer. | none |
    | `"invalida"` | No snapshot has this nonce. | none |
    | `"expirada"` | The snapshot is more than 30 minutes old (`criada_em`). | none |
    | `"pendente"` | The customer already has a pending order. | that order |
    | `"alterado"` | The cart, a price, active status, stock or a product's existence differs from the snapshot. Nothing is written. | a list of changes |

    Each change in an `"alterado"` result is `(produto_id, tipo, antes, depois)`, where `tipo` is one of `preco`, `stock`, `inativo` or `removido`. `antes` and `depois` come from the snapshot and the live row; a deleted product gives `removido`.

    Stock is checked and decremented for every line only after all products are locked.
*   **The one-product screen in this stage:** "Encomendar" builds a one-line snapshot (`revisao_carrinho` null), and the cart check is skipped. On `"alterado"`, the product screen is redrawn with the change marked. Stage 3 replaces this button.
*   **Confirm token:** `confirmar_encomenda_<nonce>`. It keeps the existing prefix, so the allowlist doesn't change, and is 36 bytes long.
    *   The screen's age is checked on the server against `criada_em`. No client timestamp is used.
    *   `ler_callback` accepts:
        *   exactly 16 lowercase hex characters, meaning a snapshot;
        *   the old `<nonce>_<ts>_<id>` form, used only to find an existing order by `chave_confirmacao` ("existing order always wins").

        Anything else, including the pre-token `confirmar_encomenda_<id>`, shows the expired screen.
*   **Stripe parameters:** `parametros_checkout(encomenda)` builds, all from stored data so that retries with one key send identical parameters:
    *   one `line_items` entry per order line, ordered by line id;
    *   `unit_amount` = the unit price in cents, rounded half-up with `quantize` before converting to an integer;
    *   `expires_at` = `data_encomenda` + 60 minutes, as a Unix timestamp.

    Orders are capped at 20 lines, well under Stripe's 100.
*   **Session cutoff in `garantir_sessao_pagamento`.** In both cases below, `PagamentoRecebido` from the cancel returns `"a_confirmar"`, and a Stripe error goes to the existing payment-failure screen (the cleanup retries later).
    *   **No payment row yet, and the order is more than 28 minutes old:** no session is created. The order is cancelled through `cancelar_encomenda`, and the call returns `"expirada"`.
    *   **Stored session `expired`:** it calls `cancelar_encomenda` instead of creating a replacement (this ends D19).
*   **Stock return:** `_devolver_stock` locks the order's products in one query ordered by id, then applies the `F()` increments.
*   **Reconciling cleanup** (G12), changed in this stage:
    *   `encomendas_abandonadas` drops its `pagamento__isnull=True` filter.
    *   `cancelar_se_abandonada` becomes `reconciliar_encomenda`. It locks the order, re-checks that it is still pending and old enough, reads the order's `Pagamento` row, and then:
        *   no payment row: cancel;
        *   otherwise: `cancelar_encomenda`, which expires an open session or cancels after an expired one;
        *   if that raises `PagamentoRecebido` (the stored session is `complete`): `confirmar_pagamento(pagamento.pk, pagamento.stripe_session_id)`, the webhook's own call, then send the "payment confirmed" message.

        A Stripe or database error is logged and leaves the order unchanged.
    *   The same command deletes old snapshots and idle carts (G11) once Stage 3 exists.
    *   **Known limit:** a legacy order younger than 60 minutes that still needs its first session sends new parameters with its old key, so Stripe may reject it. The customer sees the payment-failure screen, and the cleanup normally reconciles the order within 65 minutes.
*   **Payment screen:** it lists every line as `quantity × name — subtotal`, with names escaped for Markdown, then the total.
*   **Audit:** `CRIAR_ENCOMENDA` lists every line. A blocked Confirm (`"alterado"`) writes a log line, not an audit row: it doesn't change stored data.

### Stage 3: the cart

*   **Models:**
    *   **`Carrinho`:** one per customer (one-to-one with `Utilizador`), with `revisao` (integer, increased on every change) and `atualizado_em` (last activity).
    *   **`LinhaCarrinho`:** `carrinho`; `produto` (`on_delete=CASCADE`); `quantidade`; and `preco_ao_adicionar`, the price when the line was added or last changed, used to mark price changes.
    *   **Constraints:** one line per product per cart, and `quantidade > 0`. Every constraint is named.
*   **Limits:**
    *   20 lines per cart;
    *   99 per line;
    *   a line's quantity is never above the product's current stock when it is changed.

    The plus button stops at whichever limit comes first and says which.
*   **Services.** Each service that changes the cart runs in one transaction that locks the cart row. Each one increases `revisao` and sets `atualizado_em`; viewing changes neither.

    | Service | Result |
    | --- | --- |
    | `adicionar_ao_carrinho(user, produto_id)` | `("adicionado" \| "limite_stock" \| "limite_quantidade" \| "limite_linhas" \| "indisponivel", quantidade)` |
    | `alterar_quantidade(user, produto_id, delta)` | the same results as above; reaching 0 removes the line, giving `("removido", 0)` |
    | `remover_do_carrinho(user, produto_id)` | `"removido"` or `"inexistente"` |
    | `ver_carrinho(user)` | the lines with live price, stock and status, each with the flags `preco_alterado`, `stock_insuficiente`, `indisponivel` and `removido`, plus the total at live prices. No lock and no write. |
    | `preparar_confirmacao_carrinho(user)` | `("ok", snapshot)`, `("vazio", None)` or `("bloqueado", flagged lines)` |
    | `pode_repetir(encomenda, user)` | `True` or `False`. A read-only check for showing the "Repeat order" button. |
    | `repetir_encomenda(user, encomenda_id)` | `(added, reduced, skipped)` lists |

    `preparar_confirmacao_carrinho` locks the cart, reads `revisao`, and builds the snapshot.
*   **Which flags block checkout:** `stock_insuficiente`, `indisponivel` and `removido` block it, and hide "Finalizar compra". `preco_alterado` only marks the line, and the confirmation screen shows the new price.
*   **"Repeat order":**
    *   It is allowed only for the customer's own cancelled order with no payment marked `pago` or `reembolsado`.
    *   It merges the lines within the 20-line, per-line and stock limits, skipping inactive products, and reports what it reduced or skipped.
*   **Screens and buttons:**
    *   The product screen gets "🛒 Adicionar ao carrinho", which replaces "Encomendar". The old `encomendar_<id>` prefix stays on the allowlist and adds that product to the cart, so old buttons still do something useful.
    *   The main menu gets "🛒 Carrinho", with no count.
    *   The cart screen has + / − / 🗑 for each line, "Finalizar compra" and "Continuar a comprar".
    *   The confirmation screen has "Confirmar" and "Voltar ao carrinho".
    *   Cancelled unpaid orders show "🔁 Repetir encomenda" in the order details when `pode_repetir` passes.
    *   **New callback prefixes,** each added to both `ALLOWED_PREFIXES` and `PREFIX_ROUTES`:
        *   `carrinho_add_<produto_id>`
        *   `carrinho_mais_<produto_id>`
        *   `carrinho_menos_<produto_id>`
        *   `carrinho_remover_<produto_id>`
        *   `repetir_encomenda_<id>`
    *   **New exact routes:** `carrinho_ver` and `carrinho_finalizar`.
*   **Result screens:**

    | Screen | Text | Buttons |
    | --- | --- | --- |
    | Expired | "Este ecrã expirou." | Ver carrinho, Menu |
    | Pending order | "Já tens uma encomenda por pagar." | Ver encomenda (`ver_encomenda_<id>`), Menu |
    | Payment link failed | the existing failure text | Tentar novamente (same token), Menu |
    | Invalid | "❌ Pedido inválido." | Menu |
    | Payment deadline passed | "O prazo de pagamento desta encomenda terminou." | Ver encomenda, Menu |

*   **Messages:** the cart and confirmation texts stay under Telegram's 4096-character limit at 20 lines with 200-character names. Names are cut to 60 characters on screen, and the snapshot keeps the full name.

### Stage 4: shop setup and handover

*   **`Loja` (shop settings):** a single row with `pk=1`, holding `nome` (≤ 80 characters) and `texto_boas_vindas` (a `CharField`, ≤ 1000 characters).
    *   `obter_loja()` uses `get_or_create`, defaulting to "Loja" and a neutral welcome text, so an empty database still starts.
    *   The admin blocks add and delete.
    *   `/start` shows the name and the welcome text, both passed through `md()`.
    *   The admin panel title uses the name instead of "Jarvis".
*   **Telegram admins:** `Utilizador.e_admin` (boolean).
    *   `e_admin(user_id)` is async. It returns true when the id is in the bootstrap set from `ADMIN_TELEGRAM_IDS`, or when the database flag is set (checked with `aexists()` on every call, so changes take effect immediately).
    *   It replaces both `guards.is_admin` and the unused `security.decorators.admin_only` list.
    *   In this stage, `teclado_menu_principal(user_id, admin)` receives the admin flag instead of calling the check itself.
    *   In Django admin, `e_admin` is read-only unless the user is a superuser.
*   **Shop-owner permissions:** a data migration creates the group "Dono da loja":
    *   add, change and view: `Produto`, `Categoria`;
    *   change and view: `Loja`;
    *   view only: `Encomenda`, `LinhaEncomenda`, `Pagamento`;
    *   no delete anywhere. Products and categories are removed by deactivating them;
    *   nothing on `Utilizador`, `MovimentoStock`, `RegistoAuditoria`, `ConfirmacaoCarrinho`, `Carrinho` or Django's users and groups.

    **Server-side enforcement:**
    *   The order admin actions declare `allowed_permissions`:
        *   `marcar_cancelada` requires the custom permission `core.cancelar_encomenda` (declared in `Encomenda.Meta.permissions`), which the group doesn't have;
        *   `marcar_enviada` and `marcar_entregue` require the change permission on orders.
    *   Django checks these when the action is posted, so a crafted request is refused. Superusers have every permission.
*   **Remaining SEC-01 settings:** HTTPS redirect, secure cookies, HSTS and `CSRF_TRUSTED_ORIGINS`. They are read from three environment variables and switched on when `DJANGO_HTTPS=True`, so local development is unaffected:
    *   `DJANGO_HTTPS`;
    *   `DJANGO_BEHIND_PROXY`;
    *   `CSRF_TRUSTED_ORIGINS`.
*   **Handover:** after the owner's Telegram user is flagged as admin, `ADMIN_TELEGRAM_IDS` is reduced to the reseller's id (or emptied) and the bot restarted once. That is the only way to remove a bootstrap admin.

### Scheduled job and deployment

*   **One cron entry, every 5 minutes:** `manage.py cancelar_encomendas_abandonadas --horas 1`. It reconciles pending orders (G12), deletes unused snapshots after 45 minutes, and deletes idle carts after 30 days (G11). It prints one summary line with counts.
*   **Setup check:** run the command once by hand, then confirm a second run's summary line appears in the log within 5 minutes.
*   **Deploy order for an existing copy:**
    1.  database backup;
    2.  stop the bot;
    3.  `migrate`;
    4.  start the bot.

    Legacy orders are reconciled by the next cleanup runs.
*   **Fresh copy:** `migrate`, create the superuser, enter the `Loja`, categories and products, install and check the cron entry, start the bot.

### What stays the same

*   The webhook handlers `confirmar_pagamento` and `cancelar_por_expiracao`.
*   Refunds, and the Stripe logic of `cancelar_encomenda`.
*   `avancar_estado_encomenda` and `ajustar_stock`.
*   The Telegram admin order and stock screens.
*   Logging, rate limiting and sessions.
*   Card-only payments: a completed session always means paid.

## Risks & Mitigations

| Risk | Mitigation |
| --- | --- |
| Stage 0 fails (ownership, licence or images). | It runs first. The design falls back to a portfolio demo (Q1). |
| Lock cycle between checkout and cancellation. | Products are locked in id order, and checkout takes no lock on the customer row. Covered by threaded tests T6, T7 and T9. |
| Stripe's webhook is lost or late. | The 5-minute cleanup reconciles every pending order older than 60 minutes (G12). |
| A Stripe key is reused with new parameters for a legacy order at deploy. | The customer sees the failure screen, and the cleanup normally reconciles the order within 65 minutes. |
| One customer holds scarce stock by repeating orders. | One pending order per customer, a 65-minute target per hold, and every repeat needs a new Confirm. Accepted risk. |
| A shop owner changes orders or refunds from Django admin. | The group has view-only order rights, and the actions declare `allowed_permissions` checked on the server (test A8). |
| Owner text breaks Telegram Markdown. | The welcome text and category names go through `md()` (SEC-07). |
| The cleanup fails repeatedly (Stripe outage, database error, or the cron job stopped), so stock stays held past 65 minutes. | Each run's summary line counts errors, and the setup check confirms the job runs. `DEPLOYMENT.md` tells the operator to check the log daily and act when errors repeat or summary lines stop. No automatic alert: needs attention. |
| A migration can't be reversed. | The release only moves forward, with a database backup before every migration (`DEPLOYMENT.md`). |

## Alternatives Considered

### From the brainstorm

The source for this group is the cart brainstorm, "Alternatives Weighed" and "Key Decisions".

#### A1: PC part picker with compatibility checks

*   **Pros**: It sets a PC-parts shop apart from generic Telegram shop bots.
*   **Cons**: It only matters to PC-parts shops. It needs structured spec data for every product (sockets, RAM type, power draw, form factors), and wrong data produces confident wrong answers.
*   **Reason for Rejection**: Rejected by author (the cart brainstorm, Key Decisions): it narrows the package to one kind of shop, and the data upkeep falls on every client.

#### A2: One deployment serving many shops

*   **Pros**: The reseller runs one server for all clients, and upgrades happen once.
*   **Cons**:
    *   A shop id on every table and every query, where one missed filter shows one shop's orders to another.
    *   Several bots in one process, which breaks SEC-09's single-process assumption.
    *   Each Stripe webhook has to be routed to the right shop.
*   **Reason for Rejection**: Rejected by author (the cart brainstorm, Key Decisions): a copy per shop needs none of this and can't leak data between shops.

#### A3: Reserve stock when an item is added to the cart

*   **Pros**: An item in the cart is guaranteed at checkout.
*   **Cons**: It needs a timer to release idle reservations, and it lets any customer block stock just by browsing.
*   **Reason for Rejection**: Rejected by author (the cart brainstorm, Key Decisions): stock is taken at Confirm, as today.

#### A4: Use a pending order (`estado='carrinho'`) as the cart

*   **Pros**: No new tables.
*   **Cons**: Cart rows would show up in the cleanup, the admin order screens and actions, and every SEC lock path, each of which would need a filter.
*   **Reason for Rejection**: Rejected by the Goldfish brainstorm review (the brainstorm review, §4), and kept by the author: separate cart tables keep orders meaning "something the customer confirmed".

#### A5: Remove sold-out items from the cart automatically

*   **Pros**: One tap less for the customer.
*   **Cons**: The cart changes without the customer acting.
*   **Reason for Rejection**: Rejected by author (the cart brainstorm, Key Decisions): the line is flagged and checkout is blocked until the customer removes it.

#### A6: A per-country price rule

*   **Pros**: Resellers could adapt to local consumer law.
*   **Cons**: A setting with no known case that needs it, and every reseller would have to interpret the law themselves.
*   **Reason for Rejection**: Rejected during the brainstorm and accepted by the author (the cart brainstorm, Alternatives Weighed): one rule, which is to show the final price on the confirmation screen and charge exactly that. That this fits EU consumer rules is the author's reading, not legal advice, and has not been verified.

#### A7: Abandoned-cart recovery by email, with discounts, or by bot reminders

*   **Pros**: A familiar selling point.
*   **Cons**:
    *   Email needs addresses the bot never collects.
    *   Discounts need a coupon system and per-order Stripe prices.
    *   Bot reminders are EU marketing messages: they need consent, an opt-out, delivery safeguards and another scheduled job.
*   **Reason for Rejection**: Rejected by author for this design (the cart brainstorm, Key Decisions). Bot reminders are kept as a future idea.

#### A8: A configurable bot logo, or a welcome image

*   **Pros**: Visual branding per shop.
*   **Cons**: Bots can't set their profile photo through the API. A welcome image needs upload processing (SEC-11), caching of Telegram's copy, and a text fallback.
*   **Reason for Rejection**: Rejected by author (the cart brainstorm, Key Decisions): the profile photo set in BotFather gives the branding.

#### A9: Carry the confirmation screen as a hash in the button, or as a cart revision number

*   **Pros**: A hash needs no new table and no write when the screen is drawn. A revision number keeps the token layout.
*   **Cons**: A hash can show that something changed but can't recover what the customer confirmed. A revision number says nothing about prices on its own.
*   **Reason for Rejection**: Rejected by author (the cart brainstorm, Key Decisions): a stored snapshot keeps a record of what the bot prepared, which helps settle a disputed charge. The cart's revision number is still used inside the snapshot comparison.

#### A10: Restore the cart automatically, an "Edit" button, or a customer cancel button on a pending order

*   **Pros**: The customer gets their items back, or frees their pending slot, without waiting.
*   **Cons**: An automatic restore surprises the customer. "Edit" and "cancel" both change a pending order and can race a payment finishing in another tab.
*   **Reason for Rejection**: Rejected by author (the cart brainstorm, Key Decisions; customer cancel deferred on 2026-11-25): "Repeat order" on cancelled unpaid orders gives the recovery path.

#### A11: A 60-minute window per Stripe session instead of per order

*   **Pros**: Every link gets a full hour.
*   **Cons**: Retries extend the hold. Without a cap on when the first link may be created, a hold could approach two hours.
*   **Reason for Rejection**: Rejected by author (the cart brainstorm, decision on the payment window): the short window exists to protect scarce stock.

#### A12: A quantity picker on the one-product order before building the cart

*   **Pros**: A smaller first visible change.
*   **Cons**: A temporary feature that the cart replaces.
*   **Reason for Rejection**: Rejected by author (the cart brainstorm, Key Decisions).

#### A13: A one-line-per-cart release setting so the code can roll back

*   **Pros**: Older code could be redeployed while the cart is being proven.
*   **Cons**: An extra setting and a second release for a risk that doesn't exist yet.
*   **Reason for Rejection**: Rejected by author (the cart brainstorm, release safety): forward-only, with backups.

#### A14: A unique short code (slug) per category in buttons

*   **Pros**: Old catalogue buttons would keep working.
*   **Cons**: A permanent field that owners must fill in, keep unique and never change.
*   **Reason for Rejection**: Rejected by author (the cart brainstorm, Key Decisions).

#### A15: A drain command and a startup block for pre-cart orders

*   **Pros**: Old orders are settled before the new code serves customers.
*   **Cons**:
    *   A one-off command and permanent startup code for a situation the reconciling cleanup already covers.
    *   The startup block keeps the bot down while any paid legacy order waits for a webhook.
*   **Reason for Rejection**: Rejected by author on 2026-11-25 (review decision A), replacing the brief's drain decision: the 5-minute cleanup reconciles every pending order older than 60 minutes, legacy ones included.

### Decided while drafting

These were approved at the Technical Plan gate, 2026-11-24.

#### A16: Snapshot lines in a child table instead of JSON

*   **Pros**: Each line could be queried on its own.
*   **Cons**: Joins and more rows for data written once and always read whole.
*   **Reason for Rejection**: Rejected by author: the snapshot is a record, not something to query line by line.

#### A17: Keep a "buy now" button beside "Add to cart"

*   **Pros**: One-product purchases keep their two-tap path.
*   **Cons**: A second checkout path with its own tests.
*   **Reason for Rejection**: Rejected by author: one checkout path through the cart.

#### A18: Cache the Telegram admin list in memory

*   **Pros**: No database query on admin checks.
*   **Cons**: Changes apply only after the cache refreshes or the bot restarts.
*   **Reason for Rejection**: Rejected by author: a change in Django admin must apply immediately. One indexed query per admin check is cheap at one shop's traffic, an estimate that hasn't been measured.

### Decided in review (2026-11-25)

#### A19: Lock the customer row to enforce one pending order

*   **Pros**: An explicit check before the insert.
*   **Cons**: It forms a lock cycle with cancellation through the foreign-key share lock, and it duplicates the unique constraint.
*   **Reason for Rejection**: Rejected after the review (the design review, item 🔴2): the constraint plus a savepoint enforces the rule without the extra lock.

#### A20: Build a new snapshot when Confirm finds a change

*   **Pros**: The customer would land on a fresh confirmation screen at once.
*   **Cons**: It writes while product locks are held, and it can't build a snapshot when an item is out of stock.
*   **Reason for Rejection**: Rejected after the review (item 🔴3): `"alterado"` writes nothing and shows the cart with the changes.

#### A21: A timestamp in the Confirm button

*   **Pros**: Age is known without a database read.
*   **Cons**: It comes from the client and duplicates `criada_em`.
*   **Reason for Rejection**: Rejected after the review (item 🟡1): the age is checked on the server.

## Future Improvements

*   **A customer "cancel my pending order" button.** Deferred on 2026-11-25. It needs a race test against a payment finishing at the same moment.
*   **Abandoned-cart reminders, a welcome image, and a configurable payment window per shop.**

## Detailed Implementation Plan

Each stage is one branch and one PR. CI (`.github/workflows/tests.yml`) must pass before the next stage starts. Stage 0 has no code and must be finished first (see [Stage 0](#stage-0-prerequisites-no-code)). The migration numbers assume no other migration lands in between.

### Target Files to Create/Modify

#### Stage 1: categories in the database

##### 1. `core/models.py` (Modify)

*   **Type / Scope**: Django models.
*   **Proposed Changes**:
    *   Add `Categoria(nome: CharField(60), ordem: PositiveIntegerField(default=0), ativo: BooleanField(default=True))`, with `Meta.ordering = ['ordem', 'nome']` and `__str__` returning `nome`.
    *   Replace `Produto.categoria` (a text field with `CATEGORIA_CHOICES`) with `categoria = ForeignKey(Categoria, on_delete=PROTECT, related_name='produtos')`.
    *   Remove `CATEGORIA_CHOICES`. The data migration keeps its own copy.
    *   **Why:** categories become shop data that the owner edits (G8).

##### 2. `core/migrations/0009_categoria.py` (Create)

*   **Type / Scope**: Schema migration.
*   **Proposed Changes**: Create `Categoria`. Add the nullable `Produto.categoria_nova` foreign key with `PROTECT`.

##### 3. `core/migrations/0010_categoria_dados.py` (Create)

*   **Type / Scope**: Data migration.
*   **Proposed Changes**:
    *   The migration holds a literal copy of today's `Produto.CATEGORIA_CHOICES` pairs, so later model changes can't alter it.
    *   Forward:
        *   for each distinct `Produto.categoria` code in use, create a `Categoria`;
        *   a known code gets its model label as the name ("Processadores", and so on) and its position in the list as `ordem`;
        *   an unknown code gets the code itself as the name and `ordem = 99`;
        *   then set `categoria_nova` on every product.

        A fresh install gets no categories.
    *   Reverse: raise `IrreversibleError` with "forward-fix only".

##### 4. `core/migrations/0011_categoria_troca.py` (Create)

*   **Type / Scope**: Schema migration.
*   **Proposed Changes**: Remove `Produto.categoria` (the text field). Rename `categoria_nova` to `categoria`. Make it required.

##### 5. `core/services.py` (Modify)

*   **Type / Scope**: Services.
*   **Proposed Changes**:
    *   Add `get_categorias_ativas()` and `get_categoria_ativa(id)` (both async; the second returns `None` for a missing or inactive category).
    *   Change `get_produtos_por_categoria(categoria_id)` to filter `categoria_id=…, categoria__ativo=True, ativo=True`, ordered by name.
    *   Change `get_todos_produtos()` to order by `categoria__ordem`, `categoria__nome`, `nome`, with `select_related('categoria')`.
    *   **Why:** this removes the hard-coded codes and keeps the sorting meaningful.

##### 6. `core/admin.py` (Modify)

*   **Type / Scope**: Django admin.
*   **Proposed Changes**:
    *   Register `CategoriaAdmin` with `list_display` (nome, ordem, ativo), `list_editable` (ordem, ativo), and no delete permission.
    *   `ProdutoAdmin.list_filter` uses `categoria`.

##### 7. `telegram_bot/constants.py` (Modify)

*   **Type / Scope**: Bot texts.
*   **Proposed Changes**:
    *   Delete `CATEGORIAS`.
    *   Add `MENSAGEM_MENU_EXPIRADO = "Este menu já não está disponível. Abre o catálogo novamente."`.

##### 8. `telegram_bot/keyboards.py` (Modify)

*   **Type / Scope**: Bot keyboards.
*   **Proposed Changes**:
    *   `teclado_catalogo(categorias)` builds one `cat_<id>` button per category it receives, with the name as the label.
    *   `teclado_produto` and `teclado_falha_registo` use `cat_{produto.categoria_id}`.
    *   The expired-menu message reuses the existing `teclado_voltar_catalogo()`; no new keyboard.

##### 9. `telegram_bot/handlers/customer.py` (Modify)

*   **Type / Scope**: Bot handlers.
*   **Proposed Changes**:
    *   `menu_catalogo` loads the active categories.
    *   `categoria` parses the value after `cat_`. If it isn't a positive integer, or `get_categoria_ativa` returns `None`, it shows `MENSAGEM_MENU_EXPIRADO` with `teclado_voltar_catalogo()`. Otherwise it lists that category's products.
    *   Every category name placed in a Markdown message goes through `md()`.
    *   **Why:** old `cat_ssd` buttons fail gracefully (G8), and owner-entered names can't break Markdown (SEC-07).

##### 10. `telegram_bot/handlers/admin.py` (Modify)

*   **Type / Scope**: Bot handlers.
*   **Proposed Changes**: `_texto_produto` shows `md(p.categoria.nome)`, and the services fetch products with `select_related('categoria')`.

##### 11. `core/tests.py` and `telegram_bot/tests.py` (Modify)

*   **Type / Scope**: Tests.
*   **Proposed Changes**:
    *   The `criar_produto` helper creates or reuses a `Categoria("Armazenamento")`.
    *   Bot test fixtures with `categoria="ssd"` use `categoria_id=1` and `categoria=SimpleNamespace(nome="Armazenamento")`.
    *   **Migration test** (forward only, in a `TransactionTestCase`): migrate to 0009 with `MigrationExecutor`, insert products in two known codes and one unknown code through the historical models, migrate to 0011, and check names, `ordem` (99 for the unknown code) and each product's category.
    *   **New tests:**
        *   `cat_<id>` lists products;
        *   `cat_ssd`, `cat_999` and an inactive category show the expired-menu message;
        *   category order follows `ordem`;
        *   a category named with `_*[` is escaped in the product list and the admin product text.

#### Stage 2: multi-line order core

##### 12. `core/models.py` (Modify)

*   **Type / Scope**: Django models.
*   **Proposed Changes**:
    *   Add `ConfirmacaoCarrinho`:
        *   `nonce`: CharField(16), unique;
        *   `utilizador`: FK to Utilizador, `CASCADE`;
        *   `criada_em`: `auto_now_add`;
        *   `revisao_carrinho`: IntegerField, null. Null only for Stage 2 one-product snapshots;
        *   `total_centimos`: PositiveIntegerField;
        *   `linhas`: JSONField, a list of `{produto_id, nome, quantidade, preco_unitario_centimos}`.

        `save()` raises an error when the row already has a pk, which makes the row immutable.
    *   Add `Encomenda.confirmacao` as a `OneToOneField(ConfirmacaoCarrinho, on_delete=PROTECT, null=True, related_name='encomenda')`.
    *   Add `Meta.constraints` on `Encomenda`: `UniqueConstraint(fields=['utilizador'], condition=Q(estado='pendente', confirmacao__isnull=False), name='uma_encomenda_pendente_por_cliente')`.

##### 13. `core/migrations/0012_confirmacao_carrinho.py` (Create)

*   **Type / Scope**: Schema migration.
*   **Proposed Changes**: Create the snapshot model, the `Encomenda.confirmacao` field and the partial unique constraint. Legacy rows have a null `confirmacao`, so the constraint can't fail on old data.

##### 14. `core/services.py` (Modify)

*   **Type / Scope**: Services.
*   **Proposed Changes**:
    *   **Constants:** `JANELA_PAGAMENTO = timedelta(minutes=60)`, `CORTE_SESSAO = timedelta(minutes=28)`, `MAX_LINHAS = 20`, `MAX_QUANTIDADE = 99`. `VALIDADE_SEGUNDOS` (1800) moves here from `telegram_bot/confirmacao.py`, which imports it back, so `core` doesn't depend on the bot package.
    *   **`_bloquear_produtos(ids)`:** `Produto.objects.select_for_update().filter(pk__in=ids).order_by('pk')`, returned as a dict by id. Every path that locks products uses it. It is also the pause point for the threaded tests.
    *   **`criar_confirmacao(utilizador, linhas, revisao_carrinho=None)`:** takes a list of `(produto_id, quantidade)`. It reads live, unlocked product data and returns `("bloqueado", flagged lines)` if any product is inactive, missing or short of stock. Otherwise it writes the snapshot with the names and unit prices in cents, and returns `("ok", snapshot)`.
    *   **`criar_encomenda_de_confirmacao(telegram_user, nonce)`** runs in one transaction:

        ```text
        snap = lock ConfirmacaoCarrinho(nonce)               -- missing → ("invalida", None)
        if snap has encomenda: owner → ("existente", it); else → ("de_outro_utilizador", None)
        if snap.utilizador != caller → ("de_outro_utilizador", None)
        if now − snap.criada_em > VALIDADE_SEGUNDOS → ("expirada", None)
        pendente = Encomenda(utilizador=caller, estado='pendente').first()   -- plain read, legacy included
        if pendente → ("pendente", pendente)
        alteracoes = []
        if snap.revisao_carrinho is not None:
            lock caller's Carrinho; if revisao != snap.revisao_carrinho → alteracoes from the cart
        produtos = _bloquear_produtos(ids in snap)
        for each snapshot line: removido / inativo / preco / stock differences → alteracoes
        if alteracoes → set_rollback; ("alterado", alteracoes)          -- nothing written
        savepoint:
            create Encomenda(estado='pendente', total=snap.total, confirmacao=snap,
                             chave_confirmacao=snap.nonce)
        IntegrityError on uma_encomenda_pendente_por_cliente → ("pendente", their pending order)
        for each line ordered by produto_id: LinhaEncomenda (nome_produto from snapshot),
            stock update with F(), MovimentoStock 'saida'
        RegistoAuditoria CRIAR_ENCOMENDA listing "qty× name @ price" for every line
        if snap.revisao_carrinho is not None: delete the cart's lines, revisao += 1, atualizado_em = now
        return ("criada", encomenda)
        ```

        There is no lock on `Utilizador` (see the lock rules). The pending-order read covers legacy orders, which the constraint excludes; the constraint covers two cart orders created at the same moment, which the read alone can't. Each change is `(produto_id, tipo, antes, depois)` with `tipo` in `preco`, `stock`, `inativo`, `removido`.
    *   **Remove `reservar_encomenda`, `reservar_encomenda_async` and `chave_substituicao`.** The order comes only from a snapshot, and replacement sessions end (D19).
    *   **`parametros_checkout(encomenda)`:** one `line_items` entry per line, ordered by `pk`, with `unit_amount = int((preco_unitario * 100).quantize(Decimal('1'), ROUND_HALF_UP))`, and `expires_at = int((data_encomenda + JANELA_PAGAMENTO).timestamp())`. A legacy line with no `nome_produto` uses the product's current name.
    *   **`garantir_sessao_pagamento`:**
        *   With no `Pagamento` and `now − data_encomenda > CORTE_SESSAO`: call `cancelar_encomenda(id, 'prazo de pagamento esgotado')` and return `("expirada", None)`. No `Session.create`.
        *   When the stored session is `expired`: call `cancelar_encomenda(id, 'sessão expirada')` and return `("expirada", None)`, instead of creating a replacement.
        *   In both cases, `PagamentoRecebido` returns `("a_confirmar", encomenda)`, and a `StripeError` propagates to the existing payment-failure handling.
        *   Everything else stays as it is: Stripe is not called under a lock, and the lock and re-check happen before saving.
    *   **`_devolver_stock(encomenda, observacoes)`:** first `_bloquear_produtos(ids of the order's lines)`, then the `F()` increments and the movements, in id order.
    *   **Reconciling cleanup (G12):**
        *   `encomendas_abandonadas(horas)` drops the `pagamento__isnull=True` filter: every pending order older than `horas`.
        *   `cancelar_se_abandonada` is replaced by `reconciliar_encomenda(id, horas)`, in one transaction:

            ```text
            encomenda = lock Encomenda(id, estado='pendente', data_encomenda < now − horas)
            if none → None                                       -- settled meanwhile
            pagamento = Pagamento.filter(encomenda=encomenda).first()
            if pagamento and pagamento.estado_pagamento in ('pago', 'reembolsado'):
                log; return "erro"                               -- inconsistent; never refund here
            try:
                resultado, _ = cancelar_encomenda(id, 'limpeza: prazo de pagamento esgotado')
                return "cancelada" if resultado == "cancelada" else None
            except PagamentoRecebido:                             -- stored session is complete
                return confirmar_pagamento(pagamento.pk, pagamento.stripe_session_id)
            ```

            *   With no payment row, `cancelar_encomenda` cancels and returns stock, as today.
            *   `PagamentoRecebido` is raised only when a payment row with a session exists, so `pagamento` is set in that branch. The exception rolls back only `cancelar_encomenda`'s savepoint; the order lock is still held.
            *   `confirmar_pagamento` re-locks the same order and payment rows in the same transaction (no wait) and returns `"confirmada"`, or `None` if the webhook already confirmed it. The command notifies the customer on `"confirmada"`.
            *   A `StripeError` or `DatabaseError` rolls back, is logged, and returns `"erro"`; the order is unchanged and retried on the next run.
    *   **New async wrappers:** `criar_confirmacao_async` and `criar_encomenda_de_confirmacao_async`.

##### 15. `telegram_bot/confirmacao.py` (Modify)

*   **Type / Scope**: Callback tokens.
*   **Proposed Changes**:
    *   The token is `confirmar_encomenda_<nonce>`: the existing prefix, so `security/sanitization.py` doesn't change in this stage. It is 36 bytes; `callback_confirmar(nonce)` keeps the 64-byte assert.
    *   `ler_callback` returns:
        *   `("snapshot", nonce)` when the value is exactly 16 lowercase hex characters;
        *   `("antigo", chave)` for the old `<nonce>_<ts>_<id>` form;
        *   `("invalido", None)` for anything else, including the pre-token `confirmar_encomenda_<id>`.
    *   `Confirmacao`, `emitida_em` and the client-side expiry check are removed; the age is checked on the server.

##### 16. `telegram_bot/handlers/customer.py` (Modify)

*   **Type / Scope**: Bot handlers.
*   **Proposed Changes**:
    *   **`encomendar` (Stage 2 only):** `criar_confirmacao(user, [(p.id, 1)])`, then shows the confirmation screen. If it returns `"bloqueado"`, the product screen is redrawn with the problem marked.
    *   **`confirmar_encomenda`** maps each result:
        *   `criada` and `existente`: `_mostrar_pagamento`;
        *   `alterado`: after the transaction, redraw the product screen (Stage 2) or the cart (Stage 3) with each change marked "⚠️ preço alterado: X → Y", "⚠️ sem stock suficiente" or "⚠️ indisponível";
        *   `pendente`: the pending-order screen;
        *   `expirada`: the expired screen;
        *   `de_outro_utilizador` and `invalida`: the invalid screen.

        The screens and their buttons are listed in Stage 3, "Result screens".
    *   **`"antigo"` tokens:** look up an existing order by `chave_confirmacao` and recover its payment. Otherwise show the expired screen.
    *   **`_mostrar_pagamento`:** lists every line (`quantidade× nome — subtotal`, names cut to 60 characters and escaped with `md()`), then the total. `"expirada"` shows the payment-deadline screen; `"a_confirmar"` keeps today's "payment being confirmed" text.

##### 17. `telegram_bot/keyboards.py` (Modify)

*   **Type / Scope**: Bot keyboards.
*   **Proposed Changes**:
    *   `teclado_confirmar_encomenda(callback_confirmar, voltar)`, where `voltar` sends the customer back to the product (Stage 2) or the cart (Stage 3).
    *   One keyboard per result screen, matching the table in Stage 3.

##### 18. `core/admin.py` (Modify)

*   **Type / Scope**: Django admin.
*   **Proposed Changes**:
    *   The `EncomendaAdmin` detail view shows `confirmacao` read-only. No separate admin page for snapshots.
    *   `ativar_produtos` and `desativar_produtos` run in `transaction.atomic()` and lock with `select_for_update().filter(pk__in=…).order_by('pk')` before the `update()`, following the lock rules.

##### 19. `core/management/commands/cancelar_encomendas_abandonadas.py` (Modify)

*   **Type / Scope**: Management command.
*   **Proposed Changes**: Call `reconciliar_encomenda` for each order from `encomendas_abandonadas`. Notify confirmed orders through `notificar_estado` and cancelled ones as today. Print one summary line: confirmed, cancelled, errors. `--horas` keeps its default and its check.

##### 20. `core/tests.py` (Modify)

*   **Type / Scope**: Tests.
*   **Proposed Changes**:
    *   **Replace the `reservar_encomenda` calls** with a helper `encomenda_teste(user, [(produto, qty)])` that builds a snapshot and confirms it. This covers:
        *   the payment-session tests S1–S22;
        *   the order-creation tests db1–db4;
        *   the end-to-end tests E1 and E2;
        *   the expiry tests X1–X8;
        *   the cancellation tests C*.
    *   **`ConcurrentCheckoutTests.correr_em_paralelo`** patches `services._bloquear_produtos` as its pause point, instead of `_encomenda_por_chave`.
    *   **Remove or rewrite** the tests that assume replacement sessions (D19): S9–S14, S19 and S22 become "expired session → order cancelled, stock returned, no new session". Test db4 becomes "two screens → second Confirm returns `pendente`".
    *   **New tests:**
        *   multi-line order: lines, stock and audit;
        *   `alterado` for each change type and for a changed cart revision, with nothing written and the change list returned;
        *   a snapshot used twice returns `existente`;
        *   a customer with a legacy pending order (`confirmacao` null) gets `pendente` on Confirm, with no order, stock change or cart change; after that order is cancelled, the same snapshot creates the order if it is still valid;
        *   another user's snapshot is refused;
        *   a snapshot older than 30 minutes by `criada_em` returns `expirada`;
        *   `parametros_checkout` line order, rounding and `expires_at`;
        *   session cutoff at 27 and 28 minutes creates a session; at 29 minutes there is no `Session.create`, the order is `cancelada` and the result is `"expirada"` (patched clock);
        *   retry with the same key on each side of the cutoff;
        *   expired stored session → cancelled, no `Session.create`;
        *   `PagamentoRecebido` during the cutoff cancel returns `"a_confirmar"`;
        *   `_devolver_stock` returns every line;
        *   `ativar_produtos` and `desativar_produtos` lock in id order.
    *   **Reconciling cleanup tests:** no payment → cancelled; session `complete` → `confirmar_pagamento` called with the order's payment id and stored session id, order confirmed and customer notified; session `complete` but the webhook already confirmed it → `None`, no second notice; a pending order whose payment is already `pago` → `"erro"`, no refund; `open` → expired and cancelled; Stripe error → unchanged; a legacy order and a snapshot order are both handled; a second run changes nothing.
    *   **Threaded tests:**
        *   **T6:** two checkouts with products {A,B} and {B,A}. Both orders are created, or one returns `"alterado"`. Stock = start − created quantities, and no `DeadlockDetected`.
        *   **T7:** a checkout racing a cancellation that returns stock for shared products. Both finish and the stock reflects both.
        *   **T8:** two Confirm taps from two different snapshots of the same customer. One `criada` and one `pendente`.
        *   **T9:** an admin cancel of the customer's paid order with product P, racing the same customer's checkout with P. Both finish, with no deadlock.

##### 21. `telegram_bot/tests.py` (Modify)

*   **Type / Scope**: Tests.
*   **Proposed Changes**: Adapt the checkout handler tests H1–H14 to the new token and results. Add tests for each result screen and its buttons, for `"antigo"` and pre-token buttons, and for the multi-line payment screen staying under 4096 characters at 20 lines.

#### Stage 3: the cart

##### 22. `core/models.py` (Modify)

*   **Type / Scope**: Django models.
*   **Proposed Changes**:
    *   Add `Carrinho(utilizador: OneToOneField(Utilizador, CASCADE), revisao: IntegerField(default=0), atualizado_em: DateTimeField(default=now))`.
    *   Add `LinhaCarrinho(carrinho: FK CASCADE related_name='linhas', produto: FK(Produto, CASCADE), quantidade: PositiveIntegerField, preco_ao_adicionar: Decimal(10,2))`, with:
        *   `UniqueConstraint(fields=['carrinho', 'produto'], name='uma_linha_por_produto')`;
        *   `CheckConstraint(condition=Q(quantidade__gt=0), name='quantidade_positiva')`.

##### 23. `core/migrations/0013_carrinho.py` (Create)

*   **Type / Scope**: Schema migration.
*   **Proposed Changes**: Create both cart models and their constraints. This only adds tables.

##### 24. `core/services.py` (Modify)

*   **Type / Scope**: Services.
*   **Proposed Changes**: The services and results in the Stage 3 table:
    *   **`_carrinho_bloqueado(utilizador)`:** get or create the cart, then `select_for_update`.
    *   **`adicionar_ao_carrinho`:** refuses an inactive or out-of-stock product, and a new line beyond `MAX_LINHAS`; adds 1, up to the lower of `MAX_QUANTIDADE` and the current stock; sets `preco_ao_adicionar` to the current price.
    *   **`alterar_quantidade`:** same limits. Reaching 0 removes the line. Any change refreshes `preco_ao_adicionar`.
    *   **`remover_do_carrinho`**.
    *   **`ver_carrinho`:** no lock and no write. Flags `preco_alterado` (live price ≠ `preco_ao_adicionar`), `stock_insuficiente`, `indisponivel` and `removido`.
    *   **`preparar_confirmacao_carrinho`:** locks the cart, reads `revisao`, then calls `criar_confirmacao(user, lines, revisao)`. An empty cart returns `"vazio"`.
    *   **`pode_repetir(encomenda, user)`:** read-only. True for the customer's own `cancelada` order with either no `Pagamento` or one not in (`pago`, `reembolsado`).
    *   **`repetir_encomenda`:** checks `pode_repetir`, locks the cart and merges at most 20 lines, each capped by stock and `MAX_QUANTIDADE`, skipping inactive products. It returns lists of what was added, reduced and skipped.

    Each changing service increases `revisao` and sets `atualizado_em`.

##### 25. `telegram_bot/handlers/customer.py` (Modify)

*   **Type / Scope**: Bot handlers.
*   **Proposed Changes**:
    *   **New handlers:** `carrinho_ver`, `carrinho_add`, `carrinho_mais`, `carrinho_menos`, `carrinho_remover`, `carrinho_finalizar` and `repetir_encomenda`.
    *   **`encomendar`** is replaced by `carrinho_add`. The `encomendar_` prefix stays routed, to `carrinho_add`.
    *   **The confirmation screen** is drawn from the snapshot, with "Confirmar" and "Voltar ao carrinho".
    *   **`alterado` and `bloqueado`** show the cart with the flags.
    *   **`encomenda_detalhe`** shows "🔁 Repetir encomenda" when `pode_repetir` is true.
    *   **Routes** for the new prefixes and the exact routes `carrinho_ver` and `carrinho_finalizar`.

##### 26. `telegram_bot/keyboards.py` (Modify)

*   **Type / Scope**: Bot keyboards.
*   **Proposed Changes**:
    *   `teclado_carrinho(linhas)` has + / − / 🗑 per line, "Finalizar compra" (hidden while a line is flagged `stock_insuficiente`, `indisponivel` or `removido`) and "Continuar a comprar".
    *   `teclado_produto` uses `carrinho_add_<id>`.
    *   `teclado_menu_principal` adds "🛒 Carrinho" (no count; its signature doesn't change in this stage).
    *   Add `teclado_repetir(encomenda_id)`.

##### 27. `telegram_bot/constants.py` (Modify)

*   **Type / Scope**: Bot texts.
*   **Proposed Changes**: Add the cart texts in pt-PT:
    *   empty cart;
    *   each limit reached;
    *   flagged-line labels;
    *   the result screens;
    *   the "Repeat order" result, listing reduced and skipped items.

##### 28. `security/sanitization.py` (Modify)

*   **Type / Scope**: Callback allowlist.
*   **Proposed Changes**:
    *   Add the prefixes `carrinho_add_`, `carrinho_mais_`, `carrinho_menos_`, `carrinho_remover_` and `repetir_encomenda_`.
    *   Add the exact values `carrinho_ver` and `carrinho_finalizar`.
    *   Keep `encomendar_`; old buttons add the product to the cart.

##### 29. `core/management/commands/cancelar_encomendas_abandonadas.py` (Modify)

*   **Type / Scope**: Management command.
*   **Proposed Changes**: After reconciling, the same run:
    *   deletes `ConfirmacaoCarrinho` rows with no order (`encomenda__isnull=True`) and `criada_em` more than 45 minutes ago;
    *   deletes `Carrinho` rows whose `atualizado_em` is more than 30 days ago (their lines cascade).

    Both counts join the summary line. There is no separate cleanup command.

##### 30. `core/tests.py` and `telegram_bot/tests.py` (Modify)

*   **Type / Scope**: Tests.
*   **Proposed Changes**:
    *   **Cart service tests:**
        *   each add limit;
        *   minus to 0 removes the line;
        *   each flag, and which flags block checkout;
        *   viewing doesn't change `revisao` or `atualizado_em`;
        *   Confirm empties only that cart, and a cart changed after the screen was drawn gives `alterado`.
    *   **"Repeat order" tests:**
        *   merge with an existing line, capped;
        *   at most 20 lines, with the rest reported as skipped;
        *   inactive and out-of-stock products skipped;
        *   `pode_repetir` false for paid, refunded, pending or another user's order.
    *   **Cleanup tests:** snapshots at 44 and 46 minutes; a linked snapshot older than 45 minutes is kept; carts at 29 and 31 days; viewing a cart doesn't refresh its timer.
    *   **Routing tests:** every new callback, and `encomendar_<id>`, passes `validar_callback` and reaches its handler, extending the SEC-12 test.
    *   **Message-size test** for the cart screen at 20 lines.

#### Stage 4: shop setup, admin rights and handover

##### 31. `core/models.py` (Modify)

*   **Type / Scope**: Django models.
*   **Proposed Changes**:
    *   Add `Loja(nome: CharField(80, default="Loja"), texto_boas_vindas: CharField(max_length=1000, default=<neutral pt-PT text>))`, whose `save()` forces `pk=1`.
    *   Add `Utilizador.e_admin = BooleanField(default=False)`.
    *   Add `Encomenda.Meta.permissions = [('cancelar_encomenda', 'Pode cancelar e reembolsar encomendas')]`.

##### 32. `core/migrations/0014_loja_e_admin.py` (Create)

*   **Type / Scope**: Schema migration.
*   **Proposed Changes**: Create `Loja`, add `Utilizador.e_admin`, and add the `Encomenda` permission.

##### 33. `core/migrations/0015_grupo_dono_loja.py` (Create)

*   **Type / Scope**: Data migration.
*   **Proposed Changes**:
    *   Depends on 0014 and on the latest `auth` and `contenttypes` migrations.
    *   Forward: call `create_permissions(apps.get_app_config('core'), verbosity=0)` and the same for `auth`, then create the group "Dono da loja" with the permissions from Stage 4, looked up by codename.
    *   Reverse: delete the group.

##### 34. `core/services.py` (Modify)

*   **Type / Scope**: Services.
*   **Proposed Changes**:
    *   Add `obter_loja()`, which does `get_or_create(pk=1)`, plus `obter_loja_async()`.
    *   Add `async e_admin(user_id)`, true when `user_id` is in `ADMIN_TELEGRAM_IDS` (bootstrap, parsed once) or `await Utilizador.objects.filter(telegram_id=str(user_id), e_admin=True, ativo=True).aexists()`.

##### 35. `telegram_bot/guards.py` (Modify)

*   **Type / Scope**: Bot guards.
*   **Proposed Changes**: Remove `ADMIN_IDS` and `is_admin`. Callers use `await services.e_admin(user_id)`.

##### 36. `telegram_bot/handlers/callbacks.py`, `telegram_bot/handlers/commands.py`, `telegram_bot/handlers/customer.py`, `telegram_bot/keyboards.py` (Modify)

*   **Type / Scope**: Bot handlers and keyboards.
*   **Proposed Changes**:
    *   `callback_handler` awaits `e_admin` for routes marked admin-only.
    *   `teclado_menu_principal(user_id, admin)` receives the admin flag instead of calling `is_admin`. Its callers pass `await e_admin(user_id)`: `commands.start`, `customer.menu_inicio`, and every helper that returns to the main menu.
    *   `/start` uses `obter_loja_async()` for the name and welcome text, both escaped with `md()`.
    *   `/admin` and `admin_menu` use the shop name instead of "Jarvis".
    *   `keyboards.py` no longer imports `is_admin`.

##### 37. `telegram_bot/handlers/admin.py` (Modify)

*   **Type / Scope**: Bot handlers.
*   **Proposed Changes**: The admin menu title uses `md((await obter_loja_async()).nome)`.

##### 38. `security/decorators.py` (Modify)

*   **Type / Scope**: Security helpers.
*   **Proposed Changes**: Delete the unused `ADMIN_IDS` and `admin_only`. There is only one admin source now.

##### 39. `core/admin.py` (Modify)

*   **Type / Scope**: Django admin.
*   **Proposed Changes**:
    *   `LojaAdmin`: no add or delete; the change list redirects to the single row.
    *   `UtilizadorAdmin.get_readonly_fields` adds `e_admin` unless `request.user.is_superuser`, and `list_display` shows `e_admin`.
    *   `EncomendaAdmin` actions:
        *   `marcar_cancelada` declares `allowed_permissions=('cancelar',)`, and `has_cancelar_permission(request)` returns `request.user.has_perm('core.cancelar_encomenda')`;
        *   `marcar_enviada` and `marcar_entregue` declare `allowed_permissions=('change',)`.

##### 40. `config/settings.py` and `.env.example` (Modify)

*   **Type / Scope**: Settings.
*   **Proposed Changes**:
    *   When `DJANGO_HTTPS=True`, set:
        *   `SECURE_SSL_REDIRECT`;
        *   `SESSION_COOKIE_SECURE` and `CSRF_COOKIE_SECURE`;
        *   `SECURE_HSTS_SECONDS=31536000`;
        *   `SECURE_PROXY_SSL_HEADER`, only if `DJANGO_BEHIND_PROXY=True`.
    *   Add `CSRF_TRUSTED_ORIGINS` from a comma-separated environment variable.
    *   `.env.example` documents the three variables.
    *   **Why:** this finishes SEC-01 before an owner gets a Django login (G9).

##### 41. `core/tests.py` and `telegram_bot/tests.py` (Modify)

*   **Type / Scope**: Tests.
*   **Proposed Changes**:
    *   `obter_loja` defaults on an empty database.
    *   `/start` shows the configured name and text, escaped; a welcome text with `_*[` renders.
    *   `e_admin` is true from the bootstrap list and from the database flag. Turning the flag off takes effect on the next check.
    *   Tests that patched `guards.is_admin` patch `services.e_admin` instead.
    *   A non-superuser staff user can't change `e_admin`.
    *   The "Dono da loja" group has exactly the listed permissions.
    *   **A8:** an owner in the group who posts the `marcar_cancelada` action gets a permission error, and the order is unchanged. The same for `marcar_enviada`.
    *   Settings with `DJANGO_HTTPS=True`.

#### Scheduled job

##### 42. `deploy/crontab.example` (Create)

*   **Type / Scope**: Deployment example.
*   **Proposed Changes**: One entry, every 5 minutes: `manage.py cancelar_encomendas_abandonadas --horas 1`, run with the project's virtualenv and `.env`, appending to the log.

#### Documentation

##### 43. `DEPLOYMENT.md` (Create)

*   **Type / Scope**: Reseller guide.
*   **Proposed Changes**:
    *   one copy per shop;
    *   BotFather setup, including the profile photo;
    *   Stripe keys and webhook endpoint, with test mode first;
    *   `.env`;
    *   `migrate`;
    *   creating the superuser;
    *   entering `Loja` and categories;
    *   the owner's staff account in "Dono da loja";
    *   marking Telegram admins;
    *   **handover:** once the owner's Telegram user is flagged, set `ADMIN_TELEGRAM_IDS` to the reseller's id only (or empty) and restart the bot once;
    *   installing the cron entry, then the **setup check:** run the command once by hand and confirm a second run's summary line appears in the log within 5 minutes;
    *   HTTPS settings;
    *   a database backup before every migration, and how to restore it;
    *   the deploy order for an existing copy: backup, stop the bot, `migrate`, start the bot;
    *   a checklist to confirm the webhook works before taking real payments;
    *   watching the cleanup: check the log daily, and investigate when the error count repeats across runs or the summary lines stop, since stock stays held until the cleanup succeeds.

##### 44. `README.md`, `ARCHITECTURE.md`, `SECURITY.md`, `ROADMAP.md`, `PROJECT_GUIDANCE.md` (Modify)

*   **Type / Scope**: Project docs.
*   **Proposed Changes**:
    *   `README.md`: features and a link to `DEPLOYMENT.md`.
    *   `ARCHITECTURE.md`: the order flow (cart → snapshot → order), new tables, lock rules, the reconciling cleanup, and the removal of D19 replacement.
    *   `SECURITY.md`: a new entry for cart abuse limits, snapshot tokens, one pending order per customer, admin rights in the database, and the server-side admin action permissions.
    *   `ROADMAP.md`: Stages 1–4 as rows.
    *   `PROJECT_GUIDANCE.md`:
        *   the cart decisions, including the review decisions A, B and C of 2026-11-25;
        *   retention: snapshots after 45 minutes, carts after 30 days, order-linked snapshots kept;
        *   the 60-minute window;
        *   the Stage 0 outcome.
    *   **Why:** the docs are part of what is sold.

##### 45. Background documents (Removed)

*   **Type / Scope**: The brainstorm, review and draft reports.
*   **Proposed Changes**: Removed on 2026-11-27 before the repository went public; they remain in the git history. The final design lives under `docs/designs/`.

### Verification Plan

*   **Compilation/Build**: `python manage.py check`; `python manage.py makemigrations --check --dry-run` (no drift after each stage).
*   **Unit Tests**: `python manage.py test core telegram_bot --noinput` on PostgreSQL 17, locally and in CI. Every existing SEC test either passes unchanged or is rewritten as listed under item 20.
*   **Concurrency**: the threaded tests T1–T5, plus the new T6–T9. Each new lock test gets a mutation check:
    *   remove the `order_by('pk')` in `_bloquear_produtos` → T6 fails;
    *   remove the `uma_encomenda_pendente_por_cliente` constraint → T8 fails.
*   **Static Analysis & Linting**: none is configured in the project. Not added by this design.
*   **Test Coverage Requirement**: no percentage target. Every result branch of `criar_encomenda_de_confirmacao`, `garantir_sessao_pagamento`, `reconciliar_encomenda`, each cart service and each admin action permission has at least one test.
*   **E2E/Integration (G13)**: a manual run on a separate demo database, in Stripe test mode with `stripe listen` forwarding to the webhook:
    *   a two-product cart, paid, and the order confirmed;
    *   a price change shown on the confirmation screen;
    *   a paid order cancelled from the Telegram admin, then refunded;
    *   an unpaid order left for 60 minutes (Stripe doesn't accept `expires_at` under 30 minutes, so the wait can't be shortened), reconciled by the cleanup or the expiry webhook, then "Repeat order";
    *   a final check: stock = initial − paid quantities, and every order is paid and confirmed, or cancelled with its stock returned and any payment refunded.

    The results are recorded in `PROJECT_GUIDANCE.md`.

--------------------------------------------------------------------------------

*Drafted with EGM*
