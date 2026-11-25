# Stage 6: Comprehension Test

*   **Target:** `docs/designs/cart_resellable_shop.md`, lines 1–874.
*   **Reviewer:** `egm-comprehension` (REVIEW mode). It read only the target document.

## What this proposes

The design turns a single-shop, one-product-per-order Telegram bot into a package that can be sold to a reseller, who installs one separate copy per client shop. It has four code stages:

1.  Categories move from code into a `Categoria` table.
2.  The order core learns to handle orders with several lines. A screen that never changes after it is saved (a "snapshot") becomes the only way to create an order. Products are locked in id order. The payment window is fixed at 60 minutes from order creation, and payment links can only be created while the order is at most 28 minutes old.
3.  A cart is stored in the database, with limits, warnings on changed lines, and "Repeat order" for cancelled unpaid orders.
4.  Shop name and welcome text are set in the database, Telegram admin rights become a database flag, a limited Django group is created for shop owners, the HTTPS settings are finished, and a deployment guide is written.

A non-code Stage 0 (legal and rights checks, plus one real Stripe test run) can stop the plan. A one-off drain command and a startup check cancel or block on pending orders from before the cart.

## How the existing system works, per this document

It is a pt-PT Django, PostgreSQL, python-telegram-bot and Stripe Checkout shop. The shop name "Jarvis", a PC-parts welcome text and 8 PC categories are written into the code. Admins are a list of ids in `.env`.

Checkout sells one product per order:

*   The Confirm button carries one product id.
*   `reservar_encomenda` locks one product row and takes stock at Confirm.
*   `garantir_sessao_pagamento` creates the Stripe session outside any lock and can create replacement sessions (apparently the decision called "D19").
*   A Stripe webhook confirms payment or cancels on expiry.
*   A cron-style command cancels abandoned orders, and `_devolver_stock` returns stock.

A series of security fixes, SEC-01 to SEC-13, hardened this path. They include a single polling process (SEC-09), callback allowlisting and routing (SEC-12), and upload handling (SEC-11). Tests for them exist: S-, H-, db- and T1–T5 series.

## Findings

1.  **Major. Lines 223, 560, 607, 842 ("D19").**
    *   **Problem:** D19 is used as if the reader knows it, both as the reason for removing replacement sessions and as the label for a group of tests to rewrite. Nowhere does the document say what D19 decided, so a reader can't tell what behaviour is being reversed, or why it existed.
    *   **Suggested change:** At first use (line 223), add one sentence: "D19 (an earlier decision in PROJECT_GUIDANCE.md): when a stored Stripe session expired, the bot created a replacement session with a new idempotency key. This design removes that."
2.  **Major. Lines 35, 55, 127, 283, 319, 358, 706 (SEC-01 to SEC-13).**
    *   **Problem:** The SEC ids carry the argument: "hardened in SEC-02 to SEC-13", "keeps SEC-09's one-process rule", "Remaining SEC-01 settings", "All existing SEC tests still pass". Only SEC-09 is partly explained. A reader can't list which guarantees G7 promises to keep, or what "remaining" SEC-01 work is left.
    *   **Suggested change:** Add a short glossary after "The Problem", with one line per SEC item this design touches (SEC-01, -02, -09, -11, -12 at least), or a link to SECURITY.md with section anchors.
3.  **Major. Line 690 conflicts with lines 186 and 492.**
    *   **Problem:** Line 690 says removing `encomendar_` from the allowlist means "old buttons get the expired-menu path in their handler". But the document says the allowlist rejects unknown prefixes with the generic "invalid request" alert before any handler runs (that is exactly why `cat_` stays on the allowlist at line 186). A reader concludes old `encomendar_` buttons will show "invalid request", not the expired-menu message.
    *   **Suggested change:** Choose one behaviour. Either keep `encomendar_` on the allowlist and route it to a small handler that shows `MENSAGEM_MENU_EXPIRADO`, or say plainly that old "Encomendar" buttons get the generic invalid-request alert.
4.  **Major. Lines 50–53, 62, 223, 288 (the claim of a "hard" 60-minute hold).**
    *   **Problem:** G5 promises at most 60 minutes for an order that has a payment link. The only mechanisms described are the Stripe "expired" webhook, and `garantir_sessao_pagamento` noticing an expired session when the customer comes back. The cleanup command is described only for orders without a link (line 52). If the webhook is lost and the customer never returns, nothing in the document cancels a linked order. A reader can't confirm the guarantee holds.
    *   **Suggested change:** State what `cancelar_encomendas_abandonadas --horas 1` does with orders that already have a session (for example: "it cancels any pending order older than 1 hour, with or without a session, via `cancelar_encomenda`"). Or soften G5 to "60 minutes once Stripe's expiry webhook arrives; otherwise at most 65 minutes via the cleanup".
5.  **Major. Lines 53, 138, 164, 222, 536 (why the cutoff is 28 minutes).**
    *   **Problem:** The 28-minute cutoff is repeated five times and never explained. A reader can't tell whether it is arbitrary, or derived from a Stripe constraint (for example, Stripe's minimum distance between session creation and `expires_at`, with a 2-minute margin). So they also can't tell whether it is safe to change.
    *   **Suggested change:** At line 53 or 138, add one sentence giving the reason, such as: "Stripe requires `expires_at` to be at least 30 minutes after session creation; 60 − 28 = 32 leaves a 2-minute margin."
6.  **Major. Lines 210, 547, 550 (what the "alterado" result returns).**
    *   **Problem:** The text says a new snapshot is built and returned, but not from what, or what happens if it can't be built:
        *   When the cart revision changed, is the new snapshot built from the current cart or from the old snapshot's lines?
        *   `criar_confirmacao` returns `None` when a line is inactive or short of stock (line 538). In that case, what does "alterado" carry, and what does the handler at line 583 draw? CUJ 2 expects a redraw with B shown as out of stock, which a refused snapshot can't provide.
        *   Is the new snapshot written inside the checkout transaction, while locks are held?
    *   **Suggested change:** In the Stage 2 result list or the pseudocode, say: the new snapshot is built from (cart lines if `revisao_carrinho` is set, otherwise the old snapshot's lines); if any line is flagged, return `("alterado", flagged lines)` with no snapshot, and the screen shows the flags with Confirm hidden; say whether this is written inside or after the transaction.
7.  **Major. Lines 67, 121, 291, 836 (where the webhook runs during switch-over).**
    *   **Problem:** The switch-over depends on the webhook confirming a paid legacy order while the bot refuses to start. That only works if the webhook is served by a process separate from the bot (for example, the Django web server). The document never says which process receives the webhook. A reader may think a stopped bot means a stuck deploy.
    *   **Suggested change:** In Principles (around line 127), add: "The Stripe webhook is served by the Django web process, which runs independently of the bot's polling process (`bot.py`); it keeps working while the bot is stopped or refusing to start."
8.  **Major. Lines 21 and 88 (Stage 0 contents).**
    *   **Problem:** The two lists of what Stage 0 contains don't match:
        *   Line 21 lists five items: code ownership, licence, demo images, invoicing position, and a Stripe test-mode run.
        *   Q1 lists three: "ownership and course-rights check", licence, and image rights. It leaves out invoicing and the Stripe run.

        "Course-rights" is never defined, so a reader can't tell what is being checked (was the code written for a course?). It is also unclear which items can stop the plan.
    *   **Suggested change:** Give one list of Stage 0 items, with a "blocks the plan: yes/no" note for each, and define course rights in one clause, for example "rights held by the course or school where the code was written".
9.  **Major. Lines 202 and 404 (`chave_confirmacao` and `chave_primeira_tentativa`).**
    *   **Problem:** "So `chave_primeira_tentativa` keeps its current format" assumes the reader knows the Stripe idempotency key is built from `chave_confirmacao`. Without that, the reason for storing the nonce in the old field is opaque, and so is A15's statement that legacy retries would be rejected.
    *   **Suggested change:** Add: "The Stripe idempotency key (`chave_primeira_tentativa`) is derived from `chave_confirmacao`; storing the nonce there keeps one key per order attempt."
10. **Nit. Lines 204–210 compared with lines 542 and 586 (missing "invalida" result).**
    *   **Problem:** The Stage 2 list of results leaves out `"invalida"` (snapshot not found), which the pseudocode and the handler both use.
    *   **Suggested change:** Add `"invalida"` to the list at line 204.
11. **Nit. Lines 153–154 and 545 ("age ≤ 30 min" of what).**
    *   **Problem:** "Age ≤ 30 min" doesn't say whether it is the snapshot's `criada_em` or the button timestamp `emitida_em`. The pseudocode uses `emitida_em`.
    *   **Suggested change:** Write "button timestamp (`emitida_em`) ≤ 30 min old" in the diagram.
12. **Nit. Line 670 (which flags hide "Finalizar compra").**
    *   **Problem:** "Hidden while a line is flagged" includes the price-changed flag. But G2 and line 246 say a price change only marks the line and doesn't block checkout.
    *   **Suggested change:** Write "hidden while a line is flagged insufficient stock or inactive".
13. **Nit. Lines 246 and 654 (function name).**
    *   **Problem:** The same function is called `preparar_confirmacao` in one place and `preparar_confirmacao_carrinho` in the other.
    *   **Suggested change:** Use one name in both places.
14. **Nit. Lines 62 and 291 (is the configuration check new?).**
    *   **Problem:** G10 presents the missing-configuration check as a deliverable, but line 291 calls it an "existing check".
    *   **Suggested change:** In G10, say the check already exists and only the legacy-order check is new.
15. **Nit. Lines 209 and 584 (customer with a pending order).**
    *   **Problem:** A customer with a pending order is blocked from a new checkout. The document doesn't say whether they can cancel it themselves or must wait up to 60 minutes.
    *   **Suggested change:** Add one line saying which buttons the "Já tens uma encomenda por pagar" screen offers (pay, cancel, or both).
16. **Nit. Lines 607, 621–622, 626, 860 (test ids).**
    *   **Problem:** The test ids S9–S14, S19, S22, db4, H1–H14 and T1–T5 are not described, so a reader can't check the rewrite list is complete.
    *   **Suggested change:** Add a short note on what each series covers, or link to the test file.
17. **Nit. Throughout (Portuguese identifiers).**
    *   **Problem:** Portuguese model and field names are used without English glosses: `Encomenda`, `Utilizador`, `Pagamento`, `MovimentoStock`, `RegistoAuditoria`, `estado`, `ativo`, `pago`, `reembolsado`, `PagamentoRecebido`.
    *   **Suggested change:** Add a small glossary table near the Technical Plan.
18. **Nit. Line 854 ("Step 6").**
    *   **Problem:** "Step 6" refers to a drafting process the reader doesn't know.
    *   **Suggested change:** Remove it, or write "once approved".

**Not checked:** the document was read on its own, as instructed, so nothing in it was verified against the real code. Findings 3, 4 and 7 are about behaviour; if the author confirms the code already works the way the clarified text would say, they shrink to wording fixes.
