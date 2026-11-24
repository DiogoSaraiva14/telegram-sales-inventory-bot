# Security and code-review record

Status: started as a static review of the imported source snapshot. Since then, automated tests have been added for the fixed findings (SEC-02, SEC-03, SEC-13). They run on PostgreSQL with Telegram and Stripe mocked; see README.md. No runtime exploit checks, dependency audit, or deployment review have been run. Priorities below are engineering urgency, not CVSS scores or claims of demonstrated exploitation.

**Finding status (2026-11-24):**
- **Fixed:** SEC-01, SEC-02 (bot checkout), SEC-04 (expired sessions), SEC-05, SEC-06, SEC-07, SEC-09, SEC-10, SEC-12 and SEC-13.
- **Mostly fixed:**
  - SEC-03: orders that never got a Stripe session hold stock until an admin cancels them.
  - SEC-08: log retention and access are not defined yet.
- **Partly addressed:** SEC-11. Checkout no longer re-saves products, but other saves still re-encode images.
- **Open:** none of SEC-01 to SEC-13.

## Trust boundaries

- Telegram users submit commands, callback values, names, and interaction timing.
- Telegram administrators are selected from ADMIN_TELEGRAM_IDS.
- Django admin is protected by Django authentication and staff permissions.
- Stripe sends external events to a CSRF-exempt webhook; the handler verifies Stripe's signature before dispatch.
- PostgreSQL stores Telegram identifiers, names, orders, payments, stock, and audit records.
- Secrets are expected through environment variables; .env is ignored by the source .gitignore.

## Positive controls observed

- core.views.stripe_webhook verifies the raw request body against STRIPE_WEBHOOK_SECRET before processing an event.
- ORM calls use Django querysets rather than constructing raw SQL.
- Telegram admin callback branches generally check is_admin.
- Dynamic Django admin badge values use format_html; the literal mark_safe badge values are constants.
- No hard-coded Telegram or Stripe credential values were found in the inspected source.
- The GitHub repository is private; bundled product images and Python bytecode were not imported.

## Findings

### SEC-01 — Unsafe Django configuration defaults (High priority)

**Status: fixed (2026-11-24).**
- `SECRET_KEY` and `DB_NAME` are required: settings refuse to load without them.
- `DEBUG` now defaults to False and is on only when set to `True`.
- `ALLOWED_HOSTS` comes from a comma-separated `ALLOWED_HOSTS` variable and defaults to empty. With `DEBUG` off and no hosts, Django serves nothing: `runserver` refuses to start, and a WSGI server answers every request with 400. That is a safe failure, not an open one.
- Still open, with the Phase 4 deployment work: HTTPS, secure cookies, HSTS and `CSRF_TRUSTED_ORIGINS`.

Originally: config/settings.py lines 14–20 permitted SECRET_KEY to be absent, defaults DEBUG to True, and accepts every host with ALLOWED_HOSTS = ['*']. A deployment that does not override these values starts with unsafe settings or fails later in confusing ways. Require explicit environment-specific values and fail startup when production settings are incomplete.

### SEC-02 — Order and stock writes are not atomic (High priority)

**Status: fixed for bot checkout.**
- `core/services.py` `reservar_encomenda` writes the order, line, stock decrement, stock movement and audit row in one transaction.
- It locks the product row (`SELECT … FOR UPDATE`) and refuses the sale when stock is below 1.
- The PostgreSQL tests T1 and T2 (`core/tests.py`) show two concurrent checkouts waiting on that lock.
- Admin cancellation (SEC-06), status steps and stock edits (SEC-10) also run as transactions in `core/services.py`, under the same row locks.

Originally: order creation used separate writes, and stock was read early and clamped with max(0, ...).

### SEC-03 — Checkout failures can strand pending orders and stock (High priority)

**Status: mostly fixed.**
- **Order-creation failures:** they now roll back completely and show "❌ Não foi possível registar a encomenda. Tenta novamente.", with a retry button that carries the same confirmation token.
- **Stripe failures:** they show the payment-link failure screen with a retry button.
- **Recovery:** a retry recovers or creates the order's Stripe Checkout Session.
  - The first attempt uses one stable idempotency key, and each replacement of an expired session uses a new key.
  - The success and cancel URLs are part of the key.
  - The `Pagamento` row is saved before the link is shown.
  - A completed session waiting for the webhook gets a "don't pay again" screen and is never replaced.
- **Missing product image:** checkout no longer fails, because stock is decremented with an `update()` and never calls `Produto.save()`.
- **Still open:**
  - an order whose Stripe session was never created (every attempt failed and the customer gave up) still holds its stock; no sweep releases it (see SEC-04);
  - a rendering error after a successful payment-session save leaves no reply, but a retry recovers it;
  - **cancellations that commit before the session is saved** are handled: the bot locks the order row and re-checks it, so no link is shown;
  - **delivery gap (closed by SEC-06):** a link that reaches the customer after an admin cancellation is already expired, because cancelling expires the stored session first;
  - **an order cancelled after its payment link was shown** cannot be paid any more for the same reason, and a payment that still arrives for a cancelled order is refunded in full (SEC-06).

Product detail (telegram_bot/handlers/customer.py `produto_detalhe`) is fixed: it sends the photo before deleting the old message, and on a missing file, upload error or timeout it edits the old message into the text screen with "(Imagem indisponível)" and the same buttons.

### SEC-04 — Expired Checkout does not resolve order or inventory (High priority)

**Status: fixed for expired sessions (owner decision, 2026-11-24).**
- A `checkout.session.expired` event for the session stored on the order now, in one transaction under the order row lock (`core/services.py` `cancelar_por_expiracao`), marks the payment `falhado`, cancels the order, returns each line's quantity to stock with an `F()` update, and writes a stock movement and audit rows.
- After the commit, the customer gets the existing "cancelada" Telegram message.
- Events for a replaced session id, a payment that is no longer `pendente`, or an order that is no longer `pendente` change no stock; a duplicate event therefore changes nothing.
- A retry of a cancelled order gets no new session. Replacing an expired session (D19) now happens only if the customer retries before the expiry webhook arrives.
- **Still open:** an order with no Stripe session at all holds its stock until an admin cancels it; releasing that needs a scheduled sweep. The reused message ends with "Se não solicitaste este cancelamento, contacta o suporte.", which may read oddly for an expiry.

### SEC-05 — Webhook idempotency is not atomic (High priority)

core/views.py lines 45–79 checks payment state, then performs separate payment, order, audit, and notification effects. Concurrent duplicate Stripe deliveries can pass the state check before either update commits. Persist provider event IDs or use a row-locked transaction and an outbox/retry mechanism for notifications.

**Status: fixed (2026-11-24).**
- `checkout.session.completed` now runs `core/services.py` `confirmar_pagamento`: one transaction that locks the order row and then the payment row (the same order as the expiry and checkout code), re-checks the stored session id and payment state, and only then writes the payment, order and audit rows.
- A concurrent duplicate delivery waits for the lock, then sees `pago` and changes nothing. The Telegram message is sent after the commit, only by the delivery that confirmed the payment, so the customer gets at most one.
- `checkout.session.expired` uses the same pattern (SEC-04).
- **Not done:** no event-id table and no notification outbox. A failed Telegram send is logged and not retried, as before. A payment for a cancelled order is refunded in full (SEC-06).

### SEC-06 — Cancellation behavior diverges across admin surfaces (High priority)

The Telegram admin flow permits cancelling pending or confirmed orders and restores stock (core/services.py `cancelar_encomenda_db`, called from telegram_bot/handlers/admin.py `_mudar_estado_e_notificar`), but does not update/refund the related Pagamento. Django bulk cancellation only updates Encomenda.estado (core/admin.py lines 110–113), without stock movement, payment state, notification, or audit. Route every cancellation through the same domain operation and define refund handling.

**Status: fixed (owner decisions, 2026-11-24).**
- Both admin surfaces call `core/services.py` `cancelar_encomenda`: the Telegram admin through `cancelar_encomenda_db`, and the Django admin bulk action once per selected order. Only `pendente` and `confirmada` orders can be cancelled.
- In one transaction that holds the order row lock across the Stripe calls, it settles the payment first, then returns the stock with `F()` updates and writes stock movement and audit rows:
  - an open Checkout Session is expired;
  - a completed session that is not yet confirmed refuses the cancellation (`PagamentoRecebido`), and the admin is told the payment just arrived;
  - a paid order is refunded in full, with an idempotency key per order and session, and the payment becomes `reembolsado`;
  - a Stripe error changes nothing, and the admin is told so.
- The customer gets the cancellation message, or a new refund message for a paid order.
- A `checkout.session.completed` event for an order that is already cancelled refunds the payment in full, keeps the order cancelled and tells the customer. A refund error answers 500, so Stripe redelivers.
- **Not covered:** a `pago` payment made outside Stripe cannot be refunded here, so such an order is not cancelled. Refunds are full only. Refund status updates from Stripe (`charge.refunded`, failed refunds) are not handled.

### SEC-07 — Markdown rendering uses dynamic values without escaping (Medium priority)

The Telegram /start message formats the stored user name as Markdown (telegram_bot/handlers/commands.py `start`). sanitizar_nome_utilizador removes selected characters but does not escape Telegram Markdown metacharacters (security/sanitization.py lines 87–92). Product names/descriptions are also inserted into Markdown messages. Use context-specific escaping or avoid parse mode for dynamic content.

**Status: fixed (2026-11-24).**
- `telegram_bot/ui.py` `md()` escapes dynamic text for legacy Markdown, following the Bot API rules:
  - outside an entity, it escapes `_`, `*`, `[` and the backtick with `python-telegram-bot`'s `escape_markdown(version=1)`;
  - inside a `*bold*` or `_italic_` entity, where escaping is not allowed, it closes the entity, escapes the delimiter and reopens it, as in the Bot API's `*2*\**2=4*` example. Entities do not nest, so other characters are literal there.
- It is applied to every user name, product name, description and category, and to audit fields, in Markdown messages: `/start`, product and confirmation screens, order details, payment screen, admin product, stock, order, user and audit screens.
- Values that are not affected: ids, prices, stock counts, order states and fixed category labels.
- **Not verified against live Telegram:** a value that starts or ends with the entity's delimiter leaves an empty `**` pair, which the documentation does not cover. Messages without parse mode, and button labels, need no escaping.

### SEC-08 — Personal identifiers are written to logs (Medium priority)

security/decorators.py lines 65–72 logs Telegram user IDs and usernames. telegram_bot/ (guards.py, notifications.py) and core/views.py also log Telegram identifiers during notifications and administrative actions. Minimize or pseudonymize identifiers and define log retention/access.

**Status: mostly fixed (2026-11-24).**
- User-level log lines now carry `utilizador=<label>`: rate limit, unauthorized admin access, actions, expired sessions, blocked users and invalid callbacks.
  - The label is `security/sanitization.py` `pseudonimo()`, the first 12 hex characters of an HMAC-SHA256 of the Telegram id keyed with `SECRET_KEY`.
  - It is stable, so one user's lines can be correlated, but it cannot be reversed without the key. A plain hash would not hide short numeric ids.
  - Rotating `SECRET_KEY` changes every label.
- Usernames are no longer logged. Notification logs keep only the order id, which leads to the customer in the database.
- The bot sets the `httpx` logger to WARNING, because at INFO it logs every request URL, and Telegram URLs contain the bot token.
- The webhook logs only the exception type when a Telegram send fails, for the same reason.
- **Still open:**
  - log retention and access, which depend on where the project is deployed;
  - audit rows in the database (`RegistoAuditoria`), which still hold customer names and Telegram ids in their descriptions and are visible to admins.

### SEC-09 — Rate limiting and session state are process-local (Medium priority)

security/decorators.py lines 13–43 and telegram_bot/guards.py store rate and session state in module-level dictionaries. Limits reset on restart and are independent across processes; inactive keys are not periodically evicted. Document single-process assumptions or use bounded shared state if deployment scales out.

**Status: fixed for the polling bot (2026-11-24).**
- **Single process by design:** the bot uses long polling, and Telegram refuses a second `getUpdates` consumer for the same token. So the bot always runs as exactly one process, and the in-memory state is not split across processes. This is now stated next to both dictionaries.
- **Bounded memory:** both dictionaries drop idle users at most once a minute.
  - The rate limiter drops users with no request inside its 10-second window.
  - The session store drops sessions older than 30 minutes. They already counted as expired, so behavior does not change.
- **Unchanged:** a restart still resets both. Rate limits start fresh, and every user's first button press after a restart gets "Sessão expirada" until they send /start.
- **Would need shared state:** switching the bot to webhook mode with several workers would need shared storage (for example the database), which is not implemented.

### SEC-10 — Admin order editing can bypass domain invariants (Medium priority)

core/admin.py lines 70–84 exposes editable order-line fields while Encomenda.total is readonly. LinhaEncomenda.save recalculates its subtotal (core/models.py lines 112–132), but does not recalculate the order total or stock/payment state. Django bulk status actions also bypass notification/audit/domain operations. Make order records read-only or use shared validated domain actions.

**Status: fixed (owner decisions, 2026-11-24).**
- **Read-only records:** in the Django admin, orders, order lines and payments are view-only. They cannot be added, edited or deleted, except the internal `notas` field of an order.
- **Status changes:** both admin surfaces use `avancar_estado_encomenda`.
  - Changes go forward only: `confirmada` → `enviada` → `entregue`. Each step locks the order row, checks the current state, writes an audit row, and then messages the customer.
  - There is no manual confirm. Only the Stripe webhook confirms an order: the Telegram "Confirmar" button, its callback prefix and the Django confirm action are removed.
  - A cancelled or delivered order cannot be moved, and a stale button is refused.
- **Stock:** `ajustar_stock` adds a difference under the product row lock, never below zero, and records the amount actually applied as a stock movement.
  - The Telegram stock buttons use it.
  - The Django product form turns a stock edit into the same difference (new value minus the value shown in the form), with an audit row. Its other fields are saved without writing `stock_atual`.
  - The Telegram product toggle now writes only `ativo`.
  - Before this change, these paths could overwrite a checkout that happened at the same moment.
- **Not covered:**
  - creating a product with initial stock writes no stock movement;
  - stock movements and audit rows can still be edited or deleted in the Django admin;
  - Django product saves still re-encode images (SEC-11).

### SEC-11 — Product images are reprocessed on ordinary model saves (Medium priority)

core/models.py lines 48–78 resizes and re-encodes the stored image whenever Produto.save() runs, including stock or active-state updates. Repeated saves can degrade image quality and rely on local filesystem paths. Process only newly uploaded images and use the configured storage interface.

### SEC-12 — Input validation allows known callback prefixes, but route names have drifted (Medium priority)

**Status: fixed.** The order list now emits `ver_encomenda_<id>`, a prefix already on the allowlist, routed to `encomenda_detalhe`, which still checks that the order belongs to the tapping user. Tests: `OrderDetailTests` in telegram_bot/tests.py.

Originally: telegram_bot/keyboards.py `teclado_minhas_encomendas` emits user_encomenda_<id> and telegram_bot/handlers/customer.py `encomenda_detalhe` handles it. security/sanitization.py lines 22–39 does not allow that prefix; it allows ver_encomenda_ instead. The validator at lines 43–54 rejects the generated order-detail callback, so users cannot open order details from their order list.

### SEC-13 — Repeated checkout confirmation can create duplicate orders (High priority)

**Status: fixed.**
- Each Confirm screen carries its own token: `confirmar_encomenda_<nonce>_<ts>_<produto_id>`, exactly 64 bytes at the largest id.
- The nonce is stored on the order under a unique constraint (migration 0008), so one confirmation screen creates at most one order. This holds for repeated, queued, concurrent and post-restart taps.
- **Repeat taps:** a repeat tap finds the existing order first and gets "Esta encomenda já foi registada." plus payment recovery, whatever the button's age.
- **Expiry and old buttons:**
  - Unused tokens expire 30 minutes after the screen was drawn. The age is measured when the bot processes the tap, so a tap queued during a long outage can be rejected.
  - Buttons without a token, from before the change, are treated as expired.
- **What did not work:** an in-memory guard or disabling the button would not have worked, because python-telegram-bot 22.7 processes updates one at a time by default, so a second tap waits and then runs again.
- The original recommendation, a guard keyed on user and product, was wrong: it would also block a real second purchase.

## Additional review notes

- The imported snapshot had no automated tests or CI. Tests now exist (telegram_bot/tests.py, core/tests.py); CI is still absent.
- Stripe expiry, refund, asynchronous-payment, and retry semantics are not documented.
- The source stores Telegram identifiers and names. The phone field exists in the model; no population path was observed in the reviewed bot code.
- The project has no documented data retention, privacy notice, upload limits, or deployment threat model.
- .gitignore excludes .env and media, but the source snapshot included committed product images. These were omitted from GitHub; verify ownership and licensing before adding replacements.
- The admin_only decorator in security/decorators.py is defined but not used by the bot; the Telegram admin check runs once per callback in telegram_bot/handlers/callbacks.py and in the /admin command.

## Review limitations

The observations are based on source inspection and the imported archive. They have not been confirmed by running the application, simulating webhook events, testing concurrency, auditing dependencies, or reviewing production infrastructure. Do not describe these as validated runtime exploits or fixes.
