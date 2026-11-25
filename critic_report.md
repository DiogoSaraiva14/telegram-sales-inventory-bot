# Goldfish Review Report (Stage 7, Critic Review)

**Target:** `docs/designs/cart_resellable_shop.md` (lines 1-426, everything before "## Detailed Implementation Plan"; lines 427-874 were not reviewed)

## Reviewer Status

| Reviewer | Standard | Status |
| --- | --- | --- |
| `egm-design` | feature-spec-standard.md | completed (1 Blocker, 9 Major, 9 Nit) |
| `egm-structural` | feature-spec-standard.md | completed (1 Blocker, 3 Major, 6 Nit) |
| `egm-style` | persona rules only | completed (0 Blocker, 8 Major, 17 Nit) |

After deduplication there are 2 Blockers, 15 Majors and 22 Nits. Findings raised by more than one reviewer are merged, and every lens that raised them is listed.

Two findings rest on reasoning, not on a reproduction:
- B1 rests on PostgreSQL's row-lock conflict rules.
- M5 rests on how Django admin applies permissions to actions.

A threaded test and a quick check with a view-only admin user would confirm each of them. Nobody checked Stripe's 30-minute minimum for `expires_at` (see M3) against the Stripe documentation.

---

## Blockers

### B1. The "no deadlocks" guarantee is false: foreign-key share locks on `Utilizador` create a cycle
- **Lens:** `egm-design` (`egm-style` N12 raised the same over-absolute wording)
- **Lines:** 49, 133-137, 156-159
- **Problem:** Line 136 says product rows are "the only place two transactions can wait on each other's rows". That is not true.
  - Checkout locks the customer row with `FOR UPDATE` and then locks products.
  - `cancelar_encomenda` (`core/services.py:441-448`) locks the order, then the products, and then inserts a `RegistoAuditoria` row with `utilizador=encomenda.utilizador`. The foreign-key check on that insert takes `FOR KEY SHARE` on the same `Utilizador` row, and `FOR KEY SHARE` conflicts with `FOR UPDATE`.
  - Scenario: an admin cancels customer U's confirmed order containing product P while U confirms a new cart that also contains P. The "pendente" check does not stop this, because the old order is confirmed, not pending. PostgreSQL aborts one side, possibly the cancellation after the Stripe refund call has already been made.
- **Suggested change:**
  - Lock `Utilizador`, `Carrinho` and `ConfirmacaoCarrinho` with `select_for_update(no_key=True)`. Alternatively, drop the user-row lock and rely on the cart row lock plus the partial unique index.
  - Rewrite line 136 so it accounts for foreign-key share locks.
  - Add to G4 a threaded test where checkout races `cancelar_encomenda` on the same customer's confirmed order with overlapping products.
  - Change "Two threaded tests prove it" (line 49) to "exercise it". Tests cannot prove that no deadlock exists; the lock-order rule is what does that.

### B2. The required Risks & Mitigations section is missing
- **Lens:** `egm-structural` (Blocker), `egm-design` (M-8)
- **Lines:** the whole range. The gap belongs between line 305 and line 306, and nothing in lines 427-874 fills it.
- **Problem:** The feature-spec standard requires failure modes, security implications, scalability limits and their mitigations to be stated explicitly. The design spreads these across The Problem (35-42), Principles (133-137), G4, G5, G12 and lines 264 and 283. Some have no mitigation at all. The predecessor brief opened with "Risks, stated first" (`concepts_brief_cart.md:27`); the design dropped that structure.
- **Suggested change:** Add `## Risks & Mitigations` before `## Alternatives Considered`. Make it a table with these columns: Risk | Kind (failure / security / scalability / legal) | Mitigation or accepted limit | Where enforced. It needs at least these rows:
  - deadlock (B1);
  - lost or late expiry webhook (M1);
  - repeat-order stock hoarding (M7);
  - admins from `.env` that cannot be removed (M6);
  - shop-owner group access to customer data (M5);
  - owner text breaking Markdown (M8);
  - forged or stale callbacks, including the 6 new prefixes;
  - snapshot table growth, since one row is written per screen draw, and whether a rate limit applies;
  - cron not installed or stopped (consider a "last cleanup run" timestamp in admin);
  - Stripe outage around the 28-minute cutoff;
  - Stripe idempotency mismatch on legacy orders (G12, A15);
  - no rollback path (A13, backups);
  - per-copy operations burden (G10);
  - Stage 0 ownership and licence risk (Q1).

---

## Major

### M1. The "hard 60-minute stock hold" depends on the expiry webhook arriving and is described inconsistently
- **Lens:** `egm-design` (M-1), `egm-style` (M3, M4)
- **Lines:** 50-53, 105-109, 138, 166-169, 222-223, 288
- **Problem:**
  - An order that has a payment link is released only in two ways: by the `checkout.session.expired` webhook, or by the customer coming back to `garantir_sessao_pagamento`. `cancelar_encomendas_abandonadas` only selects orders with `pagamento__isnull=True` (`services.py:457-461`). If the webhook is lost and the customer never returns, the stock is never released.
  - Line 223 ("covers a webhook that never arrives") is true only if the customer comes back.
  - The heading calls the hold "hard 60-minute", but line 52 says "at most 65 minutes".
  - Line 53 reads as if the order is cancelled automatically at 28 minutes. Line 222 says that only happens when a link is requested after the cutoff.
- **Suggested change:**
  - Extend the 5-minute cron so it also sweeps pending orders older than 60 minutes plus a margin whose session is not `complete`: retrieve the session, then call `cancelar_encomenda`. Otherwise, weaken G5 and CUJ3 to "60 minutes plus webhook delivery".
  - Retitle G5 to "a bounded stock hold (60 minutes with a payment link, 65 without)".
  - Rewrite line 53: "A request for a payment link after that cutoff cancels the order instead of creating a link."
  - Add one sentence at line 138 explaining why the cutoff is 28 minutes. It is probably Stripe's 30-minute minimum for `expires_at`; verify that first.

### M2. The "alterado" result contradicts CUJ2, and Stage 2 has no out-of-stock result
- **Lens:** `egm-design` (M-2)
- **Lines:** 103, 157, 205-210, 213, 246
- **Problem:**
  - Line 210 says "alterado" returns a new snapshot. Line 246 says a snapshot cannot be built while any line is short of stock or inactive. In CUJ2, product B is out of stock, so no snapshot can exist.
  - The Stage 2 single-product path loses today's `"sem_stock"` result.
  - The design does not say whether a snapshot written inside the Confirm transaction stays committed when no order is created.
- **Suggested change:** Split the result into two:
  - `alterado`: a new snapshot is returned, only when every line is still orderable;
  - `bloqueado`: no snapshot; the result carries the flagged lines and the bot redraws the cart, or in Stage 2 the "sem stock" screen.

  Update the diagram and CUJ2 to match, and state the commit behaviour.

### M3. The transaction diagram's arrows contradict the return values
- **Lens:** `egm-style` (M1), `egm-structural` (N5)
- **Lines:** 152-160
- **Problem:**
  - Line 154 sends a failed owner check to "expirada". Line 207 says that case returns `"de_outro_utilizador"`.
  - On line 156 the "else" is backwards: "pendente" is returned when another pending order exists.
  - Rows 155-157 are one column wider than the other rows, and the "→ else" column moves between rows.
- **Suggested change:** Write each row as condition → result. For example:
  - "not the owner → de_outro_utilizador; older than 30 min → expirada";
  - "another pending order exists → pendente".

  Pad all inner rows to the same width and align the result column.

### M4. Snapshot age is checked against a client-supplied timestamp, and the cleanup can race Confirm
- **Lens:** `egm-design` (M-9)
- **Lines:** 154, 202, 204, 289
- **Problem:**
  - `criar_encomenda_de_confirmacao(..., emitida_em)` takes its age input from the callback data. If the token's timestamp is trusted, an altered timestamp can push validity past the 45-minute cleanup point.
  - A confirmation can then be in progress while `limpar_rascunhos` deletes the snapshot it is using.
  - Line 202 does not specify `on_delete` for `Encomenda.confirmacao`.
- **Suggested change:**
  - Check age against the stored `criada_em`, and use the token timestamp only as a consistency check.
  - Declare `on_delete=PROTECT`.
  - Make the cleanup a single `DELETE ... WHERE NOT EXISTS (order)` that skips rows locked by a confirmation in progress.

### M5. The shop-owner group cannot add products, yet its "view-only" order access can still cancel and refund
- **Lens:** `egm-design` (M-3)
- **Lines:** 61, 115, 277-282
- **Problem:**
  - Line 278 grants only change and view on `Produto` and `Categoria`, so the owner cannot create products or categories. That contradicts G8 and CUJ4.
  - The `EncomendaAdmin` actions `marcar_enviada`, `marcar_entregue` and `marcar_cancelada` (`core/admin.py:117, 150-178`) have no `allowed_permissions`, so Django offers them to view-only users. `marcar_cancelada` runs `cancelar_encomenda`, which issues Stripe refunds. That contradicts G9 ("can only read orders and payments").
- **Suggested change:**
  - Grant add, change and view on `Produto` and `Categoria`.
  - Set `allowed_permissions=('change',)` on the order actions, or state explicitly that the owner may advance, cancel and refund orders.
  - Add one permission test per model and per action.

### M6. Admins listed in `.env` can never be removed through the database, which weakens handover
- **Lens:** `egm-design` (M-4)
- **Lines:** 57-61, 272-276
- **Problem:** `e_admin()` returns true for any id in `ADMIN_TELEGRAM_IDS`, permanently. After handover, the reseller's Telegram id remains a full admin of the client's shop until someone edits `.env` and restarts. That contradicts G9 ("only the reseller's superuser can grant or remove", "without restarting"), and it matters for the GDPR roles raised in the brief.
- **Suggested change:** Treat the env list as a bootstrap that applies only while no `e_admin=True` row exists, or copy it into the database once at first start and then ignore it. Document the rule in the handover steps.

### M7. "Repeat order" lets one account hold scarce stock indefinitely
- **Lens:** `egm-design` (M-5)
- **Lines:** 42, 54, 109, 247
- **Problem:** An order that expires can be put back into the cart in one tap and confirmed again straight away. One account can keep up to 99 units unavailable in back-to-back 60-minute windows, at no cost. The Problem section (line 42) names this exact threat, but the design only shortens each hold.
- **Suggested change:** Add a limit, for example refuse checkout after N expiry cancellations per customer in 24 hours, or add a cooldown after an expiry. Otherwise, record it as an accepted risk in B2 and give the reason.

### M8. Owner-entered text is sent as Markdown without escaping
- **Lens:** `egm-design` (M-7)
- **Lines:** 186, 268-271
- **Problem:** Only the shop name is escaped (line 271). The welcome text (up to 1000 characters) and the category names are now free text typed by the owner. `customer.py:40` puts the category name inside `*...*` with Markdown enabled, and SEC-07 only exempted fixed labels. One stray `_` or `*` makes Telegram reject the message, which breaks `/start` or the category screen for every customer.
- **Suggested change:** Apply `md()` to the welcome text and to category names, or send them with no parse mode. Optionally, validate the text on admin save.

### M9. The legacy-order startup gate makes shop availability depend on Stripe webhook delivery
- **Lens:** `egm-design` (M-6)
- **Lines:** 67, 117-121, 290-299
- **Problem:**
  - `bot.py` refuses to start while any legacy pending order exists, including a paid order that is still waiting for its webhook. If that webhook is late or lost, the whole shop stays down.
  - The deploy order leaves out pausing cron during `migrate`. Old code would run against the renamed `categoria` column.
  - The deploy order also leaves out running the Django/webhook process on the new code during the drain.
- **Suggested change:**
  - Replace the global startup block with a per-order guard in `garantir_sessao_pagamento`: for an order with `confirmacao IS NULL`, never create or replace a session, and cancel the order if it has no session or its session has expired.
  - Keep the drain command as the cleanup step.
  - Write out the full deploy order: stop cron and Django, migrate, restart Django, drain, then start the bot and cron.

### M10. Open Question Q2 is already decided, and it cross-references itself
- **Lens:** `egm-structural` (M4), `egm-style` (M8), `egm-design` (N-7)
- **Lines:** 46, 89, 235
- **Problem:** Q2 says the limits are "set in the Technical Plan: 99 per line and 20 lines". Line 235 says "set here, Open Question Q2". G1 says "a fixed per-line maximum" without a number.
- **Suggested change:**
  - Delete Q2.
  - Write in G1: "up to 20 lines and at most 99 per line, never above available stock".
  - Change line 235 to "**Limits:**".

### M11. Stage 0 gates the whole plan but has no section of its own
- **Lens:** `egm-structural` (M3), `egm-style` (N5)
- **Lines:** 21, 68, 88, 123-304
- **Problem:** Stage 0 "can stop the plan" (line 21), but it appears only in the Overview and in Q1. Nothing says:
  - what the pass/fail criteria are for ownership, licence, images, invoicing and the Stripe test run;
  - who decides each item;
  - what the "portfolio demo" fallback means for Stages 1-4.

  Lines 21 and 88 also describe the fallback in two different ways. The relationship between the Stage 0 Stripe run and G13 is not stated.
- **Suggested change:** Add `### Stage 0: decisions and proof (no code)` before line 174. Give it a checklist with an exit criterion per item and the fallback scope. Reduce Q1 to the items that are still open, and say whether G13 is the Stage 0 run.

### M12. Rejected alternatives are credited to "author" without a link, and the wording varies
- **Lens:** `egm-structural` (M2), `egm-design` (N-9), `egm-style` (N17)
- **Lines:** 314, 320, 326, 332, 338, 344, 353, 359, 369, 375, 381, 387, 393, 399, 405, 413, 419, 425
- **Problem:** 16 entries say "Rejected by author", some adding "when approving the Technical Plan", while A4 and A6 say only "Rejected:". The standard forbids attributing a rejection to a person without linking the source. Two alternatives are also missing:
  - other lock strategies (`FOR NO KEY UPDATE`, advisory locks, SERIALIZABLE with retry);
  - letting a customer cancel their own unpaid order instead of being blocked by the one-pending-order rule. The reason A10 gives against "Edit" does not apply to a plain cancel.
- **Suggested change:** Use "Rejected:" followed by the trade-off in every entry. Link the brief's "Alternatives Weighed" section for A1-A12 and its "Key Decisions" anchors for A13-A18. Add the two missing alternatives.

### M13. The list of changed functions is incomplete, and the "no Stripe call under lock" claim is wrong
- **Lens:** `egm-style` (M2), `egm-design` (N-1)
- **Lines:** 132, 221-224, 288
- **Problem:**
  - Line 132 says only `parametros_checkout`, the payment screen and `_devolver_stock` change. `garantir_sessao_pagamento` also changes (the session cutoff and expired-session handling), and so does the cleanup window.
  - Line 224 says the function "never calls Stripe while holding a lock". Its new branches call `cancelar_encomenda`, which holds the order lock across the Stripe retrieve and expire calls (`services.py:416-440`).
- **Suggested change:** List every function that changes on line 132, and correct line 224.

### M14. G13's "consistent" end state cannot be measured
- **Lens:** `egm-style` (M6)
- **Lines:** 68
- **Suggested change:** List the checks:
  - each product's stock equals its starting stock minus the quantities in paid, unrefunded orders;
  - every cancelled order has one `entrada` movement per line;
  - every state change has exactly one audit row;
  - the refunded payment is `reembolsado`.

### M15. An unsourced legal claim
- **Lens:** `egm-style` (M7)
- **Lines:** 344 (A6)
- **Problem:** The text says the approach "fits EU consumer rules" without citing anything.
- **Suggested change:** Write "is intended to meet the EU rule that the final price is shown before payment; this has not been legally reviewed", or cite the specific rule.

---

## Nits

| # | Lens | Lines | Problem | Suggested change |
| --- | --- | --- | --- | --- |
| N1 | design, style | 214 | The token length is given as "at most 44 bytes", but it is fixed at 43. | Write "exactly 43 bytes". |
| N2 | design | 213-214, 256-263 | Stage 2 already sends `confirmar_carrinho_` buttons, but its allowlist and route entries are listed under Stage 3. Nothing says what happens to old `encomendar_<id>` buttons. | Move the entry to Stage 2 and state how old buttons are handled. |
| N3 | design | 157, 199 | The default `JSONField` encoder cannot serialize `Decimal`. The design also does not say whether a renamed product counts as changed. | Store prices as strings and compare them as exact `Decimal` values. Say which product name the order line and Stripe use. |
| N4 | design | 158, 198 | The design does not define `Encomenda.total`. | State that it is the sum of the line subtotals and that it must equal the snapshot total. |
| N5 | design | 247, 255 | A double tap on "Repeat order" adds the quantities twice. | Accept this in the text, or make the merge set quantities instead of adding them. |
| N6 | design | 288 | The no-link cleanup window could be 30 minutes (a 35-minute hold), but `--horas` must be at least 1 (`core/tests.py:1264-1274`). | Consider a `--minutos` option, or say why not. |
| N7 | design | 283 | `DJANGO_HTTPS=True` behind a TLS-terminating proxy needs `SECURE_PROXY_SSL_HEADER`, or the HTTPS redirect loops. | Add it to the list of settings. |
| N8 | structural | 145, 148 | The diagram's labels ("Ver carrinho" / "Finalizar") and field list do not match Stage 2 and Stage 3 (252-253, 263, 195-197). | Use the real labels and callback names, and either list all the fields or write "see Stage 2". |
| N9 | structural, style | 223 | "D19" is used without being defined. | Define it at first use and link `concepts_brief.md`. Say that the old behaviour is removed. |
| N10 | structural | 35, 127, 283, 319, 358 | SEC-nn codes appear without links. | Add a note that SEC codes refer to `SECURITY.md`, or link the first use of each code. |
| N11 | structural | 308 | The heading "From the brainstorm" does not cover A13-A15, which come from the brief's Key Decisions and the Goldfish report. | Link the brief, and split those entries out or give each one a source link. |
| N12 | structural | 86 | Open Questions sits before the plan sections it refers to. | Move it after Alternatives, or make each question self-contained. |
| N13 | style | 27 | "No shop owner would accept that..." is a subjective generalisation. | State the concrete limit: one product per order. |
| N14 | style | 42 | "For up to 24 hours" undercounts, because retries create new sessions. Line 42 also uses "that work" without saying which work. | Write "24 hours or longer" and "the SEC-02 to SEC-13 guarantees". |
| N15 | style | 35 | The word "just" in the heading is filler. | Use "Why a cart is not a small change". |
| N16 | style | 57, 64, 67 | "Safe" and "clean" in goal titles are value judgements. | Use neutral titles. |
| N17 | style | 62 | The startup check is presented as a new goal, but line 291 calls it existing. Two jobs are called "cleanup". | Say the check already exists, and name `cancelar_encomendas_abandonadas` and `limpar_rascunhos`. |
| N18 | style | 77, 82 | "The reseller contract says so" refers to a document that does not exist yet. "One-line-per-cart release setting" is opaque. | Write "must say so", and explain the setting with a pointer to A13. |
| N19 | style | 96 | "Within 30 minutes" has no starting point. | Write "within 30 minutes of opening the confirmation screen". |
| N20 | style | 137 | "Checkout never touches an existing order" conflicts with the diagram, where checkout reads existing orders. | Write "never locks an existing order". |
| N21 | style | 179, 182 | Line 179 is awkward and uses filler ("actually", "and so on"). "Reversing is forward-fix only" contradicts itself. | Rephrase line 179. Write "the migration is irreversible; its reverse step raises an error". |
| N22 | style | 6, 200, 220, 238, 270, 288, 296, 299, 348, 353, 425 | Smaller wording problems: process jargon on line 6, line 200 repeating A16, filler words, ambiguous "it" and "until empty", an unspecified welcome text, a hidden 24 h to 1 h window change, fresh-copy steps that omit `.env`, cron and the admin flag, marketing tone in A7, and "cheap" in A18. | Apply the individual rewrites in the style reviewer's notes, for example "under Stripe's limit of 100" and "one indexed query per admin check". |
