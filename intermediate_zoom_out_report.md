# Goldfish Review Report: Stage 8 (Intermediate Zoom Out)

**Target:** `D:\Projects\telegram-sales-inventory-bot\docs\designs\cart_resellable_shop.md` (lines 1-874, unchanged since Stage 5)

**Focus:** whether the remedies in the Stage 5 zoom-out report, the Stage 6 comprehension report and the Stage 7 critic report add complexity or scope, which of them have simpler fixes, and whether the overall scope is still right.

## Reviewer Status

| Reviewer | Status |
| --- | --- |
| `egm-zoom-out` | completed (one instance) |

Severity breakdown: 2 Blockers, 7 Majors, 6 Nits.

The synthesizer spot-checked one code claim. `core/services.py` line 460 does filter the abandoned-order sweep with `pagamento__isnull=True`, and `cancelar_encomenda` raises `PagamentoRecebido` (line 436). No other code claims were re-checked.

## Prioritized Findings (Blockers -> Major -> Nits)

### B1. One cleanup sweep makes the whole legacy switch-over unnecessary - Severity: Blocker
* **Reviewer Lens:** `egm-zoom-out`
* **Affected Scope:** lines 50-53 (G5), 67 (G12), 117-121 (CUJ 5), 223, 285-299, 790-812, 836
* **Relates to:** Stage 5 M1, M3, M4; critic M1, M9, N6; comprehension 4, 7
* **Discrepancy Description:** Each earlier remedy adds machinery:
  * Critic M9 keeps the drain command and adds a per-order guard and a longer deploy order.
  * Stage 5 M4 and critic N6 add a threshold in minutes and a `--minutos` option.
  * Comprehension 7 asks for text about which process serves the webhook, which is only needed because of the startup gate.

  Meanwhile, `cancelar_encomenda` (services.py 407-449) already handles every payment-session state safely under the order lock. The only reason the abandoned-order sweep skips orders with a payment link is the `pagamento__isnull=True` filter (services.py 460) and the matching check around line 474.
* **Rationale & Recommendation:**
  * Remove that filter and check. In the command, catch `PagamentoRecebido` and `stripe.error.StripeError`, as `EncomendaAdmin.marcar_cancelada` does (admin.py 162-173). Keep `--horas 1`.
  * Because a session's `expires_at` is 60 minutes after the order, any pending order older than an hour is dead. G5 then holds whether or not the order has a link, and even if the webhook is lost.
  * Legacy orders need no special code. Delete `drenar_encomendas_antigas` (item 42), the startup gate (item 43), the legacy token parsing and `confirmar_encomenda_antigo` (lines 214, 574, 587, 589), and G12. CUJ 5 becomes: back up, stop the bot and cron, migrate, start.
  * Keep critic M9's "pause cron during migrate" as a one-line deploy note.
  * Trade-off: orders that never got a payment link are held for up to 65 minutes instead of Stage 5 M4's 35. Update A11's wording to match.
  * Risk to note: the cron job now makes Stripe calls while holding the order lock. Admin cancellation already does the same.

### B2. Drop the lock on the customer row; don't add `no_key` or move the cart onto `Utilizador` - Severity: Blocker
* **Reviewer Lens:** `egm-zoom-out`
* **Affected Scope:** lines 49, 133-137, 156, 548, 559, 622, 860
* **Relates to:** critic B1; Stage 5 M5, M6
* **Discrepancy Description:** The deadlock critic B1 found is real. The audit insert in `cancelar_encomenda` (services.py 444) takes a key-share lock on the customer row (`Utilizador`), which conflicts with checkout's `FOR UPDATE` on the same row. The earlier reports offer three different fixes:
  * `select_for_update(no_key=True)` on three models (critic B1);
  * moving the cart onto `Utilizador` (Stage 5 M6, option 2);
  * dropping the customer-row lock (Stage 5 M5).
* **Rationale & Recommendation:** Adopt Stage 5 M5 together with M6 option 1:
  * Take no lock on `Utilizador`.
  * Do the "already has a pending order" check without a lock, only to show a friendly message.
  * Enforce the rule with the partial unique constraint, using the savepoint plus `IntegrityError` pattern already at services.py 199-206.
  * Keep the `Carrinho` row as the single per-customer lock.

  This removes the lock cycle without new lock modes. Line 136 then becomes accurate. The mutation check at line 860 becomes "remove the constraint". Extend test T7 so that checkout and cancellation are for the same customer.

### M1. On "alterado", write nothing and show the cart - Severity: Major
* **Reviewer Lens:** `egm-zoom-out`
* **Affected Scope:** lines 47 (G2), 99-103 (CUJ 2), 157, 210, 547, 550, 583
* **Relates to:** Stage 5 M2; critic M2; comprehension 6
* **Discrepancy Description:** Critic M2 adds a sixth result code (`bloqueado`) and keeps building a snapshot inside the locked transaction. Stage 5 M2 keeps two ways of redrawing the screen.
* **Rationale & Recommendation:**
  * `"alterado"` carries no data and the transaction rolls back.
  * The handler shows the cart screen. `ver_carrinho` already flags price changes, missing stock and inactive products. The customer taps "Finalizar" again.
  * In Stage 2, show the product screen instead.

  This leaves one snapshot builder, which never runs under locks, and no new result code. The cost is one extra tap in a rare case. Rewrite the "Then" of CUJ 2 to match.

### M2. Drop the button timestamp completely - Severity: Major
* **Reviewer Lens:** `egm-zoom-out`
* **Affected Scope:** lines 150, 154, 214, 289, 545, 572-575, 786-788
* **Relates to:** Stage 5 M7; critic M4, N1; comprehension 11
* **Discrepancy Description:** Critic M4 keeps the timestamp the button supplies and adds a `DELETE ... WHERE NOT EXISTS` with SKIP LOCKED.
* **Rationale & Recommendation:**
  * Use Stage 5 M7 alone. The token is a prefix plus a nonce, and the 30-minute age check uses the snapshot's `criada_em` under its lock.
  * Cleanup deletes only snapshots older than 45 minutes, so it cannot race a confirmation, and a plain queryset delete is enough. `on_delete=PROTECT` is already specified at line 526; critic M4 only reviewed up to line 426.
  * `_base36`, `_TS` and the future-time tolerance can be removed from `confirmacao.py`. Critic N1 and comprehension 11 then no longer apply.
  * Further simplification: keep the existing `confirmar_encomenda_` prefix with a single 16-hex value. Old three-part buttons fail the pattern and get the existing expired screen (customer.py 202-209). That removes the need for the new `confirmar_carrinho_` allowlist entry and for routing two prefixes.

### M3. Old "Encomendar" buttons: keep the prefix and route it to "add to cart" - Severity: Major
* **Reviewer Lens:** `egm-zoom-out`
* **Affected Scope:** lines 251, 256-263, 662, 671, 688-690
* **Relates to:** Stage 5 N3; comprehension 3; critic N2
* **Discrepancy Description:** The earlier remedies add a handler for expired menus or rewording.
* **Rationale & Recommendation:** Give the product button the callback `encomendar_<id>` and route `encomendar_` to the add-to-cart handler. This removes the `carrinho_add_` prefix and its allowlist entry, old buttons keep working, and line 690 can be deleted.

### M4. Keep or defer the customer cancel button and "Repeat order" together - Severity: Major (the author decides)
* **Reviewer Lens:** `egm-zoom-out`
* **Affected Scope:** lines 54 (G6), 105-109 (CUJ 3), 247, 255, 584, 655, 664, 673, 703-705
* **Relates to:** Stage 5 M8; critic M7, N5, M12; comprehension 15
* **Discrepancy Description:** "Repeat order" brings a service, a handler, a prefix, a keyboard, tests, the hoarding concern (critic M7) and the double-tap issue (critic N5). Stage 5 M8 adds a customer cancel button on top. Critic M7 then proposes cooldowns, which add more state.
* **Rationale & Recommendation:**
  * Option (a), keep both in a minimal form:
    * Reuse `cancelar_encomenda_<id>`. It is already on the allowlist and deliberately unrouted (sanitization.py 40; `UNROUTED_PREFIXES` in telegram_bot/tests.py).
    * One handler calls `cancelar_encomenda` and then `repetir_encomenda`.
    * Reject the cooldown and record the risk as accepted. One pending order per customer and the 60-minute window already limit how long stock is held.
    * For critic N5, make the merge set quantities instead of adding them.
  * Option (b), defer both: the "Já tens uma encomenda por pagar" screen just shows the payment link through `_mostrar_pagamento`.

  Option (b) gives the smaller release. G6 is a settled decision, so the author chooses.

### M5. Owner rights on orders: change G9's wording, not the permission code - Severity: Major
* **Reviewer Lens:** `egm-zoom-out`
* **Affected Scope:** lines 61 (G9), 115 (CUJ 4), 277-282
* **Relates to:** critic M5
* **Discrepancy Description:** The actions in `EncomendaAdmin` have no `allowed_permissions`. However, CUJ 4 already lets the owner advance and refund orders from the Telegram admin panel, so a "read-only orders" rule in Django admin protects nothing.
* **Rationale & Recommendation:** Reword G9 to say the owner manages orders through the domain actions in either panel. Grant add, change and view on `Produto` and `Categoria`. No permission code or per-action tests are needed.

### M6. Admins listed in `.env`: add one handover step, not bootstrap logic - Severity: Major
* **Reviewer Lens:** `egm-zoom-out`
* **Affected Scope:** lines 57-60 (G9), 272-276, 836
* **Relates to:** critic M6
* **Discrepancy Description:** The proposed rules ("bootstrap only while no admin exists", "copy to the database once") add state.
* **Rationale & Recommendation:** Add a step to DEPLOYMENT.md: "remove the reseller's id from `ADMIN_TELEGRAM_IDS` and restart". The "without restart" in G9 applies to the database flag only.

### M7. Keep the new Risks section short - Severity: Major
* **Reviewer Lens:** `egm-zoom-out`
* **Affected Scope:** a new section between lines 305 and 306
* **Relates to:** critic B2; comprehension 2, 17
* **Discrepancy Description:** The proposed 14-row table repeats risks the design already handles. Several rows exist only because of mechanisms that B1 removes. One row proposes a "last cleanup run" heartbeat, which is a speculative feature.
* **Rationale & Recommendation:** Write about 8 one-line rows, each pointing to its mitigation:
  * deadlock;
  * cron not installed;
  * owner text breaking Markdown;
  * forged callbacks;
  * snapshot growth;
  * no rollback;
  * Stage 0;
  * hoarding, if Repeat order stays.

  Drop the heartbeat. Replace the SEC glossary with a link to SECURITY.md. Keep the Portuguese glossary to about 10 terms, or skip it.

### N1. Fold `limpar_rascunhos` into the existing cron command - Severity: Nit
* **Reviewer Lens:** `egm-zoom-out`
* **Affected Scope:** lines 289, 782-793, 814-816
* **Relates to:** new
* **Rationale & Recommendation:** Use one command with one cron entry. Alternatively, keep two commands but document the two lines in DEPLOYMENT.md instead of shipping a separate crontab file.

### N2. Escape owner text with the existing helpers - Severity: Nit
* **Reviewer Lens:** `egm-zoom-out`
* **Affected Scope:** lines 186, 271
* **Relates to:** critic M8
* **Rationale & Recommendation:** Use `md()` or `parse_mode=None`, as customer.py already does elsewhere. The category header at customer.py line 40 needs `md()`. Don't add validation when the admin saves.

### N3. Drop the snapshot `moeda` field and the cart count on the menu button - Severity: Nit
* **Reviewer Lens:** `egm-zoom-out`
* **Affected Scope:** lines 197, 252, 521, 656, 672
* **Relates to:** new
* **Rationale & Recommendation:** Currency is fixed at EUR, and changing it is a stated non-goal. "Carrinho (N)" adds a query and a parameter to every caller of `teclado_menu_principal`. Use a plain "Carrinho" button.

### N4. Drop the separate snapshot admin page - Severity: Nit
* **Reviewer Lens:** `egm-zoom-out`
* **Affected Scope:** lines 201, 601
* **Relates to:** Stage 5 N6
* **Rationale & Recommendation:** `EncomendaAdmin` already shows the linked snapshot read-only.

### N5. Make `revisao_carrinho` required once Stage 3 lands - Severity: Nit
* **Reviewer Lens:** `egm-zoom-out`
* **Affected Scope:** lines 196, 213, 546
* **Relates to:** new (same reasoning as A12)
* **Rationale & Recommendation:** The nullable revision and its branch exist only for Stage 2's one-product path. Stage 3 should make the field required and remove that branch.

### N6. Remove repeated text and explain the 28-minute cutoff in one sentence - Severity: Nit
* **Reviewer Lens:** `egm-zoom-out`
* **Affected Scope:** lines 191-199 and 516-523, 204-210 and 541-557, 246 and 654
* **Relates to:** Stage 5 N1, N2; comprehension 5, 10, 13; critic M1
* **Rationale & Recommendation:** Deleting the duplicated text resolves comprehension 10 and 13. Explain the cutoff in one sentence: Stripe requires `expires_at` to be at least 30 minutes after the session is created, so 60 minus 28 leaves a 2-minute margin. This Stripe rule has not been checked against Stripe's current documentation.

## Remedies That Conflict With Each Other

1. **Switch-over:** Stage 5 M1 vs critic M9 vs comprehension 7. Resolve with B1.
2. **"alterado":** Stage 5 M2 vs critic M2. Resolve with M1.
3. **Button timestamp:** Stage 5 M7 vs critic M4 vs comprehension 11. Resolve with M2.
4. **Per-customer lock:** Stage 5 M6 option 2 vs Stage 5 M5 with M6 option 1 vs critic B1 (`no_key`). Resolve with B2.
5. **Cleanup threshold:** Stage 5 M4 and critic N6 (30 minutes) vs a single 60-minute threshold. Resolve with B1.
6. **Customer cancel vs Repeat-order limits:** Stage 5 M8 vs critic M7. Resolve with M4.

## Verdict on Overall Scope

The core design is the right size: one copy per shop, a cart that becomes a snapshot that becomes an order, product locks taken in id order, and reuse of the existing hardened code once an order exists. Stage 4 is the minimum a handover to a reseller needs.

The scope is too wide in two places:
* **The legacy switch-over:** G12, CUJ 5, the drain command, the startup gate and the legacy tokens can be removed using B1.
* **Customer-recovery features:** Repeat order, plus the proposed cancel button and anti-hoarding limits, are candidates to defer.

Taking B1, B2, M1, M2 and M3 removes about 6 of the 47 planned items and adds no new mechanism. Taking the earlier remedies as written would add about 5 mechanisms.

## Not Checked

* `core/models.py`, `telegram_bot/tests.py`, and the H/S/T test ids.
* Stripe's 30-minute minimum for `expires_at`, against Stripe's documentation.
* B1 assumes no legacy order row is missing its product name. Otherwise, such an order shows the error screen until the sweep cancels it, which may be acceptable.
