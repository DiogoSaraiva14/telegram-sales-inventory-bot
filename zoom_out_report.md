# Goldfish Review Report: Stage 5 (Initial Zoom Out)

**Target:** `D:\Projects\telegram-sales-inventory-bot\docs\designs\cart_resellable_shop.md` (lines 1-874)

## Reviewer Status

| Reviewer | Status |
| --- | --- |
| `egm-zoom-out` | completed |
| `egm-inventive-catalyst` | completed |

**Summary:** 0 Blockers, 8 Majors, 7 Nits. Both reviewers say the design's core is sound: the draft, snapshot and order split, the lock ordering, the JSON snapshot lines and the reuse of the hardened post-order code paths. Most of the findings are about mechanisms that can be removed or merged, and about two places where the design promises more than it delivers.

Two findings were merged because both reviewers raised them: the legacy switch-over machinery (M1) and the `"alterado"` snapshot (M2).

I checked these claims against the code and the design myself:
- `encomendas_abandonadas` filters on `pagamento__isnull=True` (core/services.py line 460).
- The wording at design lines 53, 223, 690, 767 and 860 is as the reviewers quote it.

I did not check the other claims.

---

## Prioritized Findings (Blockers -> Major -> Nits)

### M1. The legacy switch-over machinery is heavier than the situation needs, and the startup gate puts the whole shop at risk - Severity: Major
* **Reviewer Lens:** `egm-zoom-out`, `egm-inventive-catalyst`
* **Affected Scope:** G12 (line 67), CUJ 5 (lines 117-121), "Scheduled jobs and switch-over" and startup checks (lines 290-299), items 42-43 (lines 795-812), DEPLOYMENT checklist (line 836), and the legacy-button parts of items 15, 16 and 18 (lines 214, 574, 587, 589, 597).
* **Discrepancy Description:** The design says no shop runs the bot yet:
  - A13 (line 392) rejects a rollback setting for that reason.
  - A14 (line 399) drops compatibility with old buttons for that reason.

  It still adds permanent code for upgrading a live copy:
  - a drain command;
  - a startup gate (`encomendas_antigas_pendentes` raising `SystemExit`);
  - two legacy token formats still parsed in `ler_callback`;
  - a `confirmar_encomenda_antigo` handler.

  The gate also trades availability for migration safety. A single paid legacy order waiting for a webhook keeps the whole bot offline, and line 836 admits this. The real danger is narrow: only calling `Session.create` on a legacy order breaks. Fetching an existing session does not.
* **Rationale & Recommendation:** Remove the gate, the drain command and the legacy-token parsing. Pick one of these two lighter options:
  - **Check at the point of use (catalyst).** In `garantir_sessao_pagamento`, when `confirmacao` is null and there is no payment, never call `Session.create`. Cancel the order and return `"expirada"`, the same branch the 28-minute cutoff uses. Legacy orders are then swept like any other (see M3). Add one test for this branch.
  - **One manual deploy step (zoom-out).** In DEPLOYMENT.md, add: "before migrating, cancel pending orders with the Django admin bulk cancel". That action already runs `cancelar_encomenda`.

  Either way:
  - Send the `confirmar_encomenda_` prefix straight to the expired screen, or remove it from the allowlist. See N3.
  - Keep the partial constraint's exclusion of rows with a null `confirmacao`.
  - Simplify CUJ 5 to: back up, migrate, start.

  This changes author decision G12, so the author must agree to it.

### M2. `"alterado"` promises a new snapshot that the snapshot builder will not write, and it writes while locks are held - Severity: Major
* **Reviewer Lens:** `egm-inventive-catalyst`, `egm-zoom-out`
* **Affected Scope:** G2 (line 47), CUJ 2 (lines 99-103), the Stage 2 result list (line 210), item 14 pseudocode (lines 547 and 550), and item 16 (line 583). These conflict with lines 246, 538 and 654.
* **Discrepancy Description:**
  - `criar_confirmacao` and `preparar_confirmacao_carrinho` refuse to build a snapshot while any line is inactive or short on stock.
  - Yet line 210 says `"alterado"` returns "a new snapshot … with the changed lines marked", and line 583 shows "sem stock suficiente" and "indisponível" on that screen.
  - In CUJ 2, product B is out of stock, so the redraw it describes cannot be produced as specified.
  - Building and inserting the snapshot inside the Confirm transaction also keeps every product lock held for longer, and it needs a second snapshot builder that runs under locks.
* **Rationale & Recommendation:**
  - Return `("alterado", diff)` (or `None`) and roll back, writing nothing.
  - The handler then redraws after the commit:
    - if any line has a blocking flag, show the cart (Stage 3) or the product screen (Stage 2), using `ver_carrinho`'s existing flags;
    - if only prices changed, call `criar_confirmacao` and draw the new confirmation screen with "preço alterado".
  - Rewrite CUJ 2's "Then" to match: the cart is shown with A's new price marked and B flagged.

### M3. The "hard 60-minute hold" depends on the webhook arriving or the customer coming back - Severity: Major
* **Reviewer Lens:** `egm-inventive-catalyst`
* **Affected Scope:** G5 (lines 50-53), Stage 2 "Session cutoff" (lines 221-224), the cron file (lines 287-289), and items 14 and 44 (lines 562-565, 814-816).
* **Discrepancy Description:** Line 223 says the expired-session path "also covers a webhook that never arrives". That is only true if the customer taps again. `cancelar_encomendas_abandonadas` only selects orders with no payment (`pagamento__isnull=True`, core/services.py line 460). If the webhook endpoint is down, an order with a payment link holds its stock for as long as Stripe keeps retrying, which can be days.
* **Rationale & Recommendation:**
  - Extend the 5-minute cleanup to also select pending orders that have a payment and are older than 60 minutes plus a margin (for example 65 minutes).
  - Run each one through the existing `cancelar_encomenda`. It already does the right thing under the order lock: if the session is `complete`, it raises `PagamentoRecebido` and the order is skipped; if it is `open` or `expired`, the order is cancelled and its stock returned.
  - Add tests for orders at 64 and 66 minutes, and for a `complete` session being skipped.
  - Correct line 223.

### M4. Orders with no payment link are dead at 28 minutes but keep their stock until 60-65 minutes - Severity: Major
* **Reviewer Lens:** `egm-inventive-catalyst`
* **Affected Scope:** G5, second bullet (line 52); the cron file (line 288, `--horas 1`).
* **Discrepancy Description:** After the 28-minute cutoff, an order without a link can never get one. If the customer never taps again, cleanup still waits an hour, so the stock is unsellable for about 30 extra minutes. That works against A11's stated reason for the short window (line 381: "protect scarce stock"). The one-hour figure comes from the old command's hour-based setting. It is not a real limit.
* **Rationale & Recommendation:**
  - Let the cleanup command take minutes, and cancel orders with no payment once they are older than about 30 minutes.
  - Combine this with M3 into one sweep with a rule per order age: no payment and older than 30 minutes, cancel; has a payment and older than 65 minutes, reconcile.
  - Move the SEC-03 test boundaries to minutes.
  - Update G5 to read "at most about 35 minutes without a link".

### M5. Two mechanisms enforce one pending order per customer, and the planned mutation check cannot fail - Severity: Major
* **Reviewer Lens:** `egm-zoom-out`
* **Affected Scope:** lock rules (line 134), the diagram (line 156), lines 548 and 559, test T8 (line 622), and the Verification Plan (line 860).
* **Discrepancy Description:** The user-row lock and the partial unique constraint each enforce the rule on their own. Line 860 says to "remove ... the user lock and confirm that the test fails". With the constraint still in place, T8 will still pass, so this mutation check is wrong as written.
* **Rationale & Recommendation:**
  - Keep the constraint. PROJECT_GUIDANCE.md prefers invariants at the database.
  - Do the pending check without a lock, only to give the friendly early message.
  - Create the `Encomenda` inside a savepoint before any stock write, and map an `IntegrityError` to `"pendente"`. `reservar_encomenda` already uses this pattern at services.py lines 199-206.
  - Drop the user lock from the lock order.
  - Change the mutation check to "remove the constraint", or drop it.

### M6. The separate `Carrinho` parent adds a table and a second per-customer lock - Severity: Major
* **Reviewer Lens:** `egm-zoom-out`
* **Affected Scope:** Stage 3 models (lines 231-234), item 22 (line 633), item 24 `_carrinho_bloqueado` (line 643), and the lock order on line 134 ("snapshot, then cart, then customer").
* **Discrepancy Description:** `Carrinho` is one-to-one with `Utilizador` and holds only `revisao` and `atualizado_em`. Checkout locks the cart row and the user row, which are two rows for the same customer.
* **Rationale & Recommendation:** Take exactly one per-customer lock:
  - If M5 is adopted, keep `Carrinho` as that lock and drop the user lock.
  - Otherwise, move `revisao_carrinho` and `carrinho_atualizado_em` onto `Utilizador`, point `LinhaCarrinho` at `Utilizador`, and delete `Carrinho`.

### M7. The Confirm token's timestamp duplicates the snapshot's `criada_em`, and the client can forge it - Severity: Major
* **Reviewer Lens:** `egm-zoom-out`
* **Affected Scope:** lines 150 and 214, line 545 (`confirmacao.expirada(emitida_em)`), and items 15 and 16 (lines 572-575).
* **Discrepancy Description:** The token's `<ts>` comes from callback data, which a modified client can forge, so the 30-minute window could stretch to the 45-minute cleanup. The snapshot already stores `criada_em` on the server, and the diagram on line 154 already describes the age check against the snapshot. Keeping the timestamp also keeps a base36 codec and a second clock source.
* **Rationale & Recommendation:**
  - Make the token `confirmar_carrinho_<nonce>`.
  - Check `snap.criada_em` inside the locked transaction.
  - Change the signature to `criar_encomenda_de_confirmacao(telegram_user, nonce)`.
  - `confirmacao.py` then shrinks to the prefix, `gerar_chave` and a nonce regex.

### M8. A customer cannot cancel their own pending order, so a mistake blocks them for up to an hour - Severity: Major (advisory)
* **Reviewer Lens:** `egm-inventive-catalyst`
* **Affected Scope:** G3 (line 48), the results list (line 209), item 16's `pendente` handling (line 584), A10 (lines 371-375), and T8 (line 622).
* **Discrepancy Description:** With one pending order per customer and no cancel button, a customer who confirms and then wants a change sees "Já tens uma encomenda por pagar". They must pay or wait for cleanup. A10 rejects an edit path because of a race with a payment finishing in another tab. The existing `cancelar_encomenda` already settles that race under the order lock:
  - a `complete` session raises `PagamentoRecebido`, so nothing is cancelled;
  - an `open` session is expired before the stock is returned;
  - a payment that completes anyway is refunded through SEC-06.
* **Rationale & Recommendation:**
  - Add a "Cancelar e voltar ao carrinho" button on the customer's own pending order. It runs `cancelar_encomenda(id, 'cliente')` and then `repetir_encomenda`.
  - On `PagamentoRecebido`, show the existing "don't pay again" screen.
  - Add the new prefix to the allowlist, with handler tests. The per-user rate limit bounds repeated confirm and cancel loops.

  This reverses author decision A10, so the author must agree to it.

---

### N1. Repetition between the Technical Plan and the Detailed Plan has already caused drift - Severity: Nit
* **Reviewer Lens:** `egm-zoom-out`
* **Affected Scope:** lines 204-210 against 542-586, line 246 against 654, line 223 against 564, and lines 191-199 against 516-523.
* **Discrepancy Description:** The three drifts:
  - the result list at lines 204-210 omits `"invalida"`;
  - the service is `preparar_confirmacao` at line 246 but `preparar_confirmacao_carrinho` at line 654;
  - the snapshot fields are specified twice.
* **Rationale & Recommendation:** Keep field lists and return codes only in the Detailed Plan. The Technical Plan should name the interfaces and describe the flow.

### N2. The Detailed Plan drops to code level - Severity: Nit
* **Reviewer Lens:** `egm-zoom-out`
* **Affected Scope:** the pseudocode at lines 541-557, and the exact expressions at lines 537 and 561.
* **Discrepancy Description:** The pseudocode repeats rules the Technical Plan already states, and it duplicates the diagram at lines 152-160.
* **Rationale & Recommendation:** Replace the pseudocode with the ordered list of checks and their result codes, or point to the diagram.

### N3. Removing `encomendar_` does not give the stated "expired menu" - Severity: Nit
* **Reviewer Lens:** `egm-zoom-out`
* **Affected Scope:** line 690.
* **Discrepancy Description:** Once the prefix leaves `ALLOWED_PREFIXES`, `validar_callback` (security/sanitization.py lines 55-65) rejects the button before any handler runs. The user sees the generic invalid alert, not the expired menu.
* **Rationale & Recommendation:** Either keep the prefix routed to a tiny expired-menu handler, or reword line 690 to say old buttons get the generic alert. Treat `confirmar_encomenda_` the same way.

### N4. G5 still says payment links are "replaced" - Severity: Nit
* **Reviewer Lens:** `egm-zoom-out`
* **Affected Scope:** line 53.
* **Discrepancy Description:** D19 replacement is removed (lines 223 and 560), so links are never replaced.
* **Rationale & Recommendation:** Change the line to "Payment links are created only while the order is at most 28 minutes old."

### N5. "Never calls Stripe while holding a lock" is only partly true - Severity: Nit
* **Reviewer Lens:** `egm-zoom-out`
* **Affected Scope:** lines 224 and 565.
* **Discrepancy Description:** The new branches call `cancelar_encomenda`, which holds the order lock across `Session.retrieve` and `Session.expire` (services.py lines 416-438). That is acceptable, but the claim is too broad.
* **Rationale & Recommendation:** Change it to "Session creation never runs under a lock. Cancellation keeps its existing behaviour (SEC-06)."

### N6. The admin registration for the snapshot may not be needed - Severity: Nit
* **Reviewer Lens:** `egm-zoom-out`
* **Affected Scope:** line 201 and item 19 (line 601).
* **Discrepancy Description:** `EncomendaAdmin` already shows `confirmacao` as a read-only field. A separate admin page would mostly list drafts that are deleted after 45 minutes.
* **Rationale & Recommendation:** Drop the separate registration unless browsing unconfirmed snapshots is wanted.

### N7. Wrong count of settings variables - Severity: Nit
* **Reviewer Lens:** `egm-zoom-out`
* **Affected Scope:** line 767.
* **Discrepancy Description:** The line says "both variables", but there are three: `DJANGO_HTTPS`, `DJANGO_BEHIND_PROXY` and `CSRF_TRUSTED_ORIGINS`.
* **Rationale & Recommendation:** Change it to "all three variables".

---

## Confirmed sound (no action)

- The existing code is reused, with nothing reinvented: `_devolver_stock`, `cancelar_encomenda`, `cancelar_se_abandonada`, the webhook handlers, the savepoint plus `IntegrityError` pattern, and the `SoLeitura` admin mixin. `_bloquear_produtos` is new but justified.
- These choices hold, with no false compromise found:
  - the lock order: snapshot, cart, user, then products by id, against order, payment, then products;
  - the JSON snapshot instead of a child table (A16);
  - the 60-minute window with the 28-minute cutoff (A11);
  - the admin flag stored in the database (A18);
  - one copy of the bot per shop (A2).

## Gaps in coverage

- Neither reviewer checked the "about 23" test call sites, the H1-H14 and S9-S22 test ids, or models.py and tests.py in depth.
- `core/admin.py` was not supplied, so nobody checked whether the admin bulk cancel suggested in M1 locks products in a compatible order.
