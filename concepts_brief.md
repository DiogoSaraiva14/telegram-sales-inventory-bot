# One order per confirmation screen, and a product screen that survives a bad photo (SEC-13, SEC-03 photo part)

* **Author:** Jose Diogo Pereira Saraiva
* **Date:** 2026-11-22
* **Status:**
  * **Change 1 (photo fallback):** approved and implemented in the working tree. Not committed.
  * **Change 2 (duplicate checkout):** implemented on branch `codex/checkout-change-2`, not committed.
    * `python manage.py test core telegram_bot` passes on PostgreSQL: 73 tests, with 1 expected failure (SEC-12).
    * Two review fixes were added after the first implementation (§3.11).
    * The design below is kept as it was approved.
* **Background:** [goldfish_brainstorm_report.md](goldfish_brainstorm_report.md) holds the full Goldfish review (7 lenses; 4 blocker, 18 major, 9 nit findings; product choices P1–P22). Ids such as B3, M8 or P6 refer to it. This brief narrows that review to two changes.

## Overview

The owner set four stages:

1. **Verify the assumptions** against the pinned library versions and the current code. Done; see Analysis §1.
2. **Change 1, photo fallback.** A separate, small change: if a product image is missing or its upload fails, the product screen still appears, as text, with working buttons. Implemented; see §2.
3. **Change 2, duplicate checkout.** A database-backed confirmation token and all-or-nothing order creation, so one Confirm screen creates at most one order. §3 shows the model, the migration, how a token maps to an order (including the race on the unique constraint), the expiry rule, and the test plan. Implemented; see §3.11 for what differs from this design.
4. **Deferred:** webhook and payment recovery, and new PostgreSQL concurrency test infrastructure. See §4.

## Analysis

### Risks in this narrower plan, stated first

- **The callback is exactly 64 bytes at the maximum order id** (§3.1). Telegram's limit is 64, so there is no spare byte. A boundary test and a length assert guard it.
- **Stripe idempotency only replays while the parameters match** (V8).
  - Every parameter is therefore built from saved order data, including a new product-name snapshot on the order line (§3.9).
  - The only remaining variable is the success and cancel URLs from the environment.
- **Expiry is measured when the bot processes the tap, not when the customer tapped.**
  - Telegram callback queries carry no tap time, and the `CallbackQuery` object has no date field.
  - So after more than 30 minutes of bot downtime, a tap the customer made in time is rejected as expired. That is the safe direction (no order), but the customer must start again. §3.5 shows each case.
- **A database test is required before Change 2 is accepted (D10), and it needs credentials.**
  - PostgreSQL 17 is installed and running on this machine (service `postgresql-x64-17`).
  - The repo has no `.env`, so I have no role or password for it. The test needs a role that can create databases.
  - Without that, the database tests can be written and left runnable, and I will report them as not run. Mock tests do not prove the constraint.
  - §3.8 lists the role and the environment variables.
- **Replacement sessions (D19) change `Pagamento` in place.**
  - The stored session id moves to the new session, and `falhado` goes back to `pendente`.
  - A late expiry event for the old session then matches no row; today's webhook logs it and returns 200.
  - This is the only payment-state change Change 2 makes, and it never touches `pago`.

### 1. Verified assumptions

All library facts were read from the installed pinned packages: python-telegram-bot 22.7 and stripe 15.2.1, installed in a scratch environment from `requirements.txt`. Server-side facts come from the official docs, read on 2026-11-22.

| # | Assumption | Result | Source | Remaining uncertainty |
| --- | --- | --- | --- | --- |
| V1 | The bot handles updates one at a time | **Confirmed.** `ApplicationBuilder` defaults to `SimpleUpdateProcessor(max_concurrent_updates=1)`, and the update loop awaits each update before taking the next one when the limit is 1. `telegram_bot/app.py` does not change this. | `telegram/ext/_applicationbuilder.py:216-218`; `telegram/ext/_application.py:1221-1230` | None for 22.7. A later `concurrent_updates(...)` setting or a second worker would make taps truly concurrent. |
| V2 | A second Confirm tap waits in line and then runs the whole checkout again | **Confirmed by V1 plus the code.** `confirmar_encomenda` has no check that would stop it. An in-memory "in flight" guard would always be empty when the second tap runs. | `telegram_bot/handlers/customer.py:140-178` | None |
| V3 | Taps sent while the bot is down are delivered after restart | **Confirmed.** `run_polling` defaults to `drop_pending_updates=False`. Telegram keeps undelivered updates "not … longer than 24 hours". | `telegram/ext/_application.py:805-806`; Bot API, "Getting updates" | Only updates up to 24 h old arrive. Buttons in chat history can still be pressed later; that is a new update. |
| V4 | Callback data limit | **Confirmed: 1–64 bytes, UTF-8.** PTB enforces the same constant. | Bot API, "InlineKeyboardButton" (`callback_data`); `telegram/constants.py:1395` (`MAX_CALLBACK_DATA = 64`) | None. The repo's own validator allows 200 characters (`security/sanitization.py:47`); this design must stay under 64. |
| V5 | Deadline for answering a callback query ("query is too old") | **Not documented.** The docs say clients "display a progress bar until you call answerCallbackQuery", but give no deadline for callback queries. The only documented 15-second deadline is for `answerWidgetQuery`. | Bot API, "CallbackQuery" note and "answerCallbackQuery" | **Unknown.** The design must not depend on a particular window. A late `answer()` may raise; today that aborts the handler before any order exists. |
| V6 | stripe 15.2.1 accepts `idempotency_key` on `checkout.Session.create` | **Confirmed.** `create(**params)` passes its params through `extract_options_from_dict`, which removes `idempotency_key` and sends it as the `Idempotency-Key` header. | `stripe/checkout/_session.py:2575-2586`; `stripe/_api_resource.py:170`; `stripe/_request_options.py:75-91`; `stripe/_api_requestor.py:577-579` | None for the library |
| V7 | Without our own key, Stripe calls are already idempotent | **Only within one call.** The library adds a random `uuid4` key to every POST that has none, and reuses it for its own network retries (default `max_network_retries = 2`). A second call, such as a second tap or a retry, gets a new key and creates a new session. | `stripe/_api_requestor.py:581-583`; `stripe/__init__.py:38` | None |
| V8 | Stripe idempotency behavior on the server | Keys may be removed once they are at least 24 h old. A repeated key with different parameters returns an error. Saved responses are replayed for the same key, "including 500 errors". Nothing is saved if validation fails or another request with the same key is still running. | Stripe docs, "Idempotent requests" | The docs do not say what the second of two concurrent same-key requests receives. `stripe.IdempotencyError` exists in 15.2.1 (`stripe/_error.py:151`). |
| V9 | `stripe.error.SignatureVerificationError`, used by the webhook, still exists | **Confirmed.** `stripe.error` is kept as an alias of `stripe._error`, which defines `SignatureVerificationError`. Finding N1 is resolved. | `stripe/__init__.py:114, 544`; `stripe/_error.py:184` | None |

**What changes because of these checks:**
- V1 and V2 settle it: disabling the button and an in-memory guard cannot stop the duplicate, so the database token is required.
- V4 fixes the token budget: `confirmar_encomenda_` is 20 bytes, a BigAutoField id is at most 19 digits, and a separator is 1 byte. That leaves 24 bytes for the token.
- V6 and V7 show a Stripe idempotency key is available. Change 2 uses it after D17 (§3.9).


### 2. Change 1: photo fallback (approved, implemented, not committed)

**Owner choices applied:**
- **D1:** the fallback screen adds the line `(Imagem indisponível)`. It is shown only when a product has an image that could not be opened or sent. Products without an image keep today's screen with no note.
- **D2:** on a missing file, an upload error or a timeout, the old message is kept and edited into the no-image screen with the same buttons.

**What changed** (`telegram_bot/handlers/customer.py`, `produto_detalhe`):
- The photo is sent first, and the old message is deleted only after the send succeeds.
- `OSError` (file missing or unreadable) and `telegram.error.TelegramError` (including `BadRequest` and `TimedOut`) are caught. The handler logs a warning with the product id, then calls `responder(query, context, caption + "\n(Imagem indisponível)", keyboard)`.
- A failed delete after a successful send is ignored and logged at debug level.
- The "message is already a photo" branch (`edit_caption`) is unchanged.

**Known limit:** on `TimedOut` the photo may still have arrived. The customer then sees the photo screen and the text screen; both have working buttons.

**Tests added** (`telegram_bot/tests.py`, `PhotoFallbackTests`, mock-only; each case answers the callback exactly once):
- Missing file: the old message is edited to text with `(Imagem indisponível)` and the `encomendar_7` / `cat_ssd` buttons; no send and no delete.
- `BadRequest` on send: same fallback; no delete.
- `TimedOut` on send: same fallback; no delete.
- Success: photo sent, then the old message deleted; no fallback.
- Delete fails after a successful send: no error.

**Checks run:** `manage.py check` found no issues. `manage.py test telegram_bot core` ran 16 tests: 15 passed and 1 was the expected SEC-12 failure. The logged tracebacks in the output are the warning logs from the fallback tests.

**Also updated:** README.md (known gaps) and SECURITY.md (SEC-03 now records the photo fix and the still-open checkout image failure, B3).

**Not covered:** no run against a real Telegram bot or a real missing image on a server.

**B3 (checkout fails on a missing image)** stays with Change 2, as the owner decided (D11).

### 3. Change 2: duplicate checkout (implemented; see §3.11)

**Owner choices applied:**

| Id | Decision |
| --- | --- |
| D3 | Old buttons without a token are stale: a short message, and the customer starts checkout again |
| D4 / D4a | Unused tokens expire 30 minutes after the confirmation screen was created; that time is carried inside the callback token |
| D5 | The audit row is written inside the order transaction |
| D6 | Checkout is refused when stock is insufficient; stock is never silently clamped at zero |
| D7 / D12 | Order-creation failure text: "❌ Não foi possível registar a encomenda. Tenta novamente."; the details go to the logs only |
| D8 / D15 | Repeat tap: the alert "Esta encomenda já foi registada.", and the payment button is kept |
| D9 | The token is globally unique |
| D10 | A PostgreSQL-backed test of the unique-token behavior is required; Change 2 does not start until it can run |
| D13 | The failure screen has "🔄 Tentar novamente" (same token) and "🔙 Voltar aos produtos" |
| D14 | Stale text "Este ecrã expirou. Abre o produto novamente para continuar.", with a "🔙 Voltar ao Produto" button that opens a fresh product screen |
| D16 | "Voltar aos produtos" opens the category's product list (`cat_<categoria>`) |
| D17 | A retry or repeat tap that finds the order recovers or creates that order's Stripe Checkout Session, and the session is persisted before the link is shown. The webhook stays as it is |
| D18 | The payment-link failure screen gets "🔄 Tentar novamente" (same callback) |
| D19 | If the saved order is unpaid and its session has expired, create a replacement session with a new idempotency key |
| P15 | Always charge the order's saved total; every Stripe parameter comes from saved order data |
| D20 | No product description in the Stripe line item; the saved name and total are enough |
| D21 | "⏳ O pagamento da encomenda #{id} está a ser confirmado. Não voltes a pagar. Receberás uma mensagem quando a confirmação chegar." for a completed session awaiting the webhook |

#### 3.1 Token and callback format (D4a)

- `encomendar` (which draws the "Confirmar Encomenda" screen) builds a token on every render. Nothing is stored at that point.
  - **nonce:** `secrets.token_hex(8)`, 16 lowercase hex characters (64 random bits).
  - **ts:** the render time as Unix seconds in base36, zero-padded to exactly 7 characters, using `0-9a-z`.
- **Callback:** `confirmar_encomenda_<nonce>_<ts>_<produto_id>`, for example `confirmar_encomenda_21489ae5df01141f_0towjqe_7`.
- **Length, checked against the real strings** (computed on 2026-11-22):
  - prefix 20 + nonce 16 + separator 1 + ts 7 + separator 1 + id 19 = **64 bytes**, at the maximum BigAutoField id 9223372036854775807 (2⁶³ − 1).
  - That is exactly Telegram's limit (V4), so **there is no spare byte.** Any change to the prefix, the nonce or the separators needs a shorter part elsewhere.
  - Today's time needs only 6 base36 characters (`towjqe`). Six characters last until 2038-12-24 and seven until the year 4453, which is why the timestamp is padded to a fixed 7.
- **Parser:** split the text after the prefix on `_`.
  - Exactly three parts: nonce `^[0-9a-f]{16}$`, ts `^[0-9a-z]{7}$`, and the id through `sanitizar_id`.
  - Exactly one part: a legacy button, handled by D3.
  - Anything else: today's "❌ Produto não encontrado." reply, with no service call.
- **Rollback:** the id stays the last segment, so rolled-back code that reads `split("_")[-1]` still reads a product id.
- **Stored value:** `Encomenda.chave_confirmacao` stores the nonce only. The timestamp is used only for the expiry check.
- **Forged parts:** a forged nonce or timestamp only affects the forger's own single order, because the lookup always checks the user.

#### 3.2 Model and migration

```python
# core/models.py, class Encomenda
chave_confirmacao = models.CharField(
    max_length=16, null=True, blank=True, unique=True, editable=False,
)

# core/models.py, class LinhaEncomenda: product name as it was when the order was made (P15, §3.9)
nome_produto = models.CharField(max_length=200, null=True, blank=True, editable=False)
```

- **`core/migrations/0008_checkout_idempotencia.py`:** two additive `AddField` operations.
  - `nome_produto` is filled only by `reservar_encomenda`. It is `NULL` on older lines, which never reach the new payment path, because their orders have no nonce.
  - No counter column is needed for replacement keys (§3.9).
- **Existing orders** keep `NULL`, with no backfill. PostgreSQL allows any number of `NULL`s under a unique constraint.
- **Indexes and admin:** Django adds the usual extra `_like` index for a unique `CharField`. `editable=False` keeps the field out of the Django admin form.
- **Deploy and rollback:** run `migrate` first, then deploy the code. To roll back, deploy the old code (the column can stay), or run `migrate core 0007`, which drops only these two columns and the token's indexes.

#### 3.3 How a token maps to an order, including the race

A new synchronous service in `core/services.py`, called once through `sync_to_async`. Stripe stays outside it.

```text
reservar_encomenda(telegram_user, produto_id, nonce) -> (resultado, encomenda)
  resultado: "criada" | "existente" | "de_outro_utilizador" | "sem_stock"

BEGIN                                                    -- transaction.atomic()
 1  utilizador = Utilizador get_or_create (same sanitizing as today)
 2  produto = Produto SELECT ... FOR UPDATE WHERE id = produto_id AND ativo
                                                          -- one checkout per product at a time
 3  existing = Encomenda WHERE chave_confirmacao = nonce  -- global lookup (D9)
    if existing: return ("existente" if existing.utilizador == utilizador
                         else "de_outro_utilizador", existing)
 4  if produto.stock_atual < 1: return ("sem_stock", None) -- D6; nothing written yet
 5  SAVEPOINT
      encomenda = Encomenda INSERT (utilizador, 'pendente', total = produto.preco,
                                    chave_confirmacao = nonce)
    on unique violation:
      ROLLBACK TO SAVEPOINT
      existing = Encomenda WHERE chave_confirmacao = nonce
      return ("existente" or "de_outro_utilizador", existing)   -- same rule as step 3
 6  LinhaEncomenda INSERT (quantidade 1, preco_unitario = subtotal = produto.preco,
                          nome_produto = produto.nome)          -- snapshot for Stripe (P15)
 7  Produto UPDATE stock_atual = stock_atual - 1 WHERE id = produto.id
                                                          -- F() update: no Produto.save(), no image access (B3)
 8  MovimentoStock INSERT ('saida', 1, "Encomenda #<id> via bot")
 9  RegistoAuditoria INSERT ('CRIAR_ENCOMENDA', 'Encomenda', "#<id> — <nome> — <preço>€")  -- D5
COMMIT
return ("criada", encomenda)
```

- **Any exception before COMMIT** rolls everything back, so the nonce is still unused.
- **Sequential repeat** (the normal case, V1): the second call reaches step 3 after the first has committed and returns `existente`.
- **Same nonce, same product, at the same time:**
  - The second transaction waits on the product row lock (step 2) until the first commits.
  - In PostgreSQL's default READ COMMITTED mode, each statement sees what was committed before it started, so step 3 then finds the order.
  - Result: no second order and no `IntegrityError`.
- **Same nonce, different products** (only possible with a forged callback):
  - The two transactions lock different rows, so the unique index is what decides.
  - The second insert waits on the index. If the first commits, the second hits the unique violation, rolls back to the savepoint, looks the nonce up and returns. If the first rolls back, the second insert succeeds.
  - The savepoint keeps the outer transaction usable after the failed insert.
- **Two first-time users created at once:** step 1's `get_or_create` already handles that.
- **Locks:** one product row, held for milliseconds. It is never held across a Stripe or Telegram call.
- **The handler's own lookup before this service** (§3.5, step 2) is only a fast path. Steps 3 and 5 are the guarantee.

#### 3.4 Expiry (D4 / D4a)

- The age is `now − int(ts, 36)`, compared with 30 minutes.
- A ts more than 60 seconds in the future (clock skew, or forged) is treated as stale.
- **Expiry applies only to a nonce that has no order.**
  - The handler looks the nonce up before it checks the age (§3.5, steps 2 and 3).
  - If an order exists for this user, the age is ignored: the customer gets the D8 alert, then payment recovery (§3.9), so a payment retry works however old the button is.
  - No second order is possible, because the nonce is already stored under the unique constraint.
  - Only when no order exists does a callback older than 30 minutes get the stale screen.
- **The age is measured when the bot processes the tap,** because Telegram does not record when the button was pressed.
  - A tap made in time but processed after a long outage gets the stale message. The owner accepted this.
  - Seconds spent queued behind other updates do not matter.
- **Why not `message.date`:** most screens are drawn by editing one message, so `message.date` is often far older than the Confirm screen. For a product without an image, the /start reply's time would be used, so customers browsing for more than 30 minutes could never confirm. The token timestamp is exactly the render time.

#### 3.5 Handler flow, and how repeated, stale and delayed taps behave

**Flow of `confirmar_encomenda`:**
1. **Parse** (§3.1). A legacy button gets the stale screen (D3, D14). A malformed one gets "❌ Produto não encontrado.".
2. **Look the nonce up first.**
   - **Order found, same user:** `query.answer("Esta encomenda já foi registada.", show_alert=True)` (D8/D15), then **payment-session recovery** (§3.9).
   - **Order found, other user:** `query.answer("❌ Pedido inválido.", show_alert=True)`, with no edit.
3. **Unused nonce, ts older than 30 minutes** (§3.4): the stale screen.
4. **Product lookup and the early stock check,** as today. The authoritative stock check is §3.3 step 4.
5. **`query.answer()`, then `reservar_encomenda`:**
   - `criada`: §3.9 creates and persists the session, then shows "💳 Pagar Agora".
   - `existente` (only after a true race): run payment recovery too (§3.11, review fix P2).
   - `de_outro_utilizador`: edit the message to "❌ Pedido inválido.".
   - `sem_stock`: today's "😔 Lamentamos, este produto ficou sem stock entretanto." with the Catálogo button.
   - **Any exception:** `logger.exception` with the product id and stage (no customer name, SEC-08), then edit the message to the D7 text in plain text with the D13 buttons.

**Screens (plain text unless noted):**

| Screen | Text | Buttons |
| --- | --- | --- |
| Stale (D3, D4) | "Este ecrã expirou. Abre o produto novamente para continuar." | "🔙 Voltar ao Produto" → `produto_<id>` (a fresh product screen; pressing Encomendar there makes a new token) |
| Payment being confirmed (D21, approved) | "⏳ O pagamento da encomenda #{id} está a ser confirmado. Não voltes a pagar. Receberás uma mensagem quando a confirmação chegar." | "🛒 Ver Encomendas" only |
| Order-creation failure (D7, D13) | "❌ Não foi possível registar a encomenda. Tenta novamente." | "🔄 Tentar novamente" → the same `confirmar_encomenda_<nonce>_<ts>_<id>`; "🔙 Voltar aos produtos" → `cat_<categoria>` (D16) |
| Payment-link failure (D18) | Today's "⚠️ Não foi possível gerar o link de pagamento. Contacta o suporte." under "✅ Encomenda #<id> criada!" | "🔄 Tentar novamente" with the same callback, plus today's "🛒 Ver Encomendas" |

| Situation | Today | Proposed |
| --- | --- | --- |
| Second tap on the same screen, queued behind the first (V1) | Second order; the first order's link is replaced, or "sem stock" is shown | Alert; §3.9 redraws the same order's payment screen; no new order |
| Same screen tapped on a second device | Same as above | Same as above |
| Two workers process both taps at once (future) | Two orders | One order; the loser waits on the product lock and gets `existente` |
| Repeat tap after the product sold out or was deactivated | "sem stock" or "Produto não encontrado" | The existing order is found first; alert and §3.9 |
| Repeat tap hours later on a used nonce | New order | Alert and §3.9; used nonces never expire |
| Callback over 30 minutes old, order exists and is unpaid (for example "Tentar novamente" from the D18 screen) | New order | Alert, then §3.9 recovers the session or creates a replacement; no second order |
| Customer paid, but the webhook has not yet marked the payment | New order | Alert, then the "pagamento está a ser confirmado; não voltes a pagar" screen without a pay button; no new or replacement session, no database write (§3.9 step 3, D21) |
| Unused nonce, tapped and processed within 30 minutes | Order | One order |
| Unused nonce tapped at 5 min, bot down until minute 45 | Order (if the in-memory session check lets it through) | Stale screen, no order |
| Unused nonce tapped after 30 minutes | Order | Stale screen, no order |
| Delivered more than 24 h after the tap | Never delivered (Telegram drops it) | Same |
| First tap after a bot restart | "Sessão expirada" from the in-memory session check | Unchanged: that check runs before the router |
| Legacy button `confirmar_encomenda_7` | Order | Stale screen, no order |
| Stock 0 inside the transaction | Order created, stock clamped at 0 | "sem stock", nothing written (D6) |
| Audit insert fails | Order and stock saved, no audit row, no reply | Everything rolled back; D7 screen with "Tentar novamente" (D5) |
| Missing product image file | Checkout fails after the order and stock were saved (B3) | Checkout no longer reads the image |
| Stripe fails after the order was committed | "Não foi possível gerar o link…"; no way forward | Same text; "Tentar novamente" (D18) runs §3.9 with the same attempt's key |
| Unpaid order, session expired after 24 h | Order stays pending; no way to pay | Repeat tap or "Tentar novamente": a replacement session with a new key (D19) |
| Order cancelled by an admin, then a repeat tap | New order | Alert only; no session created (`nao_ativa`) |

#### 3.6 Retry with the same token, including an uncertain commit (D13)

A retry is an ordinary Confirm tap with the same callback. The database decides what it finds:

| First attempt | Database | Retry | Result |
| --- | --- | --- | --- |
| Failed before or during the transaction | Nothing (rolled back) | Nonce unused: creates the order, within 30 minutes of ts | One order |
| Connection lost during COMMIT; outcome unknown | All of the order or none of it | Lookup: order found means alert and §3.9; not found means create | At most one order |
| Committed; the reply, Stripe or the screen failed | The order (and maybe a session, §3.9) | Lookup finds it: alert and §3.9 recover or create the session | One order, one session |
| Two retries race, or a retry races the original on another worker | Whichever commits first | Product lock or unique index: the other gets `existente` | One order |

- **The order cannot be duplicated** because every retry carries the same nonce, the order is committed with all its rows or not at all, and the unique index rejects a second row with that nonce.
- **The failure text may be wrong:** "Não foi possível registar" can be shown when the order did commit but the commit's reply was lost. The retry then shows "já foi registada" and recovers the payment link, so the customer always has a way forward.
- **The 30-minute window** applies only while the nonce is unused. A committed order is always found first.

#### 3.7 Test plan for Change 2

**Mock-only, in `telegram_bot/tests.py`** (stays database-free; checks branches, not the constraint):
- **H1 boundary:**
  - At id 9223372036854775807 with a real nonce and a 7-character ts, the callback is exactly 64 UTF-8 bytes, passes `validar_callback`, round-trips through the parser and routes to `confirmar_encomenda`.
  - A ts of 6 or 8 characters is rejected.
  - The callback builder asserts the result is at most `InlineKeyboardButton.MAX_CALLBACK_DATA` (64) bytes. A 65-byte variant, such as one with an 8-character ts, fails that assert.
- **H2** Legacy `confirmar_encomenda_7`: the stale text, plus the "Voltar ao Produto" button with `produto_7`; no service call; one answer.
- **H3** Malformed parts (non-hex nonce, uppercase or wrong-length ts, extra segments): "Produto não encontrado", no service call.
- **H4** Existing order, same user: exactly one answer, `("Esta encomenda já foi registada.", show_alert=True)`, then the §3.9 path. No `reservar_encomenda`.
- **H5** Existing order, other user: one "Pedido inválido" alert, no edit, no service.
- **H6** Expiry with a fixed clock: ts age 29:59 proceeds; 30:01 is stale; 61 s in the future is stale; a used nonce at any age gets H4.
- **H13** A callback 45 minutes old whose nonce belongs to an existing unpaid order of this user: the alert, then `garantir_sessao_pagamento` is called. There is no stale screen, no `reservar_encomenda` call and no new order.
- **H7** `existente` after the answer: no edit, no Stripe.
- **H8** `sem_stock`: the existing text, no Stripe.
- **H9** `reservar_encomenda` raises: the exact D7 text in plain text, `logger.exception` called, no exception text in the message, one answer, and the buttons are "Tentar novamente" (identical callback) and "Voltar aos produtos" (`cat_<categoria>`).
- **H10** `encomendar` produces a different nonce on each render, and a ts equal to the clock.
- **H11** The existing `CheckoutAcknowledgementTests` move to the new format and still check "answer before Stripe".

**PostgreSQL, in `core/tests.py`** (Django `TestCase`; required before acceptance, D10):
- **DB1** The same nonce reserved twice: one order, one line, one movement, stock lowered by exactly 1, one CRIAR_ENCOMENDA audit row; the second call returns `existente` with the same id.
- **DB2** The unique-index race path, made deterministic: an order with nonce X already exists, and the step-3 lookup is patched to miss once. Expect a unique violation, a rollback to the savepoint and `existente`, with counts unchanged and the outer transaction still usable.
- **DB3** Nonce X belongs to user A and user B uses it: `de_outro_utilizador`, no rows.
- **DB4** Two nonces for the same user and product: two orders, stock lowered twice.
- **DB5** Stock 0: `sem_stock`, no rows, stock still 0.
- **DB6** The audit insert fails: nothing is written at all.
- **DB7** The product's image file is missing: the reservation succeeds (B3).
- **DB8** Two `NULL` nonces coexist; a duplicate non-`NULL` nonce raises `IntegrityError`.
- **DB9** Retry after a committed attempt whose reply was lost: `existente`, same id, nothing else changes.
- **DB10** Retry after a failed attempt (the audit fails once): the second call returns `criada`; exactly one order.
- **Payment session (D17–D19, P15), with Stripe mocked:** S1–S16 in §3.9, covering first-attempt retries with a stable key, replacement with a new key, and request parameters taken from saved order data.
- **DB11** `reservar_encomenda` stores `nome_produto` equal to the product name at order time.
- **Manual migration check** on a scratch database: `migrate core 0008`, then `0007`, then `0008`.
- **Required threaded tests** (owner decision G1): T1, with stock 1, two different nonces and one order; and T2, the same nonce from two threads, giving one order. Both are described in §3.10.

#### 3.8 PostgreSQL test setup (needed before Change 2 starts)

The tests use the settings in `config/settings.py`, which read these variables from a git-ignored `.env` at the repo root, or from the shell. No password goes in chat or in the repo.

| Variable | Value for local tests |
| --- | --- |
| `SECRET_KEY` | Any local placeholder |
| `DB_NAME` | `telegram_bot` (Django creates and drops `test_telegram_bot` itself) |
| `DB_USER` | `telegram_bot_test` |
| `DB_PASSWORD` | The password chosen when creating the role |
| `DB_HOST` | `localhost` |
| `DB_PORT` | `5432`, or the port the local PostgreSQL 17 service uses |

The role needs only `LOGIN` and `CREATEDB`. Creating it requires the existing PostgreSQL administrator account (`postgres`), whose password was set when PostgreSQL 17 was installed. Two prompts appear in the terminal:
- `--pwprompt` asks for the **new** role's password, twice.
- Then `createuser` asks for the **`postgres`** password, to log in.

Neither password belongs in chat, in a command line, or in the repo. No extra grant is planned for T1; its preflight reports if one is needed (§3.10).

**Alternative: pgAdmin, with the account it already uses.**
1. Check what that account may do, in pgAdmin's Query Tool:
   ```sql
   SELECT current_user, rolsuper, rolcreaterole, rolcreatedb
   FROM pg_roles WHERE rolname = current_user;
   ```
2. If `rolsuper` is true, or both `rolcreaterole` and `rolcreatedb` are true, it can create the role. Since PostgreSQL 16, a non-superuser with `CREATEROLE` can only give `CREATEDB` if it has `CREATEDB` itself. That is from the PostgreSQL 16 release notes as I recall them; I did not check it against the docs here.
3. In pgAdmin, open Login/Group Roles → Create:
   - name `telegram_bot_test`;
   - turn on "Can login?" and "Create databases?";
   - leave "Superuser?" off;
   - set the password in the dialog.
4. If the account cannot create roles, connect with an existing PostgreSQL admin account. Do not reset authentication settings for this. If the `postgres` password is unknown, don't guess it. Ask for a safe local path; resetting it means changing PostgreSQL's local authentication settings, which is an administrator action to do by hand, not something I should change.

```powershell
& "C:\Program Files\PostgreSQL\17\bin\createuser.exe" -U postgres -h localhost --createdb --pwprompt telegram_bot_test
```

- `python manage.py test core` runs the database tests, and `python manage.py test telegram_bot` stays database-free.
- I will run them without reading or printing `.env`.
- If the connection fails, I report it and leave the tests in place, runnable and not counted as passed.

#### 3.9 Payment-session recovery and replacement (D17, D18, D19, P15)

**Scope:**
- Bot side only; the webhook is unchanged.
- The webhook already finds the payment by `stripe_session_id`. The bot saves or updates that field before it shows any link, so whenever a customer can pay, the matching row exists.

**Terms:**
- **Session attempt:** one Stripe Checkout Session that the bot means to create for an order.
- **First attempt:** made when the order has no `Pagamento` yet.
- **Replacement attempt:** made when the stored session has `status == 'expired'` and the order is still unpaid (D19).

##### Keys: stable within an attempt, new for each replacement

| Attempt | Idempotency key | Stays the same for | Changes when |
| --- | --- | --- | --- |
| First | `checkout-encomenda-<encomenda.id>-<chave_confirmacao>-u<f>` | Every retry until a `Pagamento` row holds a session id, while the URLs are unchanged | Never: once the row exists, the bot only retrieves or replaces |
| Replacement | `checkout-encomenda-<encomenda.id>-substitui-<sha256(expired session id)>-u<f>` | Every retry while the stored session id is still the expired one, and the URLs are unchanged | Automatically on the next replacement, because the stored id is then the new session's id |

- **No new counter column is needed.** The replacement key is derived from the session id it replaces, which is saved in `Pagamento.stripe_session_id`.
- **Retries of the same attempt reuse the key.** Suppose `Session.create` succeeds but saving the new id fails: the row still holds the expired id, so the retry computes the same key, and Stripe returns the same new session (V8).
- **Each replacement gets a fresh key,** so Stripe never treats a replacement as a replay of an older attempt.
- **Uniqueness:** keys contain the order id plus either the order's random nonce or a Stripe session id, so they cannot collide across orders or across test databases. Stripe allows keys up to 255 characters (V8). I did not check how long Stripe's session ids can be, so the replacement key uses the 64-character SHA-256 hex digest of the expired id. With the URL fingerprint `-u<f>` (see "Success and cancel URLs"), every key stays under 140 characters, and S7 checks the length.
- **Key pruning** (Stripe keeps keys "at least 24 hours"; sessions expire 24 hours after creation by default):
  - A key is only reused for a session that was created but never recorded.
  - If that retry comes more than 24 hours later and Stripe has pruned the key, Stripe creates a new session. The unrecorded old one was created over 24 hours earlier and has therefore already expired, so there are still never two payable sessions.

##### Request parameters: fixed, and built from saved order data (P15)

Stripe rejects a reused key when the parameters differ (V8). Every value sent must therefore come from data that does not change after the order is created:

| Parameter | Value | Source |
| --- | --- | --- |
| `line_items[0].price_data.unit_amount` | `int(linha.preco_unitario * 100)` | Saved order line (P15: always the saved price) |
| `line_items[0].quantity` | `linha.quantidade` (1) | Saved order line |
| `line_items[0].price_data.product_data.name` | `linha.nome_produto` | **New snapshot column**, written in the order transaction (§3.2) |
| `line_items[0].price_data.product_data.description` | not sent (D20) | Today's live `produto.descricao` could change between attempts |
| `currency`, `mode`, `payment_method_types` | `'eur'`, `'payment'`, `['card']` | Constants |
| `metadata.encomenda_id`, `client_reference_id` | `str(encomenda.id)` | The order |
| `success_url`, `cancel_url` | `settings.STRIPE_SUCCESS_URL`, `settings.STRIPE_CANCEL_URL`, used exactly as configured, with no per-order or per-request parts | Environment; see "Success and cancel URLs" below |

- The total charged is the sum of the saved line amounts, which equals `encomenda.total` for these one-line orders. A test checks that they match.
##### Success and cancel URLs

**The risk** (V8): Stripe compares the parameters when a key is reused. Suppose a first attempt was sent with one pair of URLs and failed before its `Pagamento` was saved. If the retry arrives after a deploy that changed `STRIPE_SUCCESS_URL` or `STRIPE_CANCEL_URL`, Stripe rejects it as an idempotency error, and the retry would keep failing until the key is pruned.

**How the design keeps them stable:**
1. **Part of the key.** The URLs become part of the attempt's identity. Every key ends with `-u<f>`, where `<f>` is the first 12 hex characters of the SHA-256 of `STRIPE_SUCCESS_URL + "\n" + STRIPE_CANCEL_URL`:
   - first attempt: `checkout-encomenda-<id>-<nonce>-u<f>`;
   - replacement: `checkout-encomenda-<id>-substitui-<sha256(expired id)>-u<f>`.
2. **Same URLs, same key:** a retry reuses the key, and Stripe returns the same session.
3. **Changed URLs, new key:** Stripe treats the retry as a new request and creates a new session with the new URLs, instead of rejecting it.
   - The earlier session is the one that was never recorded, so its link was never shown, because the link is only shown after the `Pagamento` save (step 5).
   - Nobody can pay it, and it expires within 24 hours.
   - The customer still has exactly one payable link.
4. **Read the same way every time:** the URLs come straight from settings on each call, with no query strings, order ids or timestamps added.
5. **Nothing changes once a session is recorded:** the bot only retrieves it. A later URL change affects new sessions only.
6. **Still handled:** a `stripe.IdempotencyError` that happens anyway (for example a parameter this list missed) is logged and shows the D18 screen (S15).

**Operational note for README.md, in Change 2:** changing these URLs is safe at any time. Sessions already recorded keep their old URLs until they are paid or expire.

##### `garantir_sessao_pagamento(encomenda)` in `core/services.py`

Returns `(estado, url)`. It never runs while a database transaction is open, and it never moves a payment out of `pago`.

1. **The order must still be payable:** `encomenda.estado == 'pendente'`. Otherwise return `('nao_ativa', None)`, for example when an admin cancelled it or it is already confirmed.
2. **No `Pagamento`: first attempt.**
   - `Session.create(<parameters above>, idempotency_key=<first-attempt key>)`.
   - Then `Pagamento.objects.get_or_create(encomenda=..., defaults={metodo 'stripe', valor=encomenda.total, estado_pagamento 'pendente', stripe_session_id=session.id})`.
   - If a row already existed (a concurrent first attempt), use the row's session from step 3.
3. **`Pagamento` exists:**
   - `pago` → `('paga', None)`. `reembolsado` → `('nao_ativa', None)`.
   - Otherwise `Session.retrieve(stripe_session_id)`:
     - `open` → `('aberta', session.url)`.
     - `complete` → `('a_confirmar', None)`, whatever its `payment_status`. This is the case where the customer paid but the webhook has not yet updated the payment.
       - The bot does **not** create a replacement session.
       - It does not mark anything paid and writes nothing; the webhook stays the only thing that confirms a payment.
       - It logs the session id at info level.
       - A completed session is final in Stripe: it is never turned back into an open or expired one. So every later tap also gets `a_confirmar` until the webhook marks the payment `pago`, after which step 3 returns `paga` without calling Stripe.
       - If the webhook never arrives (an operations problem, deferred with the webhook work), the order stays in `a_confirmar`. It is never charged twice.
     - `expired` → step 4. This is the only status that leads to a new session.
4. **Replacement attempt** (D19):
   - `Session.create(<same parameters>, idempotency_key=<replacement key>)`.
   - Record it with a conditional update:
     ```python
     Pagamento.objects.filter(
         pk=pagamento.pk,
         stripe_session_id=old_id,
         estado_pagamento__in=['pendente', 'falhado'],
     ).update(
         stripe_session_id=new.id,
         estado_pagamento='pendente',
         valor=encomenda.total,
     )
     ```
   - **One row updated:** `('aberta', new.url)`.
   - **No row updated:** another tap or worker replaced it first, or a payment arrived. Re-read the row and go back to step 3. Two simultaneous replacements of the same expired id compute the same key, so Stripe gives both the same new session.
   - `falhado` → `pendente` is allowed only here. It is set by today's expiry webhook (`core/views.py` `_handle_payment_expired`), and a replacement starts a new payable attempt.
5. **Only after the new session id has been saved** does the handler show "💳 Pagar Agora".

**Stock and the webhook on replacement:**
- Stock is still held by the order, because nothing releases it on expiry today (SEC-04). A replacement therefore needs no stock check.
- If SEC-04 later releases stock on expiry, replacement must re-reserve stock first. That is a note for that change.
- A late `checkout.session.expired` event for the old session no longer matches any row once it has been replaced. The current webhook logs "Pagamento não encontrado" and returns 200, which is harmless.
- An expired session cannot be paid, so no payment can arrive for the old id.

**Handler use:**
- After `criada`, or after an existing order is found (with the alert):
  - `aberta` → edit the message to today's success screen with "💳 Pagar Agora". A "message is not modified" error counts as success.
  - `paga` or `nao_ativa` → the alert only, with no edit.
  - `a_confirmar` (the customer paid; the webhook has not confirmed it yet):
    - The alert, then edit the message to: "⏳ O pagamento da encomenda #{id} está a ser confirmado. Não voltes a pagar. Receberás uma mensagem quando a confirmação chegar.". The wording was approved by the owner (D21).
    - The message is plain text with only the "🛒 Ver Encomendas" button, so no "Pagar Agora" button is left to tap. Stripe's completed-session link no longer works anyway.
    - The order stays `pendente`, and the bot creates no session and writes nothing.
    - The existing webhook confirms the order and sends its own message ("✅ Pagamento confirmado!").
    - A "message is not modified" error, on a second tap, counts as success.
  - **Any error** → log it, then show the D18 screen. This covers a Stripe API error, `stripe.IdempotencyError`, a same-key request still running (which Stripe does not save, V8), and a database error while saving.
- **D18 screen:** today's "✅ Encomenda #<id> criada! … ⚠️ Não foi possível gerar o link de pagamento. Contacta o suporte.", with "🔄 Tentar novamente" (the same Confirm callback) and today's "🛒 Ver Encomendas".
  - A retry from it always finds the order, because a used nonce never expires, and runs this function again.

**Tests** (`core/tests.py`, PostgreSQL; `stripe.checkout.Session.create` and `retrieve` mocked, recording their keyword arguments):

| # | Case | Expect |
| --- | --- | --- |
| S1 | First attempt, no `Pagamento` | `create` called once with the first-attempt key; one `Pagamento` with that session id; the URL returned only after the save |
| S2 | First attempt: `create` succeeds, the `Pagamento` save fails once, then a retry | Same key on both calls, identical parameters; exactly one `Pagamento`, with the session id Stripe returned |
| S3 | Stored session `open` | `retrieve` only; no `create`; the URL returned |
| S4 | Stored session `complete` while the `Pagamento` is still `pendente` (webhook not yet run), with `payment_status` `paid`, and again with `unpaid` | `a_confirmar`; `create` never called; the `Pagamento` row byte-for-byte unchanged; a second call gives the same result |
| S5 | `Pagamento` `pago` or `reembolsado`, or the order not `pendente` | `paga` or `nao_ativa`; Stripe not called |
| S6 | Stripe raises on the first attempt, then a retry | No `Pagamento` after the failure; the retry uses the same key |
| S7 | Keys | First-attempt key identical across calls for one order; different between two orders; every key at most 255 characters |
| S8 | Product price, name and description changed after the order | `create` arguments identical to before the change: amount from `preco_unitario`, name from `nome_produto`, no description |
| S9 | Replacement: stored session `expired`, order `pendente` | `create` with `…-substitui-<sha256(old id)>`; the row now holds the new id with `pendente`; the URL returned |
| S10 | Replacement: `create` succeeds, the conditional update fails once, then a retry | The retry uses the same replacement key (the old id is still stored); exactly one replacement session recorded |
| S11 | The replacement session also expires later | The next key is `…-substitui-<sha256(first replacement id)>`, different from the S9 key |
| S12 | `Pagamento` `falhado` (expiry webhook ran) with an expired session | Replaced; the row goes back to `pendente` |
| S13 | Two replacements race: the conditional update affects 0 rows because the id was already replaced | The function re-reads and returns the stored new session; no second update |
| S14 | Parameters of a first attempt vs its replacement | Identical except the key; both match the saved line |
| S15 | `create` raises `stripe.IdempotencyError` | D18 screen state; the error logged; `Pagamento` unchanged |
| S16 | Line amount total equals `encomenda.total` | Equal for orders created by `reservar_encomenda` |
| S17 | Same URLs across a failed first attempt and its retry | The same key, ending `-u<f>`; identical `success_url` and `cancel_url` arguments |
| S18 | URLs changed (with `override_settings`) between a failed first attempt and its retry | A different key; `create` called with the new URLs; one `Pagamento`, recording the second session; the first session id is stored nowhere |
| S19 | URLs changed between a failed replacement and its retry | A different replacement key; the row ends with the new session; the expired id is replaced once |
| S20 | URLs changed after a session was recorded and is `open` | `retrieve` only; no `create`; the recorded session's URL is returned |

**Handler tests** (mock-only, in `telegram_bot/tests.py`):
- `aberta` edits to the payment screen, and "message is not modified" is treated as success.
- `paga` and `nao_ativa` show the alert only.
- **H14** `a_confirmar`: exactly one answer (the D8 alert). The message is edited to the D21 text with the order id and only the "Ver Encomendas" button, and no URL button. `Session.create` is not called, and no service writes. A second tap gets the same result, and "message is not modified" is treated as success.
- Every error shows the D18 screen with "Tentar novamente" carrying the identical callback.
- A retry from the D18 screen routes to the existing-order path.

**Still out of scope:** the webhook fallback for a missing `Pagamento`, the locked webhook transition, duplicate webhook delivery (SEC-05), stock release on expiry (SEC-04), and refunds.

#### 3.10 Review: the design checked against the PostgreSQL test plan

I mapped every rule in §3.1–§3.9 to the test that would fail if the rule were broken. Rules with no such test are listed as gaps.

| Rule | Where | Tests |
| --- | --- | --- |
| Callback exactly 64 bytes at the max id; parser strict; legacy and malformed rejected | §3.1 | H1, H2, H3 |
| One order per nonce, sequential repeat | §3.3 step 3 | DB1, DB9, E1 |
| Unique-index path when the lookup misses (forged or racing nonce) | §3.3 step 5 | DB2, DB8 |
| A nonce belonging to another user never reveals or creates anything | §3.3 | DB3, H5 |
| Different screens create different orders | §3.1, §3.3 | DB4, H10 |
| Refuse on stock 0 (D6) | §3.3 step 4 | DB5, H8 |
| Product row lock protects stock under concurrency | §3.3 step 2 | T1 (required), T2 |
| All or nothing, audit included (D5) | §3.3 | DB6, DB10 |
| Checkout does not touch the image file (B3) | §3.3 step 7 | DB7 |
| Product-name snapshot (P15) | §3.2, §3.3 step 6 | DB11, S8 |
| Expiry only for a nonce with no order; never blocks recovery | §3.4 | H6, H13, E2 |
| Failure screens and their buttons (D7, D13, D18) | §3.5 | H9, handler tests in §3.9 |
| First-attempt key stable; session saved before the link | §3.9 | S1, S2, S6, S7, S17 |
| Completed session: no replacement, no write; "don't pay again" screen | §3.9 step 3 | S4, H14 |
| Replacement only on `expired`, new key each time, stable across its retries | §3.9 step 4 | S9, S10, S11, S12, S14 |
| Concurrent replacement resolved by the conditional update | §3.9 step 4 | S13 |
| Changed URLs give a new key instead of an idempotency error | §3.9 | S18, S19, S20, S15 |
| Request built only from saved data | §3.9 | S8, S14, S16 |
| Only pending orders get sessions | §3.9 step 1 | S5 |
| Migration additive and reversible | §3.2 | DB8, plus the manual migration check |

**Tests this review adds:**
- **S21: concurrent first attempt, made deterministic.**
  - Create the `Pagamento` directly, then patch `get_or_create`'s initial lookup to miss once.
  - Expect the `OneToOne` `IntegrityError` to be caught and the existing row's session to be used. No second row, and no session id overwritten.
  - Without this test, §3.9 step 2's race handling had no coverage.
- **S22: the unchanged webhook finds a replaced session.**
  - After S9's replacement, call `core.views._handle_payment_success({"id": <new id>})` with `httpx.post` patched. The order becomes `confirmada` and the payment `pago`.
  - The same call with the old id changes nothing and logs "Pagamento não encontrado".
  - The webhook code is not changed, but its behavior is part of D19's safety argument.
- **E1: end to end on PostgreSQL** (async test in `core/tests.py`).
  - Call the real `confirmar_encomenda` twice with the same callback, through `sync_to_async`, with Stripe and Telegram mocked.
  - Expect one order and one `Pagamento`. The second call gives the D8 alert and makes no `create` call (the `retrieve` mock returns `open`).
  - This is the only test that joins the handler, the services and PostgreSQL. Without it, the mock handler tests and the database tests never meet.
- **E2:** like E1, but the second call comes 45 minutes later, the first attempt's Stripe call failed, and so there is no `Pagamento`.
  - Expect the alert, then a first-attempt `create` with the stable key, and the payment screen.
  - Expect no stale screen and no second order.

**Gaps that remain, needing the owner's view:**
- **G1, now required test T1 (owner decision):** two concurrent confirmations with one unit in stock.
  - The unique index cannot show that stock is protected; only the product row lock does that. T1 is therefore an acceptance test for Change 2.
  - **Setup:** a Django `TransactionTestCase` on PostgreSQL (real commits, no wrapping transaction). One product with `stock_atual = 1`, two users, two different nonces.
  - **Two OS threads** (`threading.Thread`), named A and B, each calling `reservar_encomenda` with its own nonce. Each thread closes its database connection with `connection.close()` when it finishes.
  - **Making the overlap certain:** "B was started" is not enough; B might not have run yet. The test checks in PostgreSQL itself that B is waiting on A.
    1. Each thread first records its database backend id with `SELECT pg_backend_pid()` on its own connection, and puts it in a shared dict.
    2. **Thread A** runs `reservar_encomenda` with the step-3 lookup patched for thread A only: when A reaches step 3, it already holds the product row lock from step 2. It sets the event `a_tem_bloqueio`, then waits on `libertar_a`, for at most 10 s.
    3. The main thread waits for `a_tem_bloqueio` (5 s timeout), then starts **thread B**, which runs `reservar_encomenda` unpatched.
    4. **Proof that B reached the lock and is blocked by A:**
       - The main thread, on its own connection, polls `SELECT pg_blocking_pids(<B pid>)` every 50 ms for up to 5 s, until the result contains A's pid.
       - `pg_blocking_pids` returns the sessions that block a given session from acquiring a lock. A's pid in that list means B is inside its `SELECT … FOR UPDATE` and waiting for A's row lock, not merely scheduled.
       - If A's pid never appears, the test fails with "B nunca ficou bloqueado por A". That is exactly what would happen if the `FOR UPDATE` were missing.
       - Steps 1 to 3 cannot block B, because each thread uses a different user and nonce, so the product row is the first thing B can wait on.
    5. While B is blocked, the test asserts that B's result slot is still empty, and that `pg_blocking_pids(<B pid>)` still lists A just before A is released.
    6. The main thread sets `libertar_a`. A finishes and commits, and both threads are joined with a 10 s timeout each. A test fails if a thread is still alive.
    7. **Ordering:** A records the time its transaction committed, and B records the time it got past step 2 (via a step-3 patch in thread B that only notes the time). The test asserts that B passed step 2 after A committed.
  - **Permissions** (checked against the PostgreSQL 17 docs on 2026-11-22):
    - The `pg_blocking_pids(integer)` row in Table 9.69 ("Session Information Functions") states no execution restriction.
    - The "restricted to superusers and roles with privileges of the pg_monitor role by default" sentence on that page belongs to `pg_current_logfile`.
    - §5.8 ("Privileges") lists `EXECUTE` on functions as a default `PUBLIC` privilege.
    - So no `GRANT` and no `pg_monitor` membership is planned.
  - **Preflight in T1's `setUpClass`:**
    - `SELECT has_function_privilege('pg_catalog.pg_blocking_pids(integer)', 'EXECUTE')`. If it is false, the test fails (it does not skip) with "telegram_bot_test não pode executar pg_blocking_pids".
    - A second query checks that the role can read `pg_locks`, which is the fallback below.
  - **If the preflight fails on this installation:**
    - **The grant has to be made in the test database.** Function privileges live in each database's own catalog (from PostgreSQL's design; I did not confirm this on these pages). Django creates a fresh `test_telegram_bot` from `template1` on every run, so a `GRANT` made in `telegram_bot` would not reach it. The narrow fix is `GRANT EXECUTE ON FUNCTION pg_catalog.pg_blocking_pids(integer) TO telegram_bot_test`, run by the admin in `template1`. That affects every database created from it afterwards. It is the owner's call, done locally.
    - **Fallback without any grant:** check `pg_locks` instead. A session waiting for a row lock shows as waiting for the holder's permanent transaction id, and `granted = false` means it is waiting (PostgreSQL 17 §52.12). The check becomes:
      - a `transactionid` lock row for B's pid with `granted = false`;
      - whose `transactionid` equals A's, recorded by A with `pg_current_xact_id()` after its first write.

      The docs I read do not say whether `pg_locks` is readable by ordinary roles, which is why the preflight checks it.
  - **Expected:**
    - B was blocked by A (step 4);
    - the results are exactly one `criada` and one `sem_stock`;
    - one order, one order line and one stock movement;
    - stock ends at 0, never negative;
    - B got past the lock only after A had committed (step 7).
  - **What it catches:** if the `select_for_update()` were removed, B would never appear blocked, so the test fails at step 4. Even without that check, B would read stock 1 while A is paused, giving two `criada` results.
  - **T2 (same thread setup and the same blocking proof):** two threads with the *same* nonce and product. Expect B blocked by A, then one `criada`, one `existente` and one order.
  - **Cleanup:** `TransactionTestCase` empties the tables after each test. T1 and T2 sit in their own test class, so the faster `TestCase` tests are unaffected.
- **G2: an uncertain COMMIT cannot be reproduced in a test.**
  - A connection lost during COMMIT can't be triggered from Django's test runner. §3.6 argues it from PostgreSQL's all-or-nothing commit, and DB9 (committed) and DB10 (rolled back) test the two possible outcomes.
  - No further test is proposed.

**Notes for writing the PostgreSQL tests:**
- **Django's `TestCase` runs each test in one transaction.**
  - Nested `atomic()` blocks become savepoints, so DB2's rollback-to-savepoint path works as designed.
  - `select_for_update()` is allowed, because the test is inside a transaction.
- **DB7:** set the missing image with `Produto.objects.filter(...).update(imagem="produtos/em-falta.jpg")`. `Produto.save()` itself would try to resize the image.
- **E1 and E2** need async `TestCase` methods. Django runs `sync_to_async` database calls in the test's thread, so they share the test transaction.
- **D10 acceptance:** `python manage.py test core telegram_bot` must pass on PostgreSQL with the §3.8 role before Change 2 is accepted. Mock tests alone do not count.

#### 3.11 Implementation notes and review fixes

**Review fix P1, cancellation during session preparation:**
- **The fix:** `garantir_sessao_pagamento` re-checks the order after the Stripe call and before saving anything. It locks the order row (`SELECT … FOR UPDATE`) and confirms that the order is still `pendente`, and only then saves the first session or the replacement. If the order is no longer pending, it returns `nao_ativa` and saves nothing, so the unrecorded session's link is never shown.
- **Before returning a retrieved open session,** it re-reads the order state.
- **Why this works with today's cancellations:** both of them (Telegram admin `save()` and Django admin `update()`) write that row, so they either commit before the check or wait until the save commits.
- **Tests:**
  - S23–S25 cancel the order during each Stripe call.
  - T3 holds a cancellation open in another thread, proves in PostgreSQL that the bot waits on it (`pg_blocking_pids`), and checks that the bot then saves nothing.
- **Mutation check:** with the re-check disabled, S23 and T3 fail.
- **Remaining, open for the owner's decision:**
  - **Delivery gap:** the bot releases the order lock once the session is saved, then reads the order line and sends the payment screen. An admin cancellation that commits in that gap (milliseconds, plus Telegram's send time) does not stop the link from reaching the customer.
  - Closing it would need cancellation itself to handle the payment session: expire it and stop or refund the payment. That is SEC-06 work, because the lock cannot be held across the Telegram call.
  - An order cancelled after its link was shown can still be paid, and the unchanged webhook confirms it (SEC-06).
- If cancellation happens right after the order is created, the customer's Confirm screen stays as it is. A later tap gets the "já foi registada" alert. No new message text was added for this case.

**Review fix P2, the losing tap of a concurrent race:**
- **The change:** `reservar_encomenda` returning `existente` after the answer now runs payment recovery instead of doing nothing.
- **Why it is safe:** the losing tap uses the same stable key, so no second session is created, and the winning process may have stopped before creating a session.
- **If both taps reach Stripe at once,** one may get Stripe's same-key conflict and show the payment-link failure screen, whose "Tentar novamente" recovers the session.
- **Test:** H7 now checks that recovery runs and the payment screen is drawn.

**T1 ordering check:** the test asserts that B passed the product lock after A was released, rather than after A's recorded commit time. Together with the `pg_blocking_pids` proof, this shows that B waited for A's lock.

### 4. Deferred until after the focused design is reviewed

| Item | Why it waits | Report refs |
| --- | --- | --- |
| Webhook fallback: link a paid session to its order through `metadata.encomenda_id` when `Pagamento` is missing | Payment recovery, which the owner deferred. `metadata.encomenda_id` is already sent today, so sessions created before then stay recoverable later. | M5, P2, P11, P12 |
| Locked webhook transition and notifying after commit (duplicate and concurrent delivery) | Payment recovery; part of SEC-05 | M6, P13 |
| Webhook fallback when `Pagamento` is missing | Not needed while the bot saves the `Pagamento` before showing the link (§3.9); kept for later as defense in depth | M5, P2 |
| Further threaded PostgreSQL tests beyond T1–T3 (for example concurrent webhook delivery) | T1 and T2 are required (G1), and T3 was added for review fix P1; broader concurrency testing stays deferred | M14 |
| Unique `Pagamento.stripe_session_id`, a router-wide error message, SEC-04 expiry, SEC-12 | Out of scope for both changes | P20, P21 |

## Goals vs. Non-Goals

* **In-Scope Goals:**
  * Change 1: a text product screen with working buttons when a photo is missing or fails to upload, with the old message deleted only after a photo was sent.
  * Change 2: at most one order per confirmation screen. This covers queued, repeated, restarted and multi-worker taps, enforced by a unique token column and all-or-nothing order creation.
  * Change 2: a repeat tap never creates an order and never overwrites the payment screen.
  * Change 2: checkout no longer depends on the product image file (B3).
  * Change 2: refuse checkout on insufficient stock (D6), write the audit row inside the order transaction (D5), expire unused tokens 30 minutes after the screen time carried in the token (D4/D4a), and prove the unique token with a PostgreSQL test (D10).
  * Change 2: a retry or repeat tap recovers or creates the order's Stripe Checkout Session, and the session is persisted before the link is shown (D17).
  * Change 2: a stable key for every retry of one session attempt, a new key for each replacement of an expired session (D19), and request parameters from saved order data only (P15).
  * Doc updates in each change: SECURITY.md, with SEC-13's "keyed on user and product" advice corrected and SEC-03 split; README and ARCHITECTURE for the new flow; PROJECT_GUIDANCE.md for the owner's decisions.
* **Out-of-Scope Non-Goals:**
  * Webhook changes (the metadata fallback, locked transitions, duplicate delivery), stock release on expiry (SEC-04), and refunds (§4).
  * Threaded tests beyond T1, T2 and T3 (§4).
  * Turning on concurrent update handling, a notification outbox, new order states, refunds and cancellation rules (SEC-06), stock release on expiry (SEC-04), and the SEC-12 order-detail bug.

## Alternatives Weighed

Reasons are from the Goldfish review and the checks in §1; none was rejected by the owner. The full list is in report Section 7.

* **Disable the button by editing the message first.** It cannot stop a tap already queued (V1); it is feedback only.
* **In-memory "in flight" guard or "recently completed" cache.** It is always empty under one-at-a-time processing (V1, V2), lost on restart, and not shared between workers.
* **Tighter rate limit or cooldown.** A heuristic held in memory that blocks real purchases.
* **Key on the Telegram message, `callback_query.id` or `edit_date`.** The bot reuses one message for many screens; `callback_query.id` is unique per tap; `edit_date` has one-second precision and is missing on unedited messages.
* **One pending order per user and product.** It changes a business rule and would block a second purchase while an abandoned order is pending.
* **Row or advisory locks without a token.** They serialize taps but cannot tell a repeat from a wanted second order.
* **A table of issued tokens written when the screen is drawn.** It costs a write per Encomendar tap plus cleanup; the column on `Encomenda` gives the same guarantee.
* **Signed (HMAC) tokens.** Not needed, because the lookup includes the user.
* **PTB `arbitrary_callback_data`.** It is held in memory, so it does not work across restarts or workers.
* **Stripe idempotency key alone.** It stops duplicate Stripe Checkout Sessions, not duplicate orders or stock changes (V7, V8).
* **Product-id-first callback (`…_<produto_id>_<chave>`).** Rejected in favor of token-first, so rolled-back code still reads a product id from the last segment.


## Key Decisions & Staging for Next Steps

### Decided by the owner

* **Stages:** Change 1 and Change 2 are separate. Webhook redesign and PostgreSQL concurrency infrastructure are deferred.
* **Migrations and guards:** small, additive migrations are acceptable. In-memory guards are feedback only.
* **Change 1:** D1, D2 and D11. Committed as `98f8668` on `refactor/split-bot-py`, after the refactor (`76a4ce7`).
* **Change 2:** D3–D20 and P15, as tabled at the top of §3. D20: the Stripe line item has no description.
* **Owner rule:** expiry never blocks payment recovery for an existing order (§3.4).
* **Owner rule:** a completed but unrecorded session never triggers a replacement (§3.9, step 3).
* **Owner rule:** success and cancel URLs are handled safely across retries (§3.9).
* **G1:** required threaded PostgreSQL test T1 (stock 1, two tokens, one order and one "sem stock"), with T2 alongside it (§3.10).
* **D21:** approved wording for the "being confirmed, don't pay again" screen.
* **Completed session awaiting the webhook:** a clear "being confirmed, don't pay again" screen. The order stays pending, no replacement session is made, and the webhook updates it (§3.9).
* **Commits:** this brief and the Goldfish report stay out of the commits until the Change 2 design is settled.

### Status and next step

- **Change 2:** implemented on `codex/checkout-change-2`, not committed.
  - `python manage.py test core telegram_bot` passes on PostgreSQL: 73 tests, 1 expected failure (SEC-12), including T1–T3.
  - The PostgreSQL test role `telegram_bot_test` and the git-ignored `.env` are set up. The password was entered locally only.
- **Decided (Option 1):** the delivery-gap cancellation window (§3.11) is accepted as deferred to SEC-06, together with session expiry on cancellation, refunds, and webhook handling of payments for cancelled orders. In the gap, the bot releases the order lock once the session is saved, then reads the order line and sends the payment screen. An admin cancellation that commits in that gap (milliseconds, plus Telegram's send time) does not stop the link from reaching the customer.
- **After that:** commit Change 2 on its branch. This brief and the Goldfish report stay uncommitted unless the owner decides otherwise.
