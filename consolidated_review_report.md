# Consolidated Review: Multi-product cart and resellable single-shop setup

*   **Target:** [docs/designs/cart_resellable_shop.md](docs/designs/cart_resellable_shop.md), 874 lines. FeatureRange is lines 1–426; DetailedRange is lines 427–874.
*   **Mode:** consolidated, uninterrupted. All five stages ran in order, and the document was not edited between stages.
*   **GoldfishContext:**
    *   the brief and the earlier Goldfish report;
    *   `ARCHITECTURE.md`, `SECURITY.md`, `PROJECT_GUIDANCE.md`;
    *   `core/services.py`, `core/models.py`, `core/tests.py`;
    *   `telegram_bot/handlers/customer.py`, `telegram_bot/confirmacao.py`;
    *   `security/sanitization.py`.

    Stage 6 read the document only.
*   **Stage reports:**

| Stage | Reviewers | Report | Result |
| --- | --- | --- | --- |
| 5 Initial zoom-out | zoom-out, inventive-catalyst | [zoom_out_report.md](zoom_out_report.md) | 0 B, 8 M, 7 N |
| 6 Comprehension | comprehension | [comprehension_report.md](comprehension_report.md) | 9 M, 9 N |
| 7 Critic (FeatureRange) | design, structural, style | [critic_report.md](critic_report.md) | 2 B, 15 M, 22 N |
| 8 Intermediate zoom-out | zoom-out | [intermediate_zoom_out_report.md](intermediate_zoom_out_report.md) | 2 B, 7 M, 6 N |
| 9 Implementation readiness (DetailedRange) | implementation-readiness, codebase-details, style | [implementation_readiness_report.md](implementation_readiness_report.md) | 4 B, 22 M, 18 N |

**Verdict: not ready to implement.** The architecture survived the review: cart as a draft, an immutable snapshot, an order built from the snapshot, products locked in id order, one shop per copy. The failures are in how it is specified:
*   a lock cycle;
*   an `"alterado"` result with no consistent shape;
*   a stock hold that is not hard when the webhook is lost;
*   a switch-over heavier than the situation needs;
*   snapshot storage that crashes on `Decimal`;
*   gaps in the admin permissions and the test plan.

Stage 8 found that several remedies from earlier stages contradict each other. Where they do, this report adopts the simpler resolution and says so.

**Not verified by running anything.** The lock-cycle and admin-permission findings rest on PostgreSQL and Django behaviour that no reviewer reproduced. Each needs a threaded or admin test before it counts as proven. Stripe's 30-minute minimum for `expires_at` was checked in an earlier session, not by these reviewers.

## Decisions for the author

These change settled decisions or have more than one defensible answer:

1.  **D-A, the switch-over (changes G12 and CUJ 5).** See item 🔴1. Either keep the drain command and startup gate as designed, or replace both with one cleanup sweep that cancels every pending order older than 60 minutes, with or without a payment link.
2.  **D-B, customer self-service (G6 and A10).** See item 🟡6. Either keep "Repeat order" only, add a customer "cancel my pending order" button beside it, or defer both.
3.  **D-C, shop-owner rights.** See item 🟡3.
    *   May the owner **add** products and categories? The Problem statement says yes; the group grants change and view only.
    *   May the owner **cancel and refund** orders from Django admin? Today the actions have no permission check.

## Consolidated actions

### 🔴 Blockers

1.  **🔴1. Stock release and switch-over: one sweep instead of a drain command and a startup gate.**
    *   **Problem:**
        *   **The hold isn't hard.** "Hard 60 minutes" holds only if Stripe's expiry webhook arrives or the customer comes back: the cleanup command filters `pagamento__isnull=True` (`core/services.py:460`).
        *   **Stock is held after the cutoff.** Orders without a link are dead at 28 minutes but keep their stock until 60–65 minutes.
        *   **The startup gate keeps the bot down.** It does so while any paid legacy order waits for its webhook.
        *   **The jobs belong to no stage.** Items 41–44 are in no stage's pull request.
        *   **The webhook's process is never named.** The design doesn't say which process receives the webhook during a switch-over.
    *   **Proposed fix (needs D-A):**
        *   Remove the `pagamento__isnull=True` filter, so the 5-minute sweep (`--horas 1`) runs every pending order older than 60 minutes through `cancelar_encomenda`, which already handles every payment state under the order lock. That makes the hold hard: at most 65 minutes.
        *   Legacy orders are older than 60 minutes after any normal deploy, so the drain command, the startup gate, the legacy token parsing and G12 can all be removed.
        *   Ship the sweep change with Stage 2.
        *   State that the webhook runs in the Django web process, separately from `bot.py`.
    *   **Cost:** orders without a link hold stock up to 65 minutes instead of about 35.
2.  **🔴2. A lock cycle through the customer row.**
    *   **Problem:** checkout locks `Utilizador` `FOR UPDATE` while it holds product locks. `cancelar_encomenda` holds product locks, then inserts an audit row, whose foreign-key check takes a share lock on the same `Utilizador`. An admin cancel racing the same customer's checkout can deadlock, possibly after the Stripe refund was sent.
    *   **Fix:**
        *   Take no lock on `Utilizador`. One pending order per customer is enforced by the partial unique constraint, with the `IntegrityError` caught inside a savepoint (the existing pattern) and mapped to `"pendente"`.
        *   The `Carrinho` row stays the only per-customer lock.
        *   Add a threaded test: an admin cancel against the same customer's checkout with an overlapping product.
        *   Drop the user-lock mutation check (it can't fail while the constraint exists), and mutation-test the constraint instead.
    *   **Rejected remedies:** `select_for_update(no_key=True)`, and moving the cart onto `Utilizador`.
3.  **🔴3. `"alterado"` has no consistent shape.**
    *   **Problem:** it promises a new snapshot, but `criar_confirmacao` refuses one for the main case, an out-of-stock line in CUJ 2. Building one would also write while product locks are held. Nothing defines where the redraw's old price comes from, or what happens to a product deleted since the screen was drawn.
    *   **Fix:** `"alterado"` writes nothing. It rolls back and returns a list of changes: product id, kind (`preco`, `stock`, `inativo`, `removido`), old value and new value. After the commit, the handler shows the cart screen with those flags. Define the return contracts of the cart services and which flags block checkout (stock and inactive block; price changes don't).
4.  **🔴4. The snapshot JSON can't hold `Decimal` prices.**
    *   **Problem:** the default encoder raises `TypeError` on `Decimal`.
    *   **Fix:** store prices as integer cents (or decimal strings) in `linhas` and `total`, and convert back on read.
5.  **🔴5. The migration test can't run.**
    *   **Problem:** 0010's reverse raises `IrreversibleError`, and the test plans to migrate back to 0009.
    *   **Fix:** test the data migration forward only, from a database migrated to 0009 in a `TransactionTestCase`, or make 0010's reverse a no-op that the test uses.
6.  **🔴6. The required Risks & Mitigations section is missing.**
    *   **Fix:** add about 8 short rows, at most:
        *   Stage 0 failure;
        *   lock cycle;
        *   lost webhook;
        *   Stripe key reuse on deploy;
        *   stock hoarding;
        *   owner rights;
        *   Markdown injection;
        *   forward-only migrations.

### 🟡 Majors

1.  **🟡1. The button timestamp is client data.**
    *   **Fix:** check the snapshot's `criada_em` on the server instead.
    *   Also: keep the existing `confirmar_encomenda_` prefix and carry only the nonce, so no new allowlist entry or legacy parsing is needed. Use the real constant names.
2.  **🟡2. Old "Encomendar" buttons.** Removing `encomendar_` from the allowlist makes them show the generic invalid-request alert, not the expired-menu message.
    *   **Fix:** keep the prefix and route it to "add to cart".
3.  **🟡3. Shop-owner admin rights (needs D-C).**
    *   **Problem:** the group has change and view only, but the Problem statement says the owner can add categories. The order admin actions (cancel with refund, ship, deliver) declare no `allowed_permissions`, so a view-only owner can still run them.
    *   **Fix:** either grant add rights, or reword the Problem statement. Set `allowed_permissions = ('change',)` on the order actions, or reword G9 to say the owner can cancel.
4.  **🟡4. The permission data migration runs before the permissions exist.**
    *   **Fix:** create the group in a `post_migrate` handler, or call `create_permissions` inside 0015 before assigning permissions. Depend on the latest `auth` and `contenttypes` migrations.
5.  **🟡5. Admins listed in `.env` can't be removed through the database.**
    *   **Fix:** add a handover step to `DEPLOYMENT.md`: after the owner's admin flag is set, clear `ADMIN_TELEGRAM_IDS` to the reseller only, or empty it.
6.  **🟡6. Customer recovery features (needs D-B).**
    *   **Problem:** "Repeat order" ignores the 20-line cap and lets one account hold scarce stock again and again. The "Repetir" button depends on a service that changes state. With no customer cancel, a mistaken order blocks the customer for up to an hour.
    *   **Fix:**
        *   Cap "Repeat order" at 20 lines.
        *   Decide the button's visibility with a read-only check.
        *   Choose under D-B.
7.  **🟡7. Owner-entered text isn't escaped for Markdown.**
    *   **Problem:** a `_` or `*` in the welcome text or a category name makes Telegram reject the message.
    *   **Fix:** send the welcome text with `parse_mode=None`, or pass it through `md()`, and escape category names.
8.  **🟡8. Signature changes and test patches.**
    *   **Problem:** changing `teclado_menu_principal`'s signature misses callers. The admin check becoming async breaks the tests that patch `is_admin`.
    *   **Fix:** list every caller, and change the signature in the same stage as the async admin check (Stage 4). Until then, keep the cart count out of the menu (see 🟢).
9.  **🟡9. The test plan is incomplete.**
    *   **Problem:**
        *   the list of tests to rewrite misses some;
        *   the tests ignore the one-pending-order rule;
        *   the threaded tests lose their pause point (they patched `_encomenda_por_chave`) and name no replacement;
        *   T6 and T7 have no checkable result;
        *   the cutoff retry test has no expected value;
        *   G13's "consistent" isn't measurable.
    *   **Fix:**
        *   Name a pause hook, for example a patched `_bloquear_produtos`.
        *   State the expected end state of each test.
        *   Define G13 as stock at the end = starting stock − paid quantities, with every order paid-and-confirmed or cancelled-with-stock-returned.
10. **🟡10. `cancelar_encomenda` calls Stripe while holding locks.**
    *   **Problem:** `garantir_sessao_pagamento` doesn't handle its exceptions, and the "Stripe is never called under a lock" claim is wrong: `cancelar_encomenda` calls Stripe while holding the order lock (by design, SEC-06).
    *   **Fix:** handle `PagamentoRecebido` and Stripe errors in the cutoff and expired paths, and correct the claim.
11. **🟡11. The admin bulk product actions lock rows in no fixed order.**
    *   **Fix:** order the bulk activate and deactivate updates by id, or state that `update()` on a queryset takes row locks in an undefined order, and list it as a risk.
12. **🟡12. The document can't be read on its own.**
    *   **Fix:**
        *   Define D19 at first use.
        *   Add a short SEC glossary or anchored links.
        *   Explain 28 minutes once: Stripe's 30-minute minimum minus a 2-minute margin.
        *   Explain that `chave_primeira_tentativa` is built from `chave_confirmacao`.
        *   Give one Stage 0 list, with "blocks the plan: yes/no" for each item and course rights defined.
        *   Close Q2, which is already decided.
        *   Give Stage 0 its own section.
13. **🟡13. Unsourced attribution and claims.**
    *   **Problem:** the rejections are credited to "author" with no link, and the legal claim has no source.
    *   **Fix:** link each rejection to the brief section, and mark the legal statements as unverified.
14. **🟡14. Undefined screens and overstated claims.**
    *   **Problem:** the expired, pending, failure and invalid screens have no defined keyboards for snapshot tokens, and several terms overstate.
    *   **Fix:** list each screen's buttons and tighten the wording.

### 🟢 Nits

*   **Repetition and code level:**
    *   remove the duplication between the Technical Plan and the Detailed Plan, which has already drifted;
    *   move pseudocode at code level to a plain-English description, or keep it labelled as pseudocode.
*   **Wording:**
    *   `"invalida"` is missing from the Stage 2 result list;
    *   one name for `preparar_confirmacao`;
    *   "age ≤ 30 min" should say `criada_em`;
    *   "Finalizar compra" is hidden only for stock and inactive flags;
    *   G5 still says links are "replaced";
    *   G10's configuration check already exists;
    *   remove the "Step 6" reference;
    *   say three settings variables, not two;
    *   Stripe's minimum means the expiry test copy can't go below 30 minutes;
    *   narrow "a broken webhook blocks the start".
*   **Django 6:**
    *   `CheckConstraint(condition=…, name=…)`;
    *   `UniqueConstraint` needs `fields` and `name`;
    *   `TextField(max_length)` is enforced only in forms;
    *   copy the `Produto.CATEGORIA_CHOICES` pairs into 0010 instead of importing them, define `ordem` for unknown codes, and choose the model labels or the emoji labels.
*   **Reuse:**
    *   use `teclado_voltar_catalogo()` instead of a new keyboard;
    *   add `get_categoria_ativa(id)`;
    *   put `encomendas_antigas_pendentes` beside `encomendas_abandonadas`, if the startup check survives D-A;
    *   say whether `e_admin` uses `aexists()`, and name the async wrapper for `obter_loja()`.
*   **Smaller scope:**
    *   fold `limpar_rascunhos` into the existing cron command;
    *   drop the snapshot `moeda` field, the cart count on the menu button, and the separate snapshot admin page;
    *   make `revisao_carrinho` required once Stage 3 lands.
*   **Format:**
    *   apply "Type / Scope" to every item or to none;
    *   add a glossary of Portuguese identifiers;
    *   describe the test-id series S, H, db and T.

## Confirmed sound

The reviewers agreed the following holds:
*   one copy per shop;
*   the cart as a draft with a separate snapshot;
*   locking products in one id-ordered query;
*   the hard deadline from order creation with a 28-minute session cutoff;
*   card-only payments meaning "completed means paid";
*   unchanged webhook handlers;
*   category ids with an expired-menu fallback;
*   the staging order, Categoria first.

## Traceability matrix

| Action | Stage 5 | Stage 6 | Stage 7 | Stage 8 | Stage 9 |
| --- | --- | --- | --- | --- | --- |
| 🔴1 sweep instead of drain and gate | M1, M3, M4 | 4, 7 | M1, M9 | B1 | B4, N15 |
| 🔴2 lock cycle on customer row | M5, M6 | — | B1 | B2 | M1, M3 |
| 🔴3 "alterado" shape | M2 | 6 | M2, M3 | M1 | B1, M5, M8, M9 |
| 🔴4 Decimal in JSON | — | — | — | — | B2 |
| 🔴5 migration test | — | — | — | — | B3 |
| 🔴6 Risks section | — | — | B2 | M7 | — |
| 🟡1 button timestamp | M7 | 11 | M4 | M2 | M6, N7 |
| 🟡2 old Encomendar buttons | N3 | 3 | — | M3 | M7 |
| 🟡3 owner rights | — | — | M5 | M5 | M14, M16 |
| 🟡4 permission migration order | — | — | — | — | M15 |
| 🟡5 `.env` admins | — | — | M6 | M6 | — |
| 🟡6 customer recovery | M8 | 15 | M7 | M4 | M10, M11 |
| 🟡7 Markdown escaping | — | — | M8 | N2 | M13 |
| 🟡8 signatures and patches | — | — | — | — | M12, M20 |
| 🟡9 test plan | M5 | 16 | M14 | — | M18, M19, M21 |
| 🟡10 Stripe under lock | N5 | — | M13 | — | M4 |
| 🟡11 bulk product locks | — | — | — | — | M2 |
| 🟡12 self-containment | — | 1, 2, 5, 8, 9 | M10, M11 | N6 | — |
| 🟡13 attribution and legal | — | — | M12, M15 | — | — |
| 🟡14 screens and claims | — | — | — | — | M17, M22 |
| 🟢 nits | N1, N2, N4, N6, N7 | 10–14, 17, 18 | nits | N1, N3–N5 | nits 1–18 |

## Coverage gaps

*   Nobody ran code. 🔴2, 🟡3 and 🟡4 rest on framework behaviour and need tests to confirm.
*   No reviewer read `core/admin.py` in full, `telegram_bot/tests.py`, or `core/views.py`.
*   Stage 9's code reviewers did not fully read `goldfish_cart_report.md`, `ARCHITECTURE.md` or `PROJECT_GUIDANCE.md`.
*   Stage 9 hit a usage limit and was resumed. All three of its reviewers completed on retry.
