# Goldfish Brainstorm Report: SEC-13 (duplicate Confirm taps) and SEC-03 (failures after acknowledgement)

**Mode:** BRAINSTORM (design only; no code, tests or commits)
**Scope:** `telegram_bot/handlers/customer.py`, `telegram_bot/handlers/callbacks.py`, `telegram_bot/keyboards.py`, `telegram_bot/ui.py`, `telegram_bot/guards.py`, `telegram_bot/app.py`, `core/services.py`, `core/views.py`, `core/models.py`, `core/migrations/`, `security/*`, project docs.

All seven reviewers read the code without running it. Statements about how python-telegram-bot 22.7, stripe 15.2.1, Telegram or PostgreSQL behave come from the libraries' documentation and the reviewers' knowledge. None of them was confirmed at runtime; each one is marked "(unverified)". The Python packages are not installed in the repo.

## Reviewer Status

| Reviewer | Role title | Status |
| --- | --- | --- |
| `egm-design` | Architecture & Design Critic Brainstormer | completed |
| `egm-codebase-details` | Technical Accuracy & Code Reuse Brainstormer | completed |
| `egm-zoom-out` | Simplicity & Scope Brainstormer | completed |
| `egm-comprehension` | Naive Reader & Customer Comprehension Brainstormer | completed |
| `egm-implementation-readiness` | Implementation Readiness Brainstormer | completed |
| `egm-structural` | Structure & Decomposition Brainstormer | completed |
| `egm-inventive-catalyst` | Inventive Catalyst Brainstormer | completed |

No retries were needed.

Severity counts after deduplication: **Blocker 4, Major 18, Nit 9**.

---

## 1. Current behavior (checked against the code)

These are the notes from the brief, corrected or extended where reviewers found more.

- **Updates are processed one at a time.**
  - `telegram_bot/app.py:18` uses `ApplicationBuilder().token(...).build()`. It does not set `concurrent_updates` and registers no `add_error_handler`.
  - Under the PTB default (`concurrent_updates=False`, unverified), a second tap waits in line and then runs the whole handler again. Waiting in line does not prevent the duplicate.
  - `run_polling(stop_signals=None)` (line 24) does not drop pending updates (unverified), so taps sent while the bot was down arrive after it restarts.
- **The order of steps in `confirmar_encomenda`** (`customer.py:140-178`):
  1. Parse with `data.split("_")[-1]` (142).
  2. `get_produto`, active products only (`services.py:54`).
  3. Stock check from a plain read (147-151).
  4. `query.answer()` (152).
  5. `get_or_create_utilizador` (153), `criar_encomenda` (154), `registar_auditoria` (155-158). None of these is inside a try.
  6. A `try/except Exception` (159-178) that covers both the Stripe call and the success `responder`.
  - The except branch also catches rendering failures. Example: a Markdown parse error from a product name (SEC-07) after the session and the `Pagamento` row were saved. The customer is then told no link could be generated, although a working link exists.
  - `logger.error` (171) has no `exc_info` and no order id.
- **What a queued second tap does today** (comprehension, design):
  - If stock is greater than 1, it creates order N+1 and overwrites order N's payment link in the same message.
  - If stock is exactly 1, it replaces order N's success screen with "ficou sem stock entretanto". Order N stays pending, and its link is gone.
- **The callback carries no screen identity.** It is `confirmar_encomenda_<produto_id>` (`keyboards.py:72`). `responder` (`ui.py:4-19`) edits the current message in place unless it is a photo or document. One `message_id` can therefore show menu, product, Confirm, payment link and a later Confirm.
- **`criar_encomenda`** (`services.py:105-126`) makes four separate autocommit `sync_to_async` writes: order, line, `stock_atual = max(0, stale - 1)` then `produto.save()`, and the stock movement. `services.py` has no `transaction` import.
- **A missing image file breaks checkout** (new finding, codebase-details; confirmed):
  - `Produto.save()` (`core/models.py:53-56`) calls `_redimensionar_imagem()`, which runs `PilImage.open(self.imagem.path)` (line 59) whenever `imagem` is set.
  - If the file is missing, `criar_encomenda` raises at `produto.save()` (`services.py:119`). The order, the line and the decremented stock are already saved, but no stock movement is written.
  - The exception is outside the try, so the customer sees nothing.
  - README.md:56 and SECURITY.md say the product images were not imported into this repo, so this path is likely on a fresh deployment.
- **`criar_sessao_stripe`** (`services.py:128-154`):
  - Line items come from the live `produto.nome` and `produto.preco` (135-138), not from the order.
  - It sends no `idempotency_key` and no `client_reference_id`. It does set `metadata={'encomenda_id': ...}` (145).
  - `Pagamento.objects.create` (147-153) runs after the session exists. The checkout URL is never stored.
- **Models:**
  - `Pagamento.encomenda` is OneToOne (`models.py:148-150`), so the database already allows only one `Pagamento` row per order. It does not limit how many Stripe sessions an order can have.
  - `stripe_session_id` (156) is nullable, not unique and not indexed.
  - `Encomenda` has no idempotency field.
  - The latest migration is 0007. Primary keys are BigAutoField.
  - `EncomendaAdmin` (`core/admin.py:77-83`) has no `fields` list, so any new editable field would appear in the admin form.
- **Webhook** (`core/views.py`):
  - Signature verification is at 21-30, catching `stripe.error.SignatureVerificationError` (28).
  - `_handle_payment_success` looks up the `Pagamento` by session id. If none exists, it logs and returns (52-54), and the view returns 200 (42), so Stripe does not retry.
  - The duplicate check (57) is an unlocked read.
  - `Pagamento` is saved as 'pago' (63) before the order is updated (66-67). If the process dies between those two writes, Stripe's retry sees 'pago' and returns, so the order stays 'pendente' for good.
  - It does not check `payment_status`, the amount, or the order's current state.
  - The Telegram notification is sent inline, with a 10 s timeout (78, 97-101).
  - `.get()` would raise `MultipleObjectsReturned` if two rows shared a session id.
- **`produto_detalhe`** (`customer.py:41-73`):
  - It answers (57), deletes the current message (63), then opens `p.imagem.path` (64) and calls `send_photo` (65-71), with no error handling.
  - The `edit_caption` branch (59-61) looks unreachable from the current keyboards.
  - After the delete, `responder` cannot be used, because there is no message left to edit.
- **Router** (`callbacks.py:36-82`):
  - `_RespostaUnica` sets `respondida=True` before it awaits the answer, and the `finally` re-answer does nothing.
  - Handler exceptions propagate to PTB, which only logs them because no error handler is registered.
  - `test_handler_error_still_answers_once` (`tests.py:86-90`) locks in that propagation.
- **Old taps after a restart** (codebase-details, comprehension):
  - `verificar_sessao` (`guards.py:42-66`) keeps `_ultima_atividade` in memory. After a restart, every first callback gets "Sessão expirada", and the message is edited with no `reply_markup`, which removes its buttons.
  - Telegram may also reject `answer()` on old queries ("query is too old", unverified). That would raise at `customer.py:152` before any order is created.
  - Both act today as accidental guards against post-restart duplicates. Both also leave the customer without useful feedback.
- **Rate limit:** `security/decorators.py:13-43` allows 5 actions per 10 s per user, in memory. Two to five rapid taps all pass.
- **Callback length:** `validar_callback` allows 200 characters (`security/sanitization.py:47`). Telegram allows 64 bytes (unverified).
- **Tests:**
  - `telegram_bot/tests.py` uses only `SimpleTestCase` with no database. `core/tests.py` is empty. README.md:34 says the tests need no database.
  - `CheckoutAcknowledgementTests` (`tests.py:102-137`) asserts the call order `["answer","stripe","responder"]`.

---

## 2. Proposed design (shared direction across reviewers)

All seven reviewers reached the same core design on their own:

1. **Per-screen confirmation token.**
   - `encomendar` mints a random hex token each time it draws the Confirm screen and puts it in the Confirm callback data.
   - A new nullable `Encomenda` field stores it under a unique constraint. The reviewers proposed the names `chave_confirmacao` or `chave_idempotencia`.
   - Existing orders keep NULL and are not backfilled.
2. **Atomic order creation.**
   - One synchronous function inside `transaction.atomic()`, wrapped once in `sync_to_async`.
   - It inserts the order with its token, the line, the stock change and the stock movement. Audit goes inside or outside per owner choice P3.
   - On `IntegrityError`, it returns the existing order for (user, token).
   - The Stripe call stays outside the transaction.
3. **Look up the existing order first.** The handler checks (user, token) before the product lookup, the stock check or any rendering.
4. **Stripe idempotency.**
   - Pass `idempotency_key` derived from the order. Build the line items from the order snapshot.
   - Save the payment record with `Pagamento.objects.get_or_create(encomenda=...)`. Never move a 'pago' row back to 'pendente'.
5. **One failure path after `answer()`.**
   - The failure text depends on what exists at that point: nothing, an order without a link, or a link whose message failed to render.
   - Status messages are sent with `parse_mode=None` or with escaped text.
   - If the edit fails, fall back to `send_message`. Treat "message is not modified" as success.
6. **Webhook fallback.**
   - Keep `construct_event` unchanged.
   - If no `Pagamento` matches, resolve the order from the signed event's `metadata.encomenda_id`, and optionally from `client_reference_id`.
   - Check amount, currency and `payment_status`.
   - Inside one transaction with a lock or a conditional update, create or link the `Pagamento` and confirm the order.
   - Send the notification through `transaction.on_commit`.
7. **Photo fallback.**
   - Send the photo first and delete the old message only after it succeeds.
   - On `OSError` or `TelegramError`, show the text-only product screen with the same `teclado_produto(p)`.
   - Separately, change the stock decrement so it no longer calls `Produto.save()` (see B3).

**Proposed slicing** (structural, zoom-out). Each slice has its own migration and doc update:

| Slice | Content | Depends on |
| --- | --- | --- |
| 0 | Record owner decisions, restructure the SEC-03/SEC-13 entries, add a PostgreSQL test harness in `core/tests.py` | none |
| A | Photo fallback in `produto_detalhe`. No migration. | 0 |
| B | Token, migration 0008, atomic `criar_encomenda`, existing-order lookup first | 0 |
| C | Failure messages after `answer()`, Stripe idempotency key, `get_or_create` Pagamento, retry button | B (a retry is only safe once a repeat tap cannot create an order) |
| D | Webhook metadata fallback, locked transition, notify on commit | Can run alongside B. Must ship with or before C if owner choice P2 is "show the link anyway". |

**Migration 0008, as described across reviewers:**
- An additive nullable `CharField`, `editable=False` so it stays out of the admin form. Existing rows keep NULL, and PostgreSQL allows any number of NULLs under a unique constraint.
- Deploy order: migrate first, then deploy the code. Old code runs on the new schema; new code needs the new schema.
- Rollback: revert the code and leave the column in place.
- Optional and owner-gated: a unique constraint on `Pagamento.stripe_session_id` (P21) and a `stripe_checkout_url` column (P7).

---

## 3. Findings

### Blocker

#### B1. The idempotency key must identify the confirmation screen, not the Telegram message or the (user, product) pair
- **Lens:** all seven (comprehension and implementation-readiness also flagged that the term "confirmation message" is undefined)
- **Affected:** `keyboards.py:70-74` `teclado_confirmar_encomenda`; `customer.py:120-142` `encomendar` / `confirmar_encomenda`; `models.Encomenda`; `SECURITY.md:75` (SEC-13 recommendation)
- **Problem:**
  - Nothing ties a tap to a confirmation screen.
  - The obvious keys are wrong:
    - `(chat_id, message_id)` survives the in-place edits that `responder` makes (`ui.py:15-19`). It would block a legitimate second purchase made later from the same message.
    - `callback_query.id` is unique per tap, so it only catches redelivery of the same update.
    - SEC-13's current suggestion, a guard keyed on user and product, would block repeat purchases. Because SEC-04 leaves expired orders in 'pendente', the block could last indefinitely.
- **Recommendation:**
  - Define a "confirmation" as one rendering of the Confirmar screen by `encomendar`. Each rendering mints a random hex token, carried in the callback data and stored on `Encomenda` under a unique constraint.
  - Look the token up together with the user. A forged token then only creates the forger's own single order, so no HMAC signing is needed.
  - Correct the SEC-13 text in SECURITY.md to say "per confirmation screen".
  - The scope of the unique constraint differs between reviewers; see Section 4.

#### B2. Order creation must be atomic, in one synchronous function, with Stripe outside the transaction
- **Lens:** design, zoom-out, structural, implementation-readiness, inventive-catalyst, codebase-details
- **Affected:** `core/services.py:105-126` `criar_encomenda`; `customer.py:153-158`
- **Problem:**
  - The four separate autocommit writes mean a token could be stored on an order that has no line or no stock movement. Every retry would then correctly return that broken order.
  - `transaction.atomic()` cannot span several awaits from async code.
  - Holding a transaction open across `Session.create` would block the thread that `sync_to_async(thread_sensitive=True)` shares, for as long as the Stripe request takes.
- **Recommendation:**
  - Write one sync function, wrapped once in `sync_to_async`.
  - Inside `atomic()`: get or create the user, then insert the order with the token under an inner savepoint. On `IntegrityError`, fetch the existing order for (user, token) and return `(encomenda, criada=False)`. On a new insert, also write the line, the stock change and the movement, and the audit row per P3.
  - Check that the existing order's product matches the callback's product.
  - Design and structural also suggest one service entry point, for example `confirmar_checkout(user, produto_id, chave)`, that returns an outcome, the order and the URL. The handler would then only map that result to Portuguese text.
  - The design doc must state that this pulls the atomicity half of SEC-02 into scope.

#### B3. A missing product image makes checkout fail silently and leaves partial records
- **Lens:** codebase-details (confirmed by the synthesizer against `core/models.py:53-59`)
- **Affected:** `core/services.py:118-119`; `core/models.py:53-59` `Produto.save` / `_redimensionar_imagem`
- **Problem:**
  - `produto.save()` in `criar_encomenda` resizes the image on every checkout. A missing file raises `FileNotFoundError` after the order, the line and the stock decrement were committed but before the stock movement.
  - The exception is outside the try, so the customer gets no message.
  - The photo problem is therefore not limited to `produto_detalhe`.
  - The full-row save also overwrites concurrent admin edits to price, active flag or stock, and re-encodes the image on every order (SEC-11).
- **Recommendation:**
  - Decrement stock with `Produto.objects.filter(pk=..., ...).update(stock_atual=F('stock_atual') - 1)`. `update()` does not call `save()`, so the image is not touched.
  - Adding `stock_atual__gte=1` and rolling back when no row changes also stops overselling (SEC-02). That rule change is owner choice P14.
  - Add a test: checkout succeeds for a product whose image file is missing.
  - Owner choice P1, "cancel the order", reuses `cancelar_encomenda_db` (`services.py:156`). That function also uses `save()` and has the same coupling.

#### B4. Failures after `answer()` reach the customer as nothing, and rendering errors are reported as Stripe errors
- **Lens:** inventive-catalyst rated this Blocker; design, zoom-out, structural, codebase-details, implementation-readiness and comprehension rated it Major
- **Affected:** `customer.py:152-178`; `callbacks.py:79-82`; `app.py` (no error handler)
- **Problem:**
  - Lines 153-158 are not caught. A database error in `get_produto` gets past the `DoesNotExist`-only except at 143.
  - The single except (170) covers Stripe and rendering together and renders Markdown itself, so it can fail the same way the success message did.
  - After a failure the Confirm button stays live, and today another tap creates another order.
- **Recommendation:**
  - Put one guard around everything after `answer()`. Record the stage reached: nothing stored, order committed, session created, Pagamento saved.
  - Map each stage to its own Portuguese message (see the failure matrix in Section 6).
  - Send status text with `parse_mode=None`. If the edit fails, fall back to `context.bot.send_message`, then log.
  - Log with `exc_info=True` and the stage name.
  - An `app.add_error_handler` can be a last resort (P20). It cannot know whether an order exists, so its text must not claim "nada foi cobrado".
  - Guaranteed delivery holds only while Telegram itself is reachable.

### Major

#### M1. Look up the existing order before the product, stock or active checks, and scope the lookup to the tapping user
- **Lens:** comprehension (rated Blocker), zoom-out, structural, design
- **Affected:** `customer.py:141-152`
- **Problem:**
  - A repeat tap after the last unit was taken shows "ficou sem stock entretanto".
  - A repeat tap after the product was deactivated shows "Produto não encontrado".
  - In both cases the existing order is not shown. Single-unit stock is common here (`customer.py:49`).
- **Recommendation:**
  - Resolve (user, token) to an existing order first; an order that exists always wins.
  - A token that belongs to another user, or was never issued, gets a generic "Pedido inválido" and never reveals another customer's order number or link.

#### M2. Callback format, parser and the 64-byte limit need an exact specification
- **Lens:** implementation-readiness (rated Blocker), codebase-details, inventive-catalyst, structural, comprehension
- **Affected:** `keyboards.py:72`; `customer.py:142`; `security/sanitization.py:22-26, 47`; `tests.py:26`
- **Problem:**
  - `split("_")[-1]` breaks once a token is appended, and an all-digit token would parse as a product id.
  - `token_urlsafe` output can contain `_` and `-`.
  - The prefix `confirmar_encomenda_` is 20 bytes. A BigAutoField id can reach 19 digits.
- **Recommendation:**
  - Use hex-only tokens and one strict parser that validates the token with a regex.
  - Add a test that the generated callback is at most 64 bytes in UTF-8 for the maximum id and passes `validar_callback`.
  - No allowlist change is needed; the prefix is unchanged.
  - Token length and position are open (Section 4).

#### M3. An in-memory in-flight guard adds nothing here; disabling the button is feedback only
- **Lens:** all seven (design and inventive-catalyst rated it Nit)
- **Affected:** `app.py:18`; `security/decorators.py`
- **Problem:**
  - With one update at a time, the first handler has finished before the second one starts, so an in-flight set is always empty.
  - A "recently completed" cache is lost on restart and not shared between workers.
  - Editing the message first does not reach updates already queued at Telegram or PTB, other devices, other workers, or restarts, and the edit can fail.
  - Two pollers cannot share one bot token (unverified). Real concurrency only appears with `concurrent_updates`, webhook mode or several workers.
- **Recommendation:**
  - Do not build an in-memory guard. Rely on the PostgreSQL constraint.
  - Whether to show a "processing" edit is an owner choice (P16).
  - Do not enable `concurrent_updates` in this change.

#### M4. Stripe session creation is not safe to retry; use Stripe's idempotency key with parameters taken from the order
- **Lens:** all seven
- **Affected:** `services.py:128-154`; `models.py:148`
- **Problem:**
  - Today, a retry after the `Pagamento` save fails creates a second payable session for the same order and then hits the OneToOne `IntegrityError`.
  - Line items use the live `produto.preco`. If the price changes between attempts, the customer is charged something other than `encomenda.total`, and Stripe rejects the reused key with an idempotency error (unverified).
- **Recommendation:**
  - Pass `idempotency_key=f"checkout-encomenda-{encomenda.id}"`. Inventive-catalyst suggests adding the token, so that test databases that reuse ids within 24 h do not replay another order's session.
  - Also set `client_reference_id` and keep `metadata.encomenda_id`.
  - Build the amount from `LinhaEncomenda.preco_unitario` or `encomenda.total` (P15).
  - Create a session only when the order has no stored session id.
  - Limits (unverified):
    - Keys last about 24 h.
    - A concurrent request with the same key returns 409. Render that as "a ser processada", not as a failure.
    - Stripe may replay a stored error result for the same key (P9).
  - Confirm that the `idempotency_key` keyword is accepted in stripe 15.2.1.

#### M5. The webhook drops a paid session whose `Pagamento` row is missing, and acknowledges it with 200
- **Lens:** all seven
- **Affected:** `core/views.py:45-54, 42`; `_handle_payment_success`
- **Recommendation:**
  - Keep `construct_event` exactly as it is.
  - When the lookup misses, read `metadata['encomenda_id']` (or `client_reference_id`). Only this server sets those fields, and the verified signature covers them. Check `.get` support on `StripeObject` in 15.x.
  - Lock the order, then check `amount_total == int(total*100)`, `currency == 'eur'` and `payment_status == 'paid'`.
  - `get_or_create` the `Pagamento` with this session id, then continue the normal paid path.
  - Sessions created by the current code already carry this metadata, so sessions still open at deploy time are covered.
  - Zoom-out notes that in the proposed bot flow the link is shown only after the `Pagamento` is saved. The fallback is then mostly defense in depth, unless P2 is "show the link anyway" or a row was deleted by hand.
  - Edge cases go to the owner (P11, P12): a different session id on an existing `Pagamento`, a cancelled order, an amount mismatch.
  - The `checkout.session.expired` path should stay log-only; more than that drifts into SEC-04.

#### M6. Webhook duplicate handling is not atomic, and a crash midway cannot be recovered by Stripe's retry
- **Lens:** codebase-details, design, structural, implementation-readiness, inventive-catalyst, zoom-out
- **Affected:** `core/views.py:57-78`
- **Problem:**
  - Concurrent duplicate deliveries can both apply the change, giving two audit rows and two notifications.
  - A crash between the `Pagamento` save (63) and the order save (66-67) leaves the order 'pendente' for good.
- **Recommendation:**
  - Put the payment and order updates and the audit row in one `transaction.atomic()`. Use `select_for_update(of=("self",))`, so that `select_related` does not also lock the joined rows, or a conditional `filter(..., estado_pagamento='pendente').update(...)` and its row count.
  - Only the delivery that changes a row writes the audit and the notification, through `transaction.on_commit`.
  - Use the same lock target on the normal path and on the fallback path.
  - This overlaps SEC-05; storing event ids stays out of scope. The owner decides whether concurrent duplicates are in scope (P13).

#### M7. The bot's `Pagamento` save and the webhook can race
- **Lens:** implementation-readiness, inventive-catalyst
- **Affected:** `services.py:147-153`
- **Problem:**
  - If the webhook fallback creates the row first, the bot's `create` raises `IntegrityError`.
  - `update_or_create` would be worse: it could overwrite 'pago' back to 'pendente'.
- **Recommendation:**
  - Use `get_or_create(encomenda=...)`. Set `stripe_session_id` only when it is NULL, and never change `estado_pagamento` from the bot.
  - An alternative is to pre-create the `Pagamento` (pendente, session NULL) inside the order transaction. Then a missing row only happens on legacy orders, but rows with an empty session id appear in admin. This is an implementer choice once P2 is decided.

#### M8. The repeat-tap response is not specified, and naive re-rendering can destroy the payment link
- **Lens:** comprehension, design, implementation-readiness, zoom-out, structural
- **Affected:** `confirmar_encomenda` after the token lookup; `ui.py`
- **Problem:**
  - Re-sending the same text and buttons raises `BadRequest: Message is not modified` (unverified). A generic except could then overwrite a valid payment screen with an error.
  - With several workers, the losing tap's "processing" edit can land after the winner's payment screen and remove the Pay button.
  - The checkout URL is not stored anywhere, so a repeat tap has no source for the link.
  - "Ver Encomendas" cannot open an order's details (SEC-12, out of scope), so the Confirm button or a retry button is the only way back to a link.
- **Recommendation:**
  - Use distinct repeat-tap text.
  - Treat "not modified" as success.
  - A tap that finds an order still in progress answers with a toast only and does not edit. Only a finished state edits the message, and that screen always includes "Pagar Agora" while the session is open.
  - Implementation-readiness and comprehension propose running the lookup before `query.answer()`, so the toast is still available; `_RespostaUnica` allows that.
  - The URL source is owner choice P7.

#### M9. A failed `query.answer()` on an old query aborts the flow silently, and catching it changes behavior
- **Lens:** codebase-details, design, inventive-catalyst, zoom-out, comprehension
- **Affected:** `customer.py:152`; `callbacks.py:50-54`
- **Problem:**
  - Taps redelivered after a restart, or taps that waited behind slow Stripe calls, may get "query is too old" (unverified). The exception then reaches PTB, and the customer sees nothing.
  - Today this accidentally prevents post-restart orders.
  - If the new code swallows the error so it can always show a status, old taps will start creating their single order.
- **Recommendation:**
  - Zoom-out proposes `_RespostaUnica.answer` catching and logging `TelegramError` for every handler.
  - Decide P5 (process or reject old taps) before implementing.

#### M10. The in-memory bot session guard interferes with restart behavior and tests
- **Lens:** codebase-details, comprehension
- **Affected:** `telegram_bot/guards.py:36-66`
- **Problem:**
  - After a restart, every first callback hits "Sessão expirada", and the message is edited with no keyboard. This can also remove "Pagar Agora".
  - After /start, any old Confirm button that was not edited becomes live again.
  - A restart test that does not seed `_ultima_atividade` tests the guard, not the idempotency.
- **Recommendation:**
  - State that restart tests either seed the session state or call the checkout service directly.
  - Add a separate test that keeps the expected expired-session behavior.

#### M11. The photo path deletes the screen before it knows the upload works
- **Lens:** all seven
- **Affected:** `customer.py:57-71` `produto_detalhe`
- **Problem:**
  - A missing file, an upload error, a caption over 1024 characters (text allows 4096; unverified) or a Markdown parse error (SEC-07) all leave the customer with no message and no buttons.
  - `.path` also fails on storage that is not local.
- **Recommendation:**
  1. Check `p.imagem.storage.exists(p.imagem.name)` or open the file before deleting anything.
  2. Send the photo first, then delete the old message; ignore a failed delete, for example on messages older than 48 h.
  3. On `OSError` or `TelegramError`, use `responder(...)` if the old message still exists, otherwise `context.bot.send_message`. Use the same caption and `teclado_produto(p)`, with `parse_mode=None` or escaped text so the fallback cannot fail the same way.
  4. Log a warning with the product id, not the file path or personal data.
  - Risk: `TimedOut` is ambiguous, because the photo may have arrived. The fallback could then show a second product screen; both keep working buttons, since `produto_`, `encomendar_` and `cat_` callbacks carry no state (P17).
  - Leave the `edit_caption` branch unchanged.

#### M12. Confirm buttons already sent before the deploy have no token
- **Lens:** all seven
- **Affected:** `customer.py:142`; legacy `confirmar_encomenda_<id>` callbacks
- **Problem:**
  - Accepting legacy buttons keeps SEC-13 open for those messages.
  - Rejecting them changes what customers see.
  - The parser must never read the token as the product id.
- **Recommendation:** owner choice P4. Implementation-readiness notes that a token-first format (`confirmar_encomenda_<token>_<id>`) lets a rolled-back deploy keep parsing the product id from the last segment.

#### M13. The migration and how existing data is handled need to be written down
- **Lens:** implementation-readiness, structural, inventive-catalyst, design, codebase-details
- **Affected:** `core/migrations/0008_*` (new); `core/admin.py:77-83`
- **Recommendation:**
  - One additive `AddField`, nullable, `editable=False`. Existing orders keep NULL with no backfill, because the original confirmation screens cannot be reconstructed.
  - Building the unique index briefly blocks writes on a small table. Django also creates a `_like` index for unique CharFields, which is harmless.
  - Migrate before deploying the code. Reverse with `migrate core 0007`, which only drops the new keys.
  - Partial orders left by past failures are not touched.
  - Optional, unique `Pagamento.stripe_session_id` (P21):
    - It needs a preflight query for duplicate non-null values and for `''` values (admin can edit the field).
    - Converting `''` to NULL is a data change and needs approval.
    - If duplicates exist, stop and ask the owner; never delete rows.
  - Keep one migration per slice.

#### M14. The test infrastructure cannot yet prove the required behavior
- **Lens:** implementation-readiness, structural, comprehension, design, inventive-catalyst
- **Affected:** `telegram_bot/tests.py`; `core/tests.py`; `README.md:34`
- **Problem:**
  - No database tests exist.
  - `TestCase` wraps each test in one transaction and cannot show races.
  - `asyncio.gather` over two handler calls is not real concurrency, because `sync_to_async(thread_sensitive=True)` runs all ORM calls on one thread.
  - SQLite does not lock the way PostgreSQL does.
  - On a bare `MagicMock`, `query.message.photo` is truthy, which sends `responder` down its delete-and-send branch.
  - `CheckoutAcknowledgementTests` asserts the old call order.
- **Recommendation:**
  - Keep the mock-only handler tests in `telegram_bot/tests.py`, so `manage.py test telegram_bot` stays database-free.
  - Put service, webhook and concurrency tests in `core/tests.py` on PostgreSQL. Use `TransactionTestCase` with OS threads and a `threading.Barrier` or `Event`; each thread calls `connection.close()` at the end.
  - Document that `manage.py test core` needs PostgreSQL and a user with CREATEDB rights.
  - Set `photo=None` and `document=None` on mocks explicitly.
  - Update `CheckoutAcknowledgementTests` to the new callback format and call order.

#### M15. The stock decrement from a stale read overlaps SEC-02
- **Lens:** design, zoom-out, structural, implementation-readiness, inventive-catalyst, codebase-details
- **Affected:** `services.py:118`
- **Problem:** even inside a transaction, read-then-`max(0, ...)` lets two different confirmation screens oversell silently.
- **Recommendation:**
  - A conditional `F()` update with `stock_atual__gte=1`, rolling back when no row changes, is about one to three lines once the transaction exists. The out-of-stock message already exists (`customer.py:149`).
  - It changes what customers see: the race loser gets "sem stock" instead of an oversold order. It is therefore owner choice P14.
  - B3 needs `update()` either way.

#### M16. The customer cannot tell whether they were charged, and failure messages lead nowhere
- **Lens:** comprehension, design, zoom-out
- **Affected:** `customer.py:167, 174-176`; `guards.py:79, 85`; `constants.py:37`; `keyboards.py:63-65`
- **Problem:**
  - "pendente" covers awaiting payment, paid with the webhook missed, and abandoned.
  - Payment status is never shown to the customer.
  - Every failure message says "Contacta o suporte", but no support channel is defined anywhere in the repo.
  - Failure messages that point to "Ver Encomendas" end in SEC-12's broken order details.
- **Recommendation:**
  - Before the Stripe redirect no charge is possible, so pre-redirect failure texts can truthfully say "Não foi feita nenhuma cobrança". Never reuse that line after a webhook has run.
  - Repeat-tap screens read `Pagamento.estado_pagamento` rather than assuming a state.
  - Replace "Contacta o suporte" with a concrete next step, or define a contact (P18).
  - Decide how a customer gets a payment link back (P19).

#### M17. Documentation organization and placement on the roadmap
- **Lens:** structural, implementation-readiness
- **Affected:** `SECURITY.md:33-35, 73-75`; `ROADMAP.md`; `README.md:9`; `ARCHITECTURE.md:35, 42, 51, 66`; `services.py:3-4` docstring; `PROJECT_GUIDANCE.md`
- **Problem:**
  - SEC-03 bundles four concerns under "Checkout", and the photo one is not about checkout:
    - failures before the Stripe call;
    - cleanup after a Stripe failure;
    - a session saved without a `Pagamento` row;
    - the photo.
  - Findings have no status field.
  - ROADMAP.md:3 says not to work on several phases at once, yet this change pulls Phase 2 items (atomic creation, webhook idempotency) ahead of unfinished Phase 1 work.
- **Recommendation:**
  - Split SEC-03 into labeled sub-items (a-d) or move the photo item to a new id.
  - Add a status line to each finding.
  - Correct SEC-13 (B1).
  - Note in SEC-02 and SEC-05 which parts this change covers.
  - Add a ROADMAP note on why this change comes early (P22).
  - Update README, ARCHITECTURE ("seven migrations", flow step 3) and the docstring in the slices that change the behavior.
  - Record the owner's decisions in PROJECT_GUIDANCE.md.

#### M18. Logs and audit records are not enough to reconcile payments
- **Lens:** codebase-details
- **Affected:** `customer.py:171`; `core/views.py` logging; audit action set
- **Recommendation:**
  - Every checkout and webhook log line carries the order id, the token, the Stripe session id and the stage, logged with `exc_info=True`.
  - Add audit actions for: a repeat tap reusing an order, a checkout stage failure, a `Pagamento` rebuilt from metadata, and two paid sessions for one order.
  - Any PTB error handler logs the update type and callback prefix, not the user name (SEC-08).

### Nit

#### N1. Check that the `stripe.error` namespace still exists in stripe 15.2.1
- **Lens:** codebase-details, zoom-out, implementation-readiness
- **Affected:** `core/views.py:28`
- **Recommendation:** if the alias has moved, a bad signature raises `AttributeError` and returns 500 instead of 400. The invalid-signature test (W5) covers this.

#### N2. The webhook sends its Telegram notification inline with a 10 s timeout
- **Lens:** codebase-details, design
- **Affected:** `core/views.py:97-101`
- **Recommendation:** this delays the response to Stripe. Moving the send into `on_commit` (M6) keeps it out of the locked section. A notification outbox is out of scope.

#### N3. `registar_auditoria` reads the user again by telegram_id
- **Lens:** zoom-out, structural, implementation-readiness, inventive-catalyst
- **Affected:** `services.py:201-210`
- **Recommendation:** pass the `Utilizador` object in. Write audit only when an order is created, not when a repeat tap returns an existing one.

#### N4. There is no `CONN_MAX_AGE` handling and no `close_old_connections` call in the long-running bot
- **Lens:** codebase-details
- **Affected:** `config/settings.py`
- **Recommendation:** after a PostgreSQL restart, every checkout may fail until the bot restarts. This bears on the "user creation fails" message path; note it and do not fix it here.

#### N5. With one-at-a-time processing, each Stripe call and photo upload blocks all users
- **Lens:** design, structural
- **Affected:** `app.py:18`
- **Recommendation:** out of scope. The design must not rely on sequential processing.

#### N6. Existing copy promises automatic confirmation
- **Lens:** comprehension
- **Affected:** `customer.py:167`
- **Recommendation:** "A encomenda é confirmada automaticamente após o pagamento" depends on the webhook. A softer proposal: "Receberás uma mensagem quando o pagamento for confirmado."

#### N7. The order list shows raw lowercase states
- **Lens:** comprehension
- **Affected:** `keyboards.py:64`
- **Recommendation:** out of scope; noted only because it affects whether a customer can tell what happened.

#### N8. The word "session" is ambiguous
- **Lens:** comprehension
- **Affected:** the concept text and the design doc
- **Recommendation:** use "Stripe Checkout Session" and "sessão do bot" consistently.

#### N9. Do not add new order states in this change
- **Lens:** structural
- **Recommendation:** a state such as "payment failed" belongs to the ROADMAP Phase 2 state-machine decision, which is the owner's.

---

## 4. Where reviewers disagree (implementer or owner to settle)

| Topic | Positions |
| --- | --- |
| Scope of the unique constraint | Global `unique=True` on the token (design, zoom-out, implementation-readiness), or a partial `UniqueConstraint(fields=['utilizador', token], condition=token IS NOT NULL)` (codebase-details, inventive-catalyst, comprehension). All agree the lookup filters by user. |
| Token length | `token_hex(8)` with 16 hex characters (design, codebase-details, zoom-out, inventive-catalyst); `token_hex(12)` with 24 characters (implementation-readiness); 16 bytes or more (structural). All fit in 64 bytes for realistic ids; the 24-character token also fits the 19-digit maximum id. |
| Token position | `..._<produto_id>_<token>` (most reviewers), or `..._<token>_<produto_id>` (implementation-readiness), so a rolled-back deploy still reads the id from the last segment. |
| Lookup before or after `answer()` | Before, so a repeat tap can get a toast without an edit (comprehension, implementation-readiness), or after `answer()` with an edit (others). |
| Is the webhook fallback reachable? | Essential (most), or mostly defense in depth unless P2 is "show the link anyway" (zoom-out). |
| `Pagamento` created before or after the Stripe session | After, as today, with `get_or_create` (most), or pre-created inside the order transaction (implementation-readiness and structural, as an option). |
| Unique `stripe_session_id` | Not needed for correctness (zoom-out, implementation-readiness), or optional with a preflight (others). |
| Global PTB error handler | Last resort only (design, structural, zoom-out), or a router-level catch-all as an owner choice (inventive-catalyst). |

---

## 5. Unresolved product choices (owner approval needed; none chosen here)

**P1. Stripe call fails after the order is committed.**
- (a) Keep the order pending and offer a retry with the same token. Customer-friendly, and the Stripe key can recover the same session. Stock stays held until SEC-04 or an admin acts.
- (b) Cancel it and restore stock through `cancelar_encomenda_db`. The state stays clean, but the customer starts over and order numbers appear "lost". It also defines SEC-06 cancellation semantics. If the Stripe call actually timed out after creating a session, the webhook could later confirm a cancelled order, because `views.py:65-67` does not check the order state. That function also uses `save()` (B3).
- (c) Today's "Contacta o suporte" with no retry.

**P2. The Stripe session was created but saving the `Pagamento` failed.**
- (a) Show the link anyway and rely on the webhook metadata fallback. Best for the customer; requires slice D.
- (b) Hide the link and offer a retry, which reuses the same session through the idempotency key. Safer, one extra tap.
- (c) Expire the session and show an error. The expire call can fail too.
- (d) Show an error and let the session lapse after 24 h.
- Related design choice: create the `Pagamento` before or after the session (Section 4).

**P3. The audit write fails.**
- (a) Inside the order transaction: the audit trail is complete, but an audit outage blocks sales.
- (b) Best effort after commit, logged: sales continue, but the audit can have gaps.

**P4. Confirm buttons sent before the deploy (no token).**
- (a) Reject with "Este ecrã de confirmação já não é válido" and a button back to `produto_<id>`. Safe, one extra step.
- (b) Process them without the guard. Duplicates remain possible from old messages.
- (c) Key them on `(chat_id, message_id[, edit_date])`. Fragile (B1); no reviewer recommends it.

**P5. Taps that are old or redelivered after a restart.**
- (a) Process them through the idempotent path: the customer gets the single order they asked for, possibly late.
- (b) Reject taps older than N seconds with a "volta a confirmar" message. No surprise orders, an extra step, and N must be chosen. The token or the message date can carry the age.
- (c) Also decide whether `answer()` errors are swallowed (M9).

**P6. What a repeat tap shows.**
- (a) The same payment screen.
- (b) The payment screen with a "já criada" header.
- (c) A toast only, with no edit. Cheapest, but no recovery if the first tap failed halfway.
- (d) Status only. The original link may already have been overwritten (M8).

**P7. Where a repeat tap gets the payment URL.**
- (a) `stripe.checkout.Session.retrieve(id).url`: no migration, one API call each time, and Stripe must be reachable. The URL is null once the session is closed (unverified).
- (b) A new `Pagamento.stripe_checkout_url` column: works without calling Stripe, but it is an extra migration and the stored URL can go stale.
- (c) Replay `Session.create` with the same key: only valid for about 24 h.

**P8. A repeat tap after the session expired.**
- (a) Status message only (stays out of SEC-04).
- (b) Create a new session. Needs a new idempotency key and replacing the session id; touches SEC-04.

**P9. Stripe keeps returning an error under the same idempotency key.**
- (a) Keep the key: no second session, but it may keep failing until the key expires.
- (b) Switch to a new key: recovers faster, but two sessions can exist. Both are linkable through metadata.

**P10. Two paid sessions for one order.**
- (a) Refund automatically.
- (b) Alert an admin for a manual refund.
- (c) Record both payments.
- Refunds touch SEC-06.

**P11. Webhook data that does not match** (amount or currency mismatch, cancelled order, existing `Pagamento` with a different session id).
- (a) Confirm and log.
- (b) Hold for admin review without confirming; write an audit row and never overwrite.

**P12. Webhook event whose order cannot be found.**
- (a) Return 200 and log: no retries, so it needs manual follow-up.
- (b) Return 5xx: Stripe retries for up to about three days, which covers commit races but is noisy for a permanent problem.

**P13. Concurrent duplicate webhook deliveries.**
- (a) Add the row lock or conditional update now: a partial SEC-05 fix of about 10 lines, plus a threaded PostgreSQL test.
- (b) Cover only duplicates that arrive one after another and leave the rest to SEC-05.

**P14. Conditional stock decrement (SEC-02 oversell protection) in this change.**
- (a) Include it: one to three lines; the race loser gets "sem stock".
- (b) Leave it out: wrap the existing decrement in the transaction only. The `update()` form is still needed for B3.

**P15. Price charged on a retry.**
- (a) The order snapshot (`preco_unitario` / `encomenda.total`).
- (b) The current `produto.preco`. A price edit between attempts then breaks the idempotency key.
- PROJECT_GUIDANCE.md:27 lists "prices snapshotted" as a rule still to confirm.

**P16. A "processing" edit before the work starts.**
- (a) Edit to "A processar..." and remove the buttons. Better feedback, but one more Telegram call and one more failure point, and every failure path must restore a button. If the edit fails, decide whether to continue.
- (b) No edit.

**P17. Photo fallback.**
- (a) Show a notice such as "(Imagem indisponível)", or show the text screen silently.
- (b) Send it as plain text (`parse_mode=None`, which always sends) or Markdown (which can fail again).
- (c) Whether to alert admins about missing files.
- (d) On an upload timeout: send the fallback (possible duplicate screen) or log only.

**P18. Support contact.**
- (a) Define a channel, such as a handle or an email.
- (b) Remove "Contacta o suporte" from checkout failure texts.

**P19. Recovering a payment link.**
- (a) A retry or "Obter link de pagamento" button that reuses the same Confirm callback. No new route.
- (b) A new callback prefix now. This widens scope: it needs an allowlist entry, an ownership check and a routing test.
- (c) Defer to SEC-12.

**P20. Router-level generic error message for all callbacks** (a wider behavior change), or keep the change to checkout and product only.

**P21. Unique `Pagamento.stripe_session_id`.**
- (a) Add it, with a preflight and a data step converting `''` to NULL.
- (b) Leave it as it is.

**P22. Roadmap placement.** Accept pulling Phase 2 items ahead of unfinished Phase 1 work, or reorder.

---

## 6. Proposed Portuguese wording and failure matrix (drafts for review only)

European Portuguese, informal "tu", matching the existing strings. The existing bot copy starts lines with emoji; those prefixes are left out here and are a copy decision.

| Situation | What exists | Draft message | Buttons |
| --- | --- | --- | --- |
| Processing (if P16a) | Nothing yet | "A processar a tua encomenda... Aguarda um momento." | none |
| User creation or order transaction fails | Nothing (rolled back) | "Não foi possível criar a encomenda. Não foi feita nenhuma cobrança. Tenta novamente dentro de momentos." | Tentar novamente (same token), Voltar ao produto or Catálogo |
| Out of stock inside the transaction | Nothing | Existing text, `customer.py:149` ("Lamentamos, este produto ficou sem stock entretanto."). Variant if P14a: "...esgotou entretanto. Nenhuma encomenda foi criada." | existing |
| Audit fails | Per P3 | (a) same as the row above; (b) not shown to the customer | |
| Stripe fails, order kept (P1a) | Order, no session | "A encomenda #{id} foi registada, mas não foi possível gerar o link de pagamento. Não foi feita nenhuma cobrança. Tenta novamente dentro de momentos." | Gerar link de pagamento (same token), Ver Encomendas |
| Stripe fails, order cancelled (P1b) | Cancelled order | "Não foi possível gerar o link de pagamento, por isso a encomenda #{id} foi cancelada. Não foi feita nenhuma cobrança." | Catálogo |
| Session created, `Pagamento` save fails | Order and live session | Per P2: the normal success screen, or the Stripe-failure text with retry | |
| Success message fails to render | Everything saved | Resend with `parse_mode=None`; never show the "link failed" text | Pagar Agora |
| Repeat tap, still in progress / Stripe 409 | One order | Toast: "Esta encomenda já está a ser processada." | no edit |
| Repeat tap, link live | One order | "A encomenda #{id} já foi criada. Não foi criada nenhuma encomenda nova. Usa o botão abaixo para concluir o pagamento." | Pagar Agora, Ver Encomendas |
| Repeat tap, paid | One order | "A encomenda #{id} já está paga. Não foi criada nenhuma encomenda nova." | Ver Encomendas |
| Repeat tap, cancelled or payment failed | One order | "A encomenda #{id} já não está ativa. Para encomendar de novo, abre o produto no catálogo." | Catálogo |
| Repeat tap, session expired (P8a) | One order | "O link de pagamento da encomenda #{id} expirou." (plus a next step per P18) | |
| Legacy button (P4a) | Nothing | "Este ecrã de confirmação já não é válido. Abre o produto novamente para encomendar." | Ver produto (`produto_<id>`) |
| Token from another user, or malformed | Nothing | "Pedido inválido." | |
| Old query (P5b) | Nothing | "Este pedido expirou. Volta ao produto para encomendar." | |
| Photo missing or upload failed | Nothing | Normal caption as text; optional line "(Imagem indisponível)" (P17) | `teclado_produto(p)` |
| Router catch-all (if P20) | Unknown | "Ocorreu um erro. Consulta \"As Minhas Encomendas\" antes de tentar novamente." It must not claim that nothing was charged. | |

On cleanup of partial records: atomic creation (B2) means a failure before commit leaves no rows, and the same token can be reused. The only cleanup question left is a committed pending order with no usable link (P1).

---

## 7. Alternatives raised and why they fall short

| Alternative | Why it falls short |
| --- | --- |
| Disable the button by editing the message first | Taps already queued at Telegram or PTB still run. It does not cover other devices, other workers or restarts, and the edit can fail. Feedback only (P16). |
| In-memory in-flight set | Never fires with one-at-a-time processing. Lost on restart, not shared across workers. |
| "Recently completed" in-memory cache | Same restart and process limits; duplicates the database key. |
| Raising the rate limit or a per-user cooldown | Heuristic, in memory, and blocks legitimate purchases. 5 per 10 s already lets four duplicates through. |
| Key on `(chat_id, message_id)` | `responder` reuses one message across screens, so it blocks a real second purchase. Inline messages have no message. |
| Key on `message.edit_date` | One-second granularity, missing on messages never edited, and still needs a stored column. |
| Key on `callback_query.id` | Unique per tap, so it only covers redelivery of the same update. |
| One pending order per (user, product), as a partial unique index | Changes a business rule. Blocks a second purchase, and an abandoned pending order blocks re-buying until SEC-04 expiry. Needs a denormalized product column. |
| Row lock or advisory lock on user or product | Serializes taps but cannot tell a repeat from an intended second order and stores no outcome. Advisory locks are tied to the connection and awkward with poolers. |
| Holding `select_for_update` across the Stripe call | Keeps a transaction open over network I/O and blocks the shared `sync_to_async` thread. |
| Separate table of issued tokens written when the Confirm screen renders | It could bind the token to the user, snapshot the price and check expiry. It costs a write on every "Encomendar" tap plus a cleanup job; a nullable column gives the duplicate guarantee. |
| Storing the token in `Encomenda.notas` to avoid a migration | Misuses an admin field and gives no unique constraint. |
| Signed (HMAC) token | Not needed: a forged token only creates the forger's own single order. |
| PTB `arbitrary_callback_data` | Held in memory unless persistence is configured; not multi-worker. |
| Stripe idempotency key alone | Prevents duplicate sessions, not duplicate orders or stock changes. Lasts about 24 h. |
| Unique `stripe_session_id` alone | Does not stop duplicate orders. |
| Create the Stripe session first, then the order ("charge before reserve") | A stock failure after the session exists needs `Session.expire`, which can fail. The customer may see a link for a sold-out item. Changes the reservation policy, an open question in ARCHITECTURE.md. |
| Turning on `concurrent_updates` | Turns queued duplicates into real races. Not without the database guard, and not in this change. |
| Global PTB error handler as the main mechanism | Does not know whether an order or session exists, so it cannot give the right status or say truthfully that nothing was charged. Last resort only. |
| Storing webhook event ids (full SEC-05) | More schema than needed. A row lock and state check covers duplicate delivery of this event. |
| Notification outbox | Right in the long run, but out of scope; `on_commit` is enough for now. |
| Stripe Search API for reconciliation | Does not cover Checkout Sessions. The signed event's metadata or `client_reference_id` is the link. |
| Caching Telegram `file_id`s for photos | Needs a column; a later improvement, not a fallback. |

---

## 8. Acceptance criteria and tests (consolidated)

**Acceptance criteria**

- **AC1.** For any single Confirm token, across repeated, queued, concurrent and post-restart taps, the database ends with:
  - one `Encomenda`;
  - one `LinhaEncomenda`;
  - one stock "saida" movement, with stock decremented once;
  - at most one CRIAR_ENCOMENDA audit row;
  - at most one Stripe session id.
- **AC2.** Two different confirmation screens for the same user and product create two orders.
- **AC3.** A repeat tap never calls `answer()` a second time, never shows a traceback, and never removes a live "Pagar Agora" button.
- **AC4.** Every exception after `answer()` ends in exactly one Portuguese status render (an edit, or the `send_message` fallback) and one log line with context.
- **AC5.** Signature verification is unchanged. An invalid signature returns 400 and changes nothing.
- **AC6.** A duplicate webhook delivery causes one 'pago' transition, one PAGAMENTO_CONFIRMADO audit row and one notification, sent on commit.
- **AC7.** A paid session with no `Pagamento` row but a valid metadata order id ends with the order linked and confirmed. Mismatches follow P11.
- **AC8.** A missing photo or a failed upload produces a text-only product screen with `teclado_produto`. The old message is never deleted before a replacement is guaranteed.
- **AC8b.** Checkout succeeds for a product whose image file is missing (B3).
- **AC9.** The migration is additive. Existing rows are untouched, and any number of NULL keys is allowed.

**Tests.** "Handler" tests are mock-only `SimpleTestCase` tests in `telegram_bot/tests.py`. "DB" tests use PostgreSQL in `core/tests.py`. "Thread" tests use `TransactionTestCase` with OS threads.

| # | Test | Kind |
| --- | --- | --- |
| K1 | The Confirm callback for id 9223372036854775807 is at most 64 bytes, passes `validar_callback`, round-trips through the parser and is routed | handler |
| K2 | A legacy `confirmar_encomenda_7` gets the P4 response, calls no service, and answers once | handler |
| K3 | A malformed token (non-hex, wrong length, extra segments) is rejected with no service call | handler |
| K4 | `CheckoutAcknowledgementTests` updated to the new format: still answer before Stripe, still one answer | handler |
| K5-K10 | One test per failure stage (user, order transaction, audit, Stripe create, Pagamento save, final render). Each: one answer, one status message, the key words, the right buttons; a render failure is not reported as a Stripe failure | handler |
| K11 | A repeat tap with a live link and "Message is not modified" shows no error and does not remove the Pay button | handler |
| K12 | A product name containing `_*[` still gets a delivered status message | handler |
| P1 | Missing image file: text product screen via edit, `message.delete` not called | handler |
| P2 | `send_photo` raises `BadRequest` or `NetworkError`: `send_message` is sent with the caption and `teclado_produto`; delete happens only after a successful send | handler |
| P3 | `send_photo` raises `TimedOut`: handled per P17d | handler |
| P4 | A failed `edit_caption` or a caption over 1024 characters falls back to text | handler |
| M1 | Migration: existing orders keep NULL; two NULLs coexist; a duplicate non-NULL key raises `IntegrityError`; reverse migration works | DB |
| S1 | The same token twice in sequence gives the AC1 counts; the second reply shows the same order id and the link | DB |
| S1b | A repeat tap when stock is now 0, or the product is inactive, returns the existing order | DB |
| S2 | Different tokens give two orders (AC2) | DB |
| S3 | Another user's token, or a mismatched product, is rejected and creates nothing | DB |
| S4 | A failure injected after the order insert leaves zero rows, stock unchanged, and the token reusable | DB |
| S5 | Checkout on a product whose image file is missing succeeds (B3) | DB |
| S6 | Stock 0 at reservation creates no order (only if P14a) | DB |
| R1 | Restart: clear `_rate_data` and `_ultima_atividade`, seed an active session, tap again: the existing order comes back and `Session.create` is not called when a session id exists. Separate test: expected expired-session behavior. | DB |
| R2 | `Session.create` succeeds, then the `Pagamento` save raises once: reply per P2; the retry passes the same `idempotency_key`; exactly one `Pagamento` with that session id | DB |
| R3 | Stripe create raises, then the retry succeeds: one order and one `Pagamento` | DB |
| R4 | The webhook already linked the `Pagamento` as 'pago': the bot's later save keeps 'pago' and shows the paid status | DB |
| R5 | `answer()` raises `BadRequest` ("query too old"): behavior per P5 | handler or DB |
| R6 | Stripe 409 idempotency conflict renders "a ser processada" | handler |
| R7 | Product price and name change between attempts: the Stripe parameters are unchanged (snapshot, if P15a) | DB |
| W1 | Valid signature with the `Pagamento` present: paid, order confirmed, one audit row, one `httpx.post` | DB |
| W2 | The same event delivered twice in sequence: one audit row, one notification | DB |
| W3 | `Pagamento` missing, metadata holds the order id: linked and paid, one audit row, one notification | DB |
| W4 | `Pagamento` and metadata both missing or unknown: response per P12, no row changes | DB |
| W5 | Invalid signature returns 400 with no changes (also checks N1) | DB |
| W6 | Amount or currency mismatch, or a conflicting session id: handled per P11 | DB |
| C1 | Two threads with the same user and token, made deterministic with a `Barrier` or `Event`: one order, stock decremented once, both get the same id, no `IntegrityError` reaches the handler | thread |
| C2 | Two threads POST the same signed webhook event: one state change, one audit row, one notification (if P13a) | thread |
| C3 | Stock 1, two different tokens at once: exactly one order (if P14a) | thread |

**Test conventions:**
- **Stripe:** patch `core.services.stripe.checkout.Session.create` and `.retrieve` to return `SimpleNamespace(id="cs_test_1", url=...)`, and assert the `idempotency_key` keyword argument.
- **Webhook:** use `django.test.Client` to POST to `reverse("stripe_webhook")`. Use a real HMAC `Stripe-Signature` header under `override_settings(STRIPE_WEBHOOK_SECRET="whsec_test")`, and patch `core.views.httpx.post`.
- **Telegram mocks:** set `message.photo=None` and `message.document=None` explicitly. Use `AsyncMock` for `answer`, `edit_message_text`, `delete`, `send_photo` and `send_message`.

---

## 9. Verification gaps carried forward

Not confirmed by any reviewer:
- PTB 22.7: the `concurrent_updates` default and whether `run_polling` keeps pending updates.
- Telegram: the "query is too old" window, the "message is not modified" error, the 64-byte callback limit and the 1024-character caption limit.
- stripe 15.2.1: the `idempotency_key` keyword, the roughly 24 h key retention, replay of stored errors, the `stripe.error` alias, and `Session.url` being null after completion.
- PostgreSQL: that a second insert of the same key blocks on the unique index until the first transaction finishes.

Settle these before or during implementation.
