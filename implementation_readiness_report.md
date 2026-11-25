# Goldfish Review Report: Stage 9 (Implementation Readiness Test)

**Target:** `docs/designs/cart_resellable_shop.md` (lines 427-874, "## Detailed Implementation Plan" to the end)

**Verdict:** NOT READY. Several core contracts are undefined or contradict each other: the checkout `alterado` result, the stage boundaries, the snapshot JSON types, the migration test, the shop-owner permissions and the threaded-test harness. An engineer would have to come back with questions.

## Reviewer Status

| Reviewer | Status |
| --- | --- |
| `egm-implementation-readiness` | completed (on the retry; the first run hit an API rate limit) |
| `egm-codebase-details` | completed (on the retry; the first run hit an API rate limit) |
| `egm-style` | completed (on the retry; the first run hit an API rate limit) |

Severity breakdown after deduplication: 4 Blockers, 22 Majors, 18 Nits.

Limits: no reviewer ran any code. The PostgreSQL lock and Django admin behaviour claims (M1, M2, M14) come from known framework behaviour, not from a test run. Neither code reviewer fully read `goldfish_cart_report.md`, `ARCHITECTURE.md` or `PROJECT_GUIDANCE.md`. Codebase-details read `SECURITY.md` and `PROJECT_GUIDANCE.md` only for the D19 references.

---

## Blockers

### B1. The `alterado` result contradicts `criar_confirmacao` and has no defined shape
* **Reviewer Lens:** `egm-implementation-readiness`, `egm-codebase-details`
* **Affected Scope:** lines 538, 547, 550, 583, 610, 663
* **Discrepancy Description:** Lines 547 and 550 return `("alterado", new snapshot)`. Line 538 says that when a product is inactive or short of stock, `criar_confirmacao` refuses and returns "None plus the flagged lines", so for the main `alterado` case (CUJ 2, out of stock) no new snapshot exists. Line 610's test says "`alterado` ... with nothing written", but a new `ConfirmacaoCarrinho` row is written. The plan also doesn't say:
  - whether the rebuilt snapshot comes from the old snapshot's lines or from the cart's current lines (items added later would be lost, G3);
  - where the "preço alterado" mark gets the old price from, since the snapshot JSON (line 523) stores only the current price.
* **Rationale & Recommendation:** Define one return type for both functions, for example `criar_confirmacao -> (snapshot | None, linhas)` where each line has `{produto_id, nome, quantidade, preco, preco_anterior, flags}`, and `("alterado", (snapshot | None, linhas))`. State that a cart rebuild calls `preparar_confirmacao_carrinho` on the cart's current lines and `revisao`. State that the price mark compares against `preco_ao_adicionar` on cart screens and against the old snapshot price after `alterado`. Reword the test as "no order, line, stock or movement written; at most one new snapshot". State that with snapshot None the screen has no Confirm button.

### B2. The snapshot JSON cannot hold `Decimal` prices
* **Reviewer Lens:** `egm-codebase-details`
* **Affected Scope:** lines 523, 538, 550
* **Discrepancy Description:** `linhas` is a `JSONField` holding `preco_unitario`. Prices are `DecimalField` (core/models.py:49, 133). The default JSON encoder raises `TypeError` on `Decimal`, so the first `criar_confirmacao` save fails. The comparison "price != snapshot price" (line 550) has no defined type.
* **Rationale & Recommendation:** Store `preco_unitario` and `total` as strings (`str(Decimal)`), and compare with `Decimal(linha['preco_unitario']) != produto.preco`. The alternative is `JSONField(encoder=DjangoJSONEncoder)`, still parsing the values back with `Decimal(...)`.

### B3. The specified migration test cannot run
* **Reviewer Lens:** `egm-codebase-details`
* **Affected Scope:** lines 454, 506
* **Discrepancy Description:** 0010's reverse raises `IrreversibleError`. A `MigrationExecutor` test that "migrates from 0009 to 0011" must first migrate the test database back to 0009, which runs 0010's reverse and raises. Executor tests also need `TransactionTestCase` (line 790 has the same need).
* **Rationale & Recommendation:** Choose one and write it into the plan:
  - make 0010's reverse `RunPython.noop` (0011's reverse already restores the text field with `default='cpu'`);
  - test the forward function directly against the historical apps from `('core', '0010_categoria_dados')`;
  - drop the executor test.

  Name `TransactionTestCase` for migration tests.

### B4. Items 41-44 (scheduled jobs and switch-over) belong to no stage, but each stage is one PR
* **Reviewer Lens:** `egm-implementation-readiness`
* **Affected Scope:** lines 429, 780-816
* **Discrepancy Description:** Stage 2 changes `parametros_checkout` (rounding, `expires_at`) and adds the `confirmacao` field that defines "legacy". After that, any pending legacy order hits an idempotency-parameter mismatch. So `drenar_encomendas_antigas` (item 42), the startup check (item 43) and the `--horas 1` cron (item 44, needed for G5) must ship with Stage 2. `limpar_rascunhos` (item 41) needs snapshots (Stage 2) and carts (Stage 3).
* **Rationale & Recommendation:** Put items 42-44 and the snapshot half of item 41 in Stage 2, and the cart half of item 41 in Stage 3. Alternatively, label each item with its stage explicitly.

---

## Major

### M1. A deadlock remains through the foreign-key lock on the user row
* **Reviewer Lens:** `egm-codebase-details`
* **Affected Scope:** line 548 (context: lines 136-137)
* **Discrepancy Description:** Checkout takes `FOR UPDATE` on the customer's `Utilizador` row and then waits on product locks. `cancelar_encomenda` locks products in `_devolver_stock` and then inserts `RegistoAuditoria(utilizador=...)` (core/services.py:441-448). That insert needs `FOR KEY SHARE` on the user row, which conflicts with `FOR UPDATE`. The cycle occurs when an admin cancels or refunds a *confirmed* order while the same customer checks out a shared product. The premise that "products are the only place two transactions can wait on each other" ignores foreign-key locks. Not verified by a run.
* **Rationale & Recommendation:** Lock the user with `select_for_update(no_key=True)`, and use `no_key=True` in `_bloquear_produtos` too. Add a T7 variant where the same customer's confirmed order is cancelled with a refund. Confirm the deadlock with a threaded test before changing the design.

### M2. The admin bulk product actions lock rows in no fixed order
* **Reviewer Lens:** `egm-codebase-details`
* **Affected Scope:** lines 537, 860
* **Discrepancy Description:** `ProdutoAdmin.ativar_produtos` and `desativar_produtos` call `queryset.update(ativo=...)` (core/admin.py:62-65, 82-85). That locks several product rows in scan order. So `_bloquear_produtos` is not "the only place products are locked for several lines".
* **Rationale & Recommendation:** Inside `transaction.atomic()`, lock first with `select_for_update(no_key=True).order_by('pk')`, then update. List both actions under item 6.

### M3. The `IntegrityError` fallback needs a savepoint
* **Reviewer Lens:** `egm-implementation-readiness`, `egm-codebase-details`, `egm-style`
* **Affected Scope:** line 559
* **Discrepancy Description:** Inside the outer `transaction.atomic()`, an `IntegrityError` aborts the PostgreSQL transaction, so "re-read and return" raises `TransactionManagementError`. The current code wraps the create in an inner atomic for this reason (core/services.py:199-206). The sentence is also passive and doesn't say what is re-read.
* **Rationale & Recommendation:** Write: "`Encomenda.objects.create` runs in a nested `transaction.atomic()`, as `reservar_encomenda` does today. On `IntegrityError` the function reads the customer's pending order and returns `("pendente", that order)`."

### M4. Exceptions from `cancelar_encomenda` inside `garantir_sessao_pagamento` are not handled
* **Reviewer Lens:** `egm-codebase-details`
* **Affected Scope:** lines 563-564
* **Discrepancy Description:** `cancelar_encomenda` can raise `PagamentoRecebido` or `stripe.error.StripeError`, and can return `'invalida'` (core/services.py:413-436). The plan doesn't handle any of these. "Everything else stays" also leaves in place the replacement loop (`for _ in range(2)`, with its `RuntimeError`, services.py:310-337), which becomes dead code.
* **Rationale & Recommendation:**
  - `PagamentoRecebido` returns `("a_confirmar", None)`.
  - `StripeError` propagates to the existing failure screen (customer.py:173-182).
  - `'invalida'` returns `("nao_ativa", None)`.
  - Delete the replacement loop.

### M5. A product deleted since the screen was drawn is not handled
* **Reviewer Lens:** `egm-codebase-details`
* **Affected Scope:** lines 549-550
* **Discrepancy Description:** A product that has never been ordered can be deleted in admin, and `LinhaCarrinho` cascades. The snapshot's `produto_id` is then missing from the locked-products dict, and the lookup raises `KeyError`.
* **Rationale & Recommendation:** Treat a missing id as `indisponivel` and return `alterado`. Add a test.

### M6. The expiry check trusts the button timestamp and names a constant that doesn't exist
* **Reviewer Lens:** `egm-codebase-details`, `egm-implementation-readiness`, `egm-style`
* **Affected Scope:** lines 536, 545 (context: line 214)
* **Discrepancy Description:** `VALIDADE_CONFIRMACAO` does not exist. telegram_bot/confirmacao.py:18 defines `VALIDADE_SEGUNDOS`, in seconds. The check uses `emitida_em` from the callback data, but the server-side `snap.criada_em` is authoritative, and the 45-minute cleanup (item 41) is only safe if the server enforces 30 minutes. Importing the constant from telegram_bot also makes core depend on python-telegram-bot.
* **Rationale & Recommendation:** Check `timezone.now() - snap.criada_em > timedelta(seconds=VALIDADE_SEGUNDOS)`. Define the constant in core and have confirmacao.py import it from there, or state that the layering is accepted.

### M7. Removing `encomendar_` from the allowlist contradicts its stated purpose
* **Reviewer Lens:** all three
* **Affected Scope:** line 690
* **Discrepancy Description:** `validar_callback` rejects unknown prefixes before routing (security/sanitization.py:55-66, callbacks.py:66-70). Old buttons would get "❌ Pedido inválido" and never reach "the expired-menu path in their handler". `RoutingTests.test_every_allowed_callback_has_one_route` (telegram_bot/tests.py:38-47) requires a route for every allowed prefix. "After Stage 3" also conflicts with this being a Stage 3 item.
* **Rationale & Recommendation:** Choose one:
  - (a) keep `encomendar_` allowed and route it to a small handler that shows `MENSAGEM_MENU_EXPIRADO` (consistent with how `cat_` is kept, line 186);
  - (b) remove it in Stage 3 and accept the generic alert.

### M8. The cart service return contracts are undefined
* **Reviewer Lens:** `egm-implementation-readiness`, `egm-style`
* **Affected Scope:** lines 651-656, 670
* **Discrepancy Description:** `alterar_quantidade` and `remover_do_carrinho(...)` have no return values. `ver_carrinho` lines have no defined structure, yet `teclado_carrinho(linhas)` depends on it. `contar_itens_carrinho` doesn't say whether N counts lines or sums quantities.
* **Rationale & Recommendation:** Give the exact signature and return fields of each function, and define N. For example: "`remover_do_carrinho(telegram_user, produto_id)` deletes the line, increments `revisao` and sets `atualizado_em`."

### M9. Which flags block checkout is ambiguous
* **Reviewer Lens:** `egm-style`
* **Affected Scope:** lines 654, 670 (context: lines 246, 653)
* **Discrepancy Description:** "Refuses while any line is flagged" includes `preco_alterado`. Line 246 says only insufficient stock or an inactive product blocks checkout.
* **Rationale & Recommendation:** Write "while any line has `stock_insuficiente` or `indisponivel`; `preco_alterado` doesn't block" in both places.

### M10. "Repeat order" ignores the 20-line cap
* **Reviewer Lens:** `egm-codebase-details`
* **Affected Scope:** line 655 (context: line 247)
* **Discrepancy Description:** The merge caps stock and `MAX_QUANTIDADE` but not `MAX_LINHAS`. A repeat can push the cart past 20 lines and break the 4096-character guarantee (line 707).
* **Rationale & Recommendation:** Skip lines beyond `MAX_LINHAS` and report them as skipped. Add a test.

### M11. The "Repetir" button depends on a service that changes state
* **Reviewer Lens:** `egm-codebase-details`
* **Affected Scope:** line 664
* **Discrepancy Description:** The button shows "when `repetir_encomenda` would accept", but `repetir_encomenda` changes the cart.
* **Rationale & Recommendation:** Add a read-only predicate, for example `pode_repetir(encomenda)` (owner, `estado='cancelada'`, payment not `pago` or `reembolsado`), used by both the screen and the service.

### M12. The callers of `teclado_menu_principal` are missing from Stage 3, and the staging is out of order
* **Reviewer Lens:** `egm-implementation-readiness`, `egm-codebase-details`
* **Affected Scope:** lines 672, 735, 740
* **Discrepancy Description:** `commands.start` (commands.py:22) and `customer.menu_inicio` (customer.py:26) call this function. commands.py is listed only in Stage 4, and `menu_inicio` is never listed, so the Stage 3 PR breaks /start. Stage 3 also adds an `admin` parameter while `is_admin` is still sync inside keyboards.py:16.
* **Rationale & Recommendation:** In Stage 3, use `teclado_menu_principal(user_id, itens_carrinho)` and keep the internal `is_admin`. Add `admin` in Stage 4 (`await services.e_admin(user_id)`). List both callers in both stages.

### M13. The welcome text is not escaped for Markdown
* **Reviewer Lens:** `egm-implementation-readiness`, `egm-codebase-details`
* **Affected Scope:** lines 714, 740, 773-774
* **Discrepancy Description:** /start sends with `parse_mode="Markdown"` (commands.py:19-23). An owner-entered `texto_boas_vindas` containing `_`, `*`, `` ` `` or `[` makes Telegram reject the message. This is a SEC-07 regression (telegram_bot/ui.py:5-16).
* **Rationale & Recommendation:** Escape it with `md(loja.texto_boas_vindas)`, or send that part with no parse mode. Use `md(nome, '*')` inside bold, including commands.py:32 and admin.py:39. Add a test with `_` and `*` in the text.

### M14. A view-only shop owner can still cancel and refund through admin actions
* **Reviewer Lens:** `egm-implementation-readiness`, `egm-codebase-details`
* **Affected Scope:** lines 724, 755-756 (context: lines 277-280)
* **Discrepancy Description:** `marcar_cancelada`, `marcar_enviada` and `marcar_entregue` (core/admin.py:117-178) declare no `allowed_permissions`. Django offers such actions to anyone who can open the change list, which breaks G9.
* **Rationale & Recommendation:** Add `permissions=['change']` to the three actions in item 38, and add a test that the owner group sees no order actions.

### M15. The permission data migration runs before the permissions exist
* **Reviewer Lens:** `egm-implementation-readiness`, `egm-codebase-details`, `egm-style`
* **Affected Scope:** line 724
* **Discrepancy Description:** Django creates permissions in `post_migrate`. On a fresh database or the test database, the permissions for models from 0009-0014 don't exist when 0015 runs. "After `create_permissions` has run" doesn't say who runs it.
* **Rationale & Recommendation:** In 0015, first call `create_permissions(apps.get_app_config('core'), apps=apps, verbosity=0)` (setting `models_module`). Declare dependencies on `auth` and `contenttypes`.

### M16. The owner group's "add" rights contradict the Problem statement
* **Reviewer Lens:** `egm-implementation-readiness`
* **Affected Scope:** item 32 (around line 724; context: lines 33, 61, 278)
* **Discrepancy Description:** Line 33 says the owner can "add a category". Item 32 grants only change and view on `Produto` and `Categoria`.
* **Rationale & Recommendation:** Decide the add rights for `Produto` and `Categoria` explicitly, then update the group list and the item 40 test. This is a product decision for the user.

### M17. The expired, pending, failure and invalid screens have no defined keyboards for snapshot tokens
* **Reviewer Lens:** `egm-implementation-readiness`, `egm-codebase-details`
* **Affected Scope:** lines 575, 583-588, 593
* **Discrepancy Description:** `teclado_ecra_expirado(produto_id)` and `teclado_falha_registo(data, categoria)` (keyboards.py:76-85, customer.py:241) need a product or a category, which a `Confirmacao` doesn't carry. "Buttons to that order" (line 584) names no keyboard function. `MENSAGEM_ECRA_EXPIRADO` still says "Abre o produto novamente" (constants.py:57), which is wrong once there is a cart.
* **Rationale & Recommendation:** For each `confirmar_carrinho` outcome in Stages 2 and 3, give the keyboard signature and its callback targets. For example, `teclado_encomenda_pendente(id)` sends `ver_encomenda_<id>`. Update the message text.

### M18. The list of tests to change is incomplete, and the tests ignore the new one-pending-order rule
* **Reviewer Lens:** `egm-implementation-readiness`, `egm-codebase-details`, `egm-style`
* **Affected Scope:** lines 606-607
* **Discrepancy Description:**
  - **Missing tests.** Only db4 is named. These tests also need changes:
    - S7 (`chave_substituicao`, core/tests.py:268; second pending order for the same user, :264);
    - S15 (:347-354);
    - S24 (:432-437);
    - DB2 (patches `_encomenda_por_chave`, :76-93);
    - DB5 (`sem_stock`, :109-114);
    - E1 and E2 (:470-510).
  - **Wrong count.** "About 23 places" is low: 31 lines call `reservar_encomenda`.
  - **One-pending-order rule.** Many tests create a second pending order for the same customer, so `encomenda_teste` would get `pendente`.
* **Rationale & Recommendation:** List every affected test by id with its new expected result. Define how `encomenda_teste` picks a user, for example a fresh Telegram id per call or a `utilizador=` parameter.

### M19. The threaded tests have no pause point, and T6 and T7 have no checkable result
* **Reviewer Lens:** all three
* **Affected Scope:** lines 619-622, 860
* **Discrepancy Description:**
  - **No pause point.** `correr_em_paralelo` pauses thread A by patching `services._encomenda_por_chave` (core/tests.py:543-566), which goes away.
  - **T1 and T2.** T1 asserts `("sem_stock", None)` (:598), a result that no longer exists. T2 now blocks on the snapshot lock. Neither change is described.
  - **T6 and the mutation check.** T6 locks both rows in one `SELECT ... FOR UPDATE ORDER BY pk`, so there is no Python point between the two locks, and the "remove `order_by` and see it fail" check is not deterministic.
  - **No expected result.** T6 ends with "the stock is right", and T7 gives no expected outcome.
  - **T8.** In Stage 3 its second snapshot gets `alterado`, not `pendente`.
* **Rationale & Recommendation:**
  - Name a patchable hook called right after `_bloquear_produtos` (for example `_apos_bloqueio()`), and one after the user lock.
  - Say how `correr_em_paralelo` takes `(nonce, emitida_em)` requests.
  - Describe how T6 forces the interleaving, for example thread A locks product A alone through a test helper.
  - Give exact expected results for T1, T2, T6, T7 and T8. Example for T6: "both return `criada`, no deadlock, and the final stock equals the start minus both orders".
  - State which stage T8 runs in.

### M20. The tests that patch `is_admin` are not updated
* **Reviewer Lens:** `egm-implementation-readiness`, `egm-codebase-details`
* **Affected Scope:** line 736, item 40
* **Discrepancy Description:** `CallbackAnswerTests._dispatch` patches `callbacks.is_admin` (telegram_bot/tests.py:77). After item 34 it must patch the async `services.e_admin`.
* **Rationale & Recommendation:** List telegram_bot/tests.py under item 40.

### M21. The cutoff retry test has no expected result
* **Reviewer Lens:** `egm-implementation-readiness`, `egm-style`
* **Affected Scope:** lines 615-616
* **Discrepancy Description:** "Retry with the same key on each side of the cutoff" states no outcome. The 27/28/29-minute points don't say what each should give, and line 563 uses `>`, so 28 minutes still passes. Open case: the first attempt at 27 minutes created a Stripe session but didn't save the `Pagamento`, and the retry runs at 29 minutes.
* **Rationale & Recommendation:** State the result at each point, for example "27 and 28 minutes create a session; 29 minutes cancels the order". For the open case, state: "cancel, no `Session.create`; the unrecorded session expires on its own at `data_encomenda`+60 minutes".

### M22. Claims and terms that overstate or go undefined
* **Reviewer Lens:** `egm-style`, `egm-codebase-details`
* **Affected Scope:** lines 525, 543-544, 560/564/607/842, 750, 778
* **Discrepancy Description:**
  - **Line 525.** "Makes the row immutable" is false: overriding `save()` doesn't block `update()` or raw SQL.
  - **Lines 543-544.** The order of the owner check and the existing-order check is unclear, and a failed owner check has no stated result. This decides whether one customer can recover another customer's order.
  - **Lines 560, 564, 607, 842.** "D19" is never defined in this document. PROJECT_GUIDANCE.md:36 and SECURITY.md:76 still describe replacement sessions and become false.
  - **Line 750.** "Only one admin source now" contradicts line 730, which keeps the bootstrap list and adds the DB flag.
  - **Line 778.** "Settings with `DJANGO_HTTPS=True`" has no expected result and no method for testing a module-level settings read.
* **Rationale & Recommendation:**
  - **Line 525.** Write "`save()` refuses updates, the admin is read-only, and no code calls `update()` on it".
  - **Lines 543-544.** Write the owner check first: `!= caller -> ("de_outro_utilizador", None)`, then `existente`.
  - **D19.** Define it on first use as "replacement sessions", and add PROJECT_GUIDANCE.md and SECURITY.md (a new SEC-14 entry) to item 46.
  - **Line 750.** Write "every admin check goes through `services.e_admin`".
  - **Line 778.** List the expected settings for `DJANGO_HTTPS` set and unset and for `DJANGO_BEHIND_PROXY`. Say how the test reloads settings (a subprocess, or `importlib.reload` with a patched environment).

---

## Nits

1. **Line 441 (codebase-details).** `CATEGORIA_CHOICES` is `Produto.CATEGORIA_CHOICES` (core/models.py:35), not a module constant. Copy the pairs into 0010 instead of importing live model code, and define `ordem` for unknown codes. Also say whether `Categoria.nome` takes the emoji labels from telegram_bot/constants.py:3-12 or the model labels (line 453).
2. **Line 486 (codebase-details).** The new `teclado_menu_expirado()` duplicates the existing `teclado_voltar_catalogo()` (keyboards.py:23-24). Reuse it.
3. **Lines 464, 492 (readiness).** The `categoria` handler needs a `get_categoria_ativa(id)` service, which isn't listed.
4. **Lines 485, 497 (readiness, codebase-details).**
   - customer.py:241 passes `p.categoria`, which becomes a lazy FK that raises `SynchronousOnlyOperation` in async code. Pass `categoria_id` instead.
   - Name `get_produto_admin` and `get_produto` as the functions that gain `select_related('categoria')`, and add `list_select_related` to `ProdutoAdmin`.
5. **Lines 538-544, 580 (readiness, codebase-details).**
   - Reuse `_obter_ou_criar_utilizador` (services.py:28-34) and `_resultado_existente` (services.py:181-182).
   - Decide whether `criar_confirmacao` takes a model or a Telegram user.
   - Say where the nonce comes from (`confirmacao.gerar_chave()`).
6. **Line 542 (style).** The return value `"invalida"` is missing from the Stage 2 result list (lines 204-210). Add it there.
7. **Line 574 (codebase-details).** `ler_callback` slices by `len(PREFIXO)` (confirmacao.py:61), so the legacy prefix needs its own constant. The 64-byte check is a `ValueError`, not an assert.
8. **Line 634 (codebase-details).** In Django 6.0, `CheckConstraint` takes `condition=` (`check=` was removed) and a `name`. `UniqueConstraint` needs `fields=` and a `name`.
9. **Line 714 (style, codebase-details).** `TextField(max_length=1000)` is enforced only in forms and admin. Say so, or use `CharField(max_length=1000)`.
10. **Line 654 vs line 246 (readiness, codebase-details).** The service is called `preparar_confirmacao_carrinho` in one place and `preparar_confirmacao` in the other. Use one name.
11. **Lines 730, 736, 746 (codebase-details).** Say whether `e_admin` uses `aexists()` or `sync_to_async`. `obter_loja()` is sync, so name the async wrapper the handlers use.
12. **Lines 766-767 (readiness, codebase-details).** Name the `CSRF_TRUSTED_ORIGINS` environment variable. "Both variables" should be three.
13. **Lines 798-803 (readiness, codebase-details).**
    - Give the import path `core.views.notificar_estado`.
    - Treat `cancelar_encomenda` returning `'invalida'` as skipped.
    - The "completed session is skipped" test should also assert that `Session.expire` is not called.
14. **Line 811 (codebase-details, style).** Put `encomendas_antigas_pendentes()` in core/services.py beside `encomendas_abandonadas` (services.py:457-461). The startup test needs a database `TestCase`, because `StartupConfigTests` is a `SimpleTestCase`. Change "adds" to "calls".
15. **Line 836 (style).** "A broken webhook blocks the start after a drain" is true only when a paid legacy order was skipped. Narrow the claim.
16. **Line 867 (style).** Stripe requires `expires_at` to be at least 30 minutes after creation, so the window cannot be "shortened" below that.
17. **Wording (style).**
    - Line 467: "keeps the sorting meaningful" is vague.
    - Line 493: "fail gracefully" is vague.
    - Line 531: this sentence repeats line 203.
    - Line 561: "drained" is jargon.
    - Line 793: "refresh its timer" is jargon; say `atualizado_em` doesn't change.
    - Line 536: write `timedelta(minutes=30)` instead of "30 min".
    - Line 606: "about 23" should be the exact count.
    - Lines 607, 626, 860: give the source class or file of the test ids (S9-S14, H1-H14, T1-T5).
18. **Consistency (style).** "Type / Scope" appears only on items 1-4, 11, 32 and 41. Apply it to every item or to none. Item 47 (lines 852-854) changes nothing and refers to an undefined "Step 6". Drop it or reduce it to one sentence.

## What already meets the standard

- Exact file paths and migration numbering: 0009 is the next free number after `0008_checkout_idempotencia`.
- Model field types and constraints, apart from the Nit 8 syntax.
- The `parametros_checkout` rounding and `expires_at` formula.
- The lock-order rules, apart from M1 and M2.
- The 43-byte `confirmar_carrinho_` token.
- The CI commands. .github/workflows/tests.yml exists and runs them on PostgreSQL 17.
- Most of the Stage 1 and Stage 4 file lists.
- Existing helpers named in the plan exist as described: `SoLeitura`, `notificar_estado`, `chave_confirmacao` and the unused `ADMIN_IDS` / `admin_only`.
