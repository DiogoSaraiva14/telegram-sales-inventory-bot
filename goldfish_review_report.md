# Goldfish Annotated Review Report

**Status:** resolved (2026-11-25, auto accept). All 20 issues and every PROPOSE FIX nit applied to the design; the two DECLINE nits left unchanged. Accepted: 20 issues plus the nits; contested: 0; escalated: 0; declined: 2.

**Target:** `docs/designs/cart_resellable_shop.md` (full file, lines 1–874)
**Source findings:** [consolidated_review_report.md](consolidated_review_report.md) (stages 5–9). The item ids below match that report.
**Author decisions applied (2026-11-25):**
*   **A:** the scheduled cleanup replaces the drain command and the startup block, and it reconciles each order instead of cancelling blindly.
*   **B:** keep "Repeat order"; defer customer self-cancel.
*   **C:** owners may add, edit and deactivate products and categories, and may not cancel or refund in Django admin. This is enforced on the server.

**Summary:** 20 PROPOSE FIX, 0 CONTEST, 0 ESCALATE. The nits are grouped at the end: most get PROPOSE FIX, and 2 get DECLINE.

## Prioritized Findings & AI Suggestions

### [Issue 1] [`zoom-out, comprehension, design, implementation-readiness`] - Severity: Blocker
*   **Affected Scope:** Lines 50–53, 62–67, 121, 223, 283–297 (Goals G5/G10/G12, CUJ 5, Scheduled jobs), and items 42–44 (lines 791–816)
*   **Discrepancy Description:**
    *   The "hard 60-minute" hold depends on Stripe's expiry webhook, because the cleanup only selects orders without a payment.
    *   Orders without a link keep their stock after the 28-minute cutoff.
    *   The startup gate keeps the bot down while a paid legacy order waits for its webhook.
    *   The jobs belong to no stage.
*   **Rationale:** `core/services.py:460` filters `pagamento__isnull=True`. A lost webhook means stock that is never released, or a paid order that stays pending.
*   ***AI Suggestion:*** PROPOSE FIX (author decision A)
*   ***Justification:*** One reconciling sweep closes the webhook gap for every order, and it makes the legacy drain, the startup block and the legacy token parsing unnecessary.
*   ***Proposed Edit:***
    *   **Location:** G5, G10, G12, CUJ 5, the Technical Plan's "Scheduled jobs and switch-over" section, and items 42–44.
    *   **Change:**
        *   Replace G12 with: "**G12, one reconciling cleanup.** Every 5 minutes, `cancelar_encomendas_abandonadas --horas 1` reconciles each pending order older than 60 minutes, with or without a payment link, and with or without a snapshot:
            *   **no payment:** cancel and return stock;
            *   **stored Stripe session `complete`:** confirm through `confirmar_pagamento`, the same path as the webhook, and notify the customer;
            *   **session `expired` or `open`:** cancel through `cancelar_encomenda`, which expires an open session and returns stock;
            *   **Stripe or database error:** leave the order unchanged, log it, and retry on the next run.

            A lost webhook therefore never leaves a paid order pending or releases its stock."
        *   G5: "at most 65 minutes" for every order. Remove the word "replaced".
        *   Stage 2 changes `encomendas_abandonadas` (drop the `pagamento__isnull` filter) and `cancelar_se_abandonada` (reconcile per payment state). Both ship in the Stage 2 PR.
        *   Delete item 42 (`drenar_encomendas_antigas`) and item 43's startup check, plus the deploy-order step and CUJ 5's drain and startup block.
        *   Rewrite CUJ 5: "backup, `migrate`, start. Legacy orders are reconciled by the next cleanup run."
        *   Add to item 45 (`DEPLOYMENT.md`) and to G10 a setup check: install the cron file, run the command once by hand, then confirm a second run's summary line appears in the log within 5 minutes.
        *   State in Principles: "The Stripe webhook is served by the Django web process, separate from `bot.py`."
        *   **Known limit:** a legacy order younger than 60 minutes that needs a new session may get a Stripe idempotency error, because its old key was used with the old parameters. The customer sees the payment-failure screen, and the sweep reconciles the order within 65 minutes.

### [Issue 2] [`design, zoom-out, codebase-details`] - Severity: Blocker
*   **Affected Scope:** Lines 133–139 (lock rules), 152–160 (diagram), 548–559 (pseudocode), 860 (mutation check)
*   **Discrepancy Description:** Checkout locks `Utilizador` `FOR UPDATE` while it holds product locks. Cancellation holds product locks and inserts an audit row, whose foreign-key check needs a share lock on the same row. That is a deadlock cycle.
*   **Rationale:** PostgreSQL takes a `FOR KEY SHARE` lock on the referenced row for a foreign-key insert, which conflicts with `FOR UPDATE`.
*   ***AI Suggestion:*** PROPOSE FIX
*   ***Justification:*** The partial unique constraint already enforces one pending order per customer. The user lock adds a cycle and no guarantee.
*   ***Proposed Edit:***
    *   Remove "lock user" from the diagram, the lock rules and the pseudocode.
    *   The constraint violation is caught inside a savepoint (the existing `reservar_encomenda` pattern) and mapped to `"pendente"`.
    *   `Carrinho` is the only per-customer lock.
    *   Add threaded test T9: an admin cancel of the customer's paid order containing product P, racing the same customer's checkout that contains P. Both finish without a deadlock error.
    *   Replace the user-lock mutation check with: remove the constraint, and T8 must fail.

### [Issue 3] [`zoom-out, design, comprehension, implementation-readiness`] - Severity: Blocker
*   **Affected Scope:** Lines 204–210, 246, 538–550, 583, CUJ 2 (lines 101–105)
*   **Discrepancy Description:** `"alterado"` promises a new snapshot, but `criar_confirmacao` refuses one when a line is out of stock or inactive, which is the main CUJ 2 case. Building one would also write while locks are held. The source of the old price, deleted products and the return contracts are all undefined.
*   **Rationale:** As written, the result can't be implemented consistently.
*   ***AI Suggestion:*** PROPOSE FIX
*   ***Justification:*** Writing nothing and redrawing the cart is simpler and correct.
*   ***Proposed Edit:***
    *   **`"alterado"` writes nothing.** The transaction rolls back and returns a list of changes: `(produto_id, tipo ∈ {preco, stock, inativo, removido}, antes, depois)`. `antes` and `depois` come from the snapshot and the live row; a product deleted since the screen was drawn gives `removido`.
    *   **After the commit,** the handler shows the cart screen with those flags.
    *   **Which flags block checkout:** stock, inactive and removed block. A price change only marks the line; the customer then confirms a new snapshot.
    *   **Return contracts:** add a short table of each cart service and its return values.
    *   **Stage 2 (no cart):** the one-product screen redraws the product screen with the same flags.

### [Issue 4] [`codebase-details`] - Severity: Blocker
*   **Affected Scope:** Lines 196–199, 523, 538
*   **Discrepancy Description:** `JSONField` can't serialise `Decimal` prices.
*   **Rationale:** The default `DjangoJSONEncoder` isn't set on the field, so it raises `TypeError` when the value is written.
*   ***AI Suggestion:*** PROPOSE FIX
*   ***Justification:*** Integer cents avoid both the encoder and rounding drift.
*   ***Proposed Edit:*** Store `preco_unitario_centimos` and `total_centimos` as integers in the snapshot. Convert with `Decimal(c) / 100` on read. `parametros_checkout` already needs cents.

### [Issue 5] [`implementation-readiness`] - Severity: Blocker
*   **Affected Scope:** Items 3 and 11 (lines 451–454, 506)
*   **Discrepancy Description:** The planned migration test migrates back through 0010, whose reverse raises `IrreversibleError`.
*   **Rationale:** The test can't be set up as written.
*   ***AI Suggestion:*** PROPOSE FIX
*   ***Justification:*** A forward-only test is enough, because the migration is forward-fix only.
*   ***Proposed Edit:*** Item 11: "In a `TransactionTestCase`, migrate to 0009, insert products with two old codes, migrate to 0011, and check the categories and links." 0010's reverse stays an error.

### [Issue 6] [`structural, zoom-out`] - Severity: Blocker
*   **Affected Scope:** Before "Alternatives Considered" (line 305)
*   **Discrepancy Description:** The Risks & Mitigations section that the standard requires is missing.
*   **Rationale:** The risks are spread through the text.
*   ***AI Suggestion:*** PROPOSE FIX
*   ***Justification:*** A reviewer needs them in one place.
*   ***Proposed Edit:*** Add a "## Risks & Mitigations" table of 8 rows: Stage 0 failure; lock cycle (Issue 2); lost webhook (Issue 1); Stripe key reuse at deploy (Issue 1 limit); stock hoarding through repeated orders (Issue 15); owner rights (Issue 12); Markdown in owner text (Issue 16); forward-only migrations with backups.

### [Issue 7] [`zoom-out, design, comprehension, implementation-readiness`] - Severity: Major
*   **Affected Scope:** Lines 154, 225–231, 545, 570–575
*   **Discrepancy Description:** Snapshot age is checked against the button timestamp, which comes from the client and can be forged. The constant named for it doesn't exist.
*   **Rationale:** The server already stores `criada_em`.
*   ***AI Suggestion:*** PROPOSE FIX
*   ***Justification:*** This removes a client-controlled input and the token parsing work.
*   ***Proposed Edit:***
    *   The token becomes `confirmar_encomenda_<nonce>`, keeping the existing prefix, so no allowlist change is needed.
    *   Age is checked as `now − snap.criada_em ≤ VALIDADE_SEGUNDOS`, using the existing constant in `confirmacao.py`.
    *   `ler_callback` accepts `<16-hex>`, which means a snapshot, or the old `<nonce>_<ts>_<id>`, used only to find an existing order by `chave_confirmacao`. Anything else shows the expired screen.

### [Issue 8] [`comprehension, zoom-out, implementation-readiness`] - Severity: Major
*   **Affected Scope:** Line 690 (item 28), line 186
*   **Discrepancy Description:** Removing `encomendar_` from the allowlist gives old buttons the generic "invalid request" alert, not the expired-menu message.
*   **Rationale:** The allowlist rejects a callback before any handler runs.
*   ***AI Suggestion:*** PROPOSE FIX
*   ***Justification:*** Keeping the prefix costs nothing and gives a useful action.
*   ***Proposed Edit:*** Keep `encomendar_<id>` on the allowlist and route it to `carrinho_add`, so an old "Encomendar" button adds that product to the cart.

### [Issue 9] [`design, implementation-readiness`] - Severity: Major
*   **Affected Scope:** Lines 23–35 (Problem), G9, Stage 4 permissions (lines 277–282), items 32 and 38
*   **Discrepancy Description:** The owner can't add products or categories, which contradicts the Problem statement. The order admin actions declare no `allowed_permissions`, so a view-only owner can still cancel and refund.
*   **Rationale:** Django checks `allowed_permissions` on the server when an action runs. With none declared, any user who can see the list can run the action.
*   ***AI Suggestion:*** PROPOSE FIX (author decision C)
*   ***Justification:*** The fix enforces the decision on the server, not by hiding controls.
*   ***Proposed Edit:***
    *   **"Dono da loja" group:**
        *   add, change and view on `Produto` and `Categoria`;
        *   change and view on `Loja`;
        *   view only on `Encomenda`, `LinhaEncomenda` and `Pagamento`;
        *   no delete anywhere. Deactivating products and categories is the way to remove them.
    *   **`EncomendaAdmin`:**
        *   `marcar_cancelada` gets `allowed_permissions = ('cancelar',)` with a `has_cancelar_permission` that requires the custom permission `core.cancelar_encomenda` (declared in `Encomenda.Meta.permissions`). The group doesn't get it; superusers have it.
        *   `marcar_enviada` and `marcar_entregue` get `allowed_permissions = ('change',)`. The owner has no change permission on orders.
    *   **Test:** a staff user in the group who posts the cancel action gets a permission error, and the order is unchanged.
    *   **G9:** "cancellation and refunds happen in the Telegram admin panel".

### [Issue 10] [`implementation-readiness`] - Severity: Major
*   **Affected Scope:** Item 32 (lines 717–720)
*   **Discrepancy Description:** The data migration that creates the group runs before Django has created the permissions.
*   **Rationale:** Permissions are created by the `post_migrate` signal, after the migrations run.
*   ***AI Suggestion:*** PROPOSE FIX
*   ***Justification:*** Without the fix, a fresh install gets an empty group.
*   ***Proposed Edit:*** In 0015, call `create_permissions(apps.get_app_config('core'), verbosity=0)` (and the same for `auth`) before looking permissions up. Depend on the latest `auth` and `contenttypes` migrations.

### [Issue 11] [`design, zoom-out`] - Severity: Major
*   **Affected Scope:** G9, item 33, item 45
*   **Discrepancy Description:** Admins listed in `.env` can't be removed through the database, so handover is incomplete.
*   **Rationale:** The bootstrap list always wins.
*   ***AI Suggestion:*** PROPOSE FIX
*   ***Justification:*** A procedure step solves it with no code.
*   ***Proposed Edit:*** Add a `DEPLOYMENT.md` handover step: after the owner's admin flag is set in Django admin, set `ADMIN_TELEGRAM_IDS` to the reseller's id only (or leave it empty) and restart the bot once. G9 states the same.

### [Issue 12] [`zoom-out, design, comprehension, implementation-readiness`] - Severity: Major
*   **Affected Scope:** G6, items 24–25 (lines 662–664, 676–680), Future improvements
*   **Discrepancy Description:** "Repeat order" ignores the 20-line cap and can be used to hold scarce stock again and again. The button's visibility depends on a service that changes state. There is no customer cancel.
*   **Rationale:** The decision covers the features; the details were unspecified.
*   ***AI Suggestion:*** PROPOSE FIX (author decision B)
*   ***Justification:*** These are small specification fixes within the chosen scope.
*   ***Proposed Edit:***
    *   `repetir_encomenda` stops at 20 lines and reports the lines it skipped.
    *   The button appears when a read-only check, `pode_repetir(encomenda, user)`, passes.
    *   Repeated hoarding is accepted as a risk: each cycle needs a new Confirm, there is one pending order per customer, and the hold is at most 65 minutes. It is listed in Risks.
    *   Customer self-cancel moves to "Future Improvements".

### [Issue 13] [`design, implementation-readiness, zoom-out`] - Severity: Major
*   **Affected Scope:** Stage 1 (category names), Stage 4 `/start` (item 35)
*   **Discrepancy Description:** The welcome text and category names are typed by the owner and sent as Markdown without escaping.
*   **Rationale:** A `_` or `*` in them makes Telegram reject the message (SEC-07).
*   ***AI Suggestion:*** PROPOSE FIX
*   ***Justification:*** The existing `md()` helper covers it.
*   ***Proposed Edit:*** The welcome text is passed through `md()` before it is sent as Markdown. Category names are escaped wherever they appear in a Markdown message. Add tests with `_*[`.

### [Issue 14] [`implementation-readiness`] - Severity: Major
*   **Affected Scope:** Items 26, 34, 35; `telegram_bot/tests.py`
*   **Discrepancy Description:** The signature change to `teclado_menu_principal` misses its callers. The tests that patch `is_admin` aren't updated.
*   **Rationale:** The bot and the tests would break mid-stage.
*   ***AI Suggestion:*** PROPOSE FIX
*   ***Justification:*** Keeping the change to one stage avoids a broken intermediate state.
*   ***Proposed Edit:***
    *   Drop the cart count from the menu button: the label is "🛒 Carrinho", with no count (see nits).
    *   Change `teclado_menu_principal(user_id, admin)` only in Stage 4, together with `e_admin`. List every caller: `commands.start`, `customer.menu_inicio` and the menu-returning helpers.
    *   Update every test that patches `guards.is_admin` to patch `services.e_admin`.

### [Issue 15] [`implementation-readiness, zoom-out, design`] - Severity: Major
*   **Affected Scope:** Items 20, 21, 29; Verification Plan (lines 856–874); G13
*   **Discrepancy Description:** The list of tests to change is incomplete. The threaded tests lose their pause point. T6 and T7 have no checkable result. The cutoff retry test has no expected value. G13 isn't measurable.
*   **Rationale:** An implementer can't tell when the tests are done.
*   ***AI Suggestion:*** PROPOSE FIX
*   ***Justification:*** Without these, the test work can't be finished or checked.
*   ***Proposed Edit:***
    *   **Tests to change:** add `ConcurrentCheckoutTests.correr_em_paralelo` (it patches `_encomenda_por_chave`), the E1/E2 end-to-end tests, X1–X8 and the cancellation tests that create orders through `reservar_encomenda`.
    *   **New pause hook:** a patched `services._bloquear_produtos`, with thread A pausing after it acquires the locks.
    *   **Results:**
        *   T6: both orders created, or one `alterado` when stock runs out, with stock equal to the start minus the created quantities and no `DeadlockDetected`;
        *   T7: the cancel and the checkout both finish, and the stock reflects both;
        *   cutoff retry: at 29 minutes, `Session.create` isn't called, the order is `cancelada`, and the result is `"expirada"`.
    *   **G13:** final stock = initial stock − quantities of paid orders, and every order is either paid and confirmed, or cancelled with its stock returned and any payment refunded.

### [Issue 16] [`codebase-details, design, zoom-out`] - Severity: Major
*   **Affected Scope:** Lines 222–224, 296–297 (claims), item 14 (`garantir_sessao_pagamento`)
*   **Discrepancy Description:** `cancelar_encomenda` calls Stripe while holding the order lock, so "Stripe is never called under a lock" is wrong. The new calls inside `garantir_sessao_pagamento` don't handle its exceptions.
*   **Rationale:** SEC-06 holds the order lock across Stripe calls by design.
*   ***AI Suggestion:*** PROPOSE FIX
*   ***Justification:*** The claim is false, and the unhandled exceptions would reach the customer.
*   ***Proposed Edit:***
    *   Change the claim to: "Checkout and session creation never call Stripe under a lock; cancellation holds the order lock across its Stripe calls (SEC-06)."
    *   In the cutoff and expired paths:
        *   `PagamentoRecebido` returns `"a_confirmar"`;
        *   a `StripeError` propagates to the existing payment-failure screen, and the sweep retries.

### [Issue 17] [`codebase-details`] - Severity: Major
*   **Affected Scope:** Lock rules (lines 133–139); `core/admin.py` product bulk actions
*   **Discrepancy Description:** The bulk activate and deactivate actions use `queryset.update()`, which locks product rows in scan order. That can deadlock with checkout's id-ordered locks.
*   **Rationale:** A deadlock needs two transactions locking shared rows in different orders.
*   ***AI Suggestion:*** PROPOSE FIX
*   ***Justification:*** One extra query fixes it.
*   ***Proposed Edit:*** Add an item for `core/admin.py`: `ativar_produtos` and `desativar_produtos` run in a transaction that first runs `select_for_update().filter(pk__in=…).order_by('pk')`, then the `update()`. Add it to the lock rules.

### [Issue 18] [`comprehension, design, structural`] - Severity: Major
*   **Affected Scope:** Lines 21, 35, 53, 86–89, 127, 138, 202, 223
*   **Discrepancy Description:** The document relies on things it doesn't explain:
    *   D19 is undefined;
    *   SEC ids are unexplained;
    *   the 28-minute cutoff is unexplained;
    *   the link between `chave_confirmacao` and the idempotency key is unexplained;
    *   two Stage 0 lists disagree, and "course rights" is undefined;
    *   Q2 is closed but still listed;
    *   Stage 0 has no section of its own.
*   **Rationale:** A fresh reader can't follow the argument.
*   ***AI Suggestion:*** PROPOSE FIX
*   ***Justification:*** These are comprehension gaps, which are not contestable.
*   ***Proposed Edit:***
    *   Add a "Background" glossary after The Problem, with D19 and one line each for SEC-01, -02, -03, -06, -07, -09, -11, -12 and -13.
    *   At the first use of 28 minutes: "Stripe requires `expires_at` ≥ 30 minutes after session creation; 60 − 28 = 32 leaves a 2-minute margin."
    *   At item 12: "the Stripe idempotency key `chave_primeira_tentativa` is built from `chave_confirmacao`".
    *   Add a "## Stage 0" section with a single table: item, what it checks, and "blocks the plan: yes/no". Define course rights as "rights the university or course may hold over the final-project code".
    *   Remove Q2 from Open Questions.

### [Issue 19] [`design, style`] - Severity: Major
*   **Affected Scope:** Alternatives Considered (lines 306–425); Non-Goals; the legal statements
*   **Discrepancy Description:** The rejections are credited to "author" with no link to where the decision was made. A legal claim has no source.
*   **Rationale:** The decisions can't be traced.
*   ***AI Suggestion:*** PROPOSE FIX
*   ***Justification:*** Making each decision traceable adds no extra length.
*   ***Proposed Edit:***
    *   Each rejection ends with "(see concepts_brief_cart.md, Key Decisions)" or "(approved at the Technical Plan gate)".
    *   A6 and the Non-Goals legal lines say "not legal advice; unverified".

### [Issue 20] [`implementation-readiness, style`] - Severity: Major
*   **Affected Scope:** Item 16 (lines 580–595); the Stage 2 and Stage 3 screens
*   **Discrepancy Description:** The expired, pending, failure and invalid screens have no defined keyboards for snapshot tokens, and some claims overstate.
*   **Rationale:** An implementer would have to invent UI.
*   ***AI Suggestion:*** PROPOSE FIX
*   ***Justification:*** One small table removes the guesswork.
*   ***Proposed Edit:*** Add a table of screens and their buttons:

    | Screen | Buttons |
    | --- | --- |
    | expirado | Ver carrinho, Menu |
    | pendente | Ver encomenda (`ver_encomenda_<id>`), Menu |
    | falha | Tentar novamente (same token), Menu |
    | inválido | Menu |

    Tighten "never" and "always" claims to what the code guarantees.

### [Nits] [`all`] - Severity: Nit
*   **PROPOSE FIX:**
    *   **Missing or inconsistent names:**
        *   add `"invalida"` to the Stage 2 results;
        *   use one name, `preparar_confirmacao_carrinho`;
        *   the cart's "Finalizar compra" is hidden only for stock, inactive and removed flags.
    *   **Stale statements:**
        *   G5 "replaced" (covered in Issue 1);
        *   G10's configuration check already exists;
        *   remove the "Step 6" reference;
        *   "three" settings variables;
        *   Stripe's 30-minute minimum rules out a shorter `expires_at` in the demo copy;
        *   remove the "broken webhook blocks the start" claim (now obsolete).
    *   **Django 6 syntax:**
        *   `CheckConstraint(condition=…, name=…)`;
        *   name every `UniqueConstraint`;
        *   `Loja.texto_boas_vindas` becomes `CharField(max_length=1000)`.
    *   **The 0010 migration:** copy the `Produto.CATEGORIA_CHOICES` pairs into 0010, use the model labels, and give unknown codes `ordem = 99`.
    *   **Reuse:**
        *   `teclado_voltar_catalogo()` instead of a new keyboard;
        *   add `get_categoria_ativa(id)`;
        *   `e_admin` uses `aexists()`, and the handlers call `obter_loja_async()`.
    *   **Smaller scope:**
        *   fold `limpar_rascunhos` into `cancelar_encomendas_abandonadas`, so one cron entry does both;
        *   drop the snapshot `moeda` field, the cart count on the menu button, and the separate snapshot admin page.
    *   **Stage 3:** `revisao_carrinho` stays nullable, documented as "null only for Stage 2 one-product snapshots".
    *   **Format:**
        *   apply "Type / Scope" to every item or to none;
        *   add a short glossary of Portuguese identifiers (`Encomenda` = order, `Utilizador` = customer, `Pagamento` = payment, `MovimentoStock` = stock movement, `RegistoAuditoria` = audit row, `pago` = paid, `reembolsado` = refunded);
        *   one line on the test series: S = payment session, H = checkout handler, db = order creation, T = threaded, X = expiry, C = cancellation.
*   **DECLINE:**
    *   **"The Detailed Plan drops to code level":** the template allows pseudocode and schemas, and the pseudocode is labelled.
    *   **"Merge the Technical Plan into the Detailed Plan":** the template requires both. The drifted statements are fixed above instead.
