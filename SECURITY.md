# Security and code-review record

Status: static review of the imported source snapshot only. No tests, runtime exploit checks, dependency audit, or deployment review were run. Priorities below are engineering urgency, not CVSS scores or claims of demonstrated exploitation.

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
- The GitHub review branch is private; bundled product images and Python bytecode were not imported.

## Findings

### SEC-01 — Unsafe Django configuration defaults (High priority)

config/settings.py lines 14–20 permits SECRET_KEY to be absent, defaults DEBUG to True, and accepts every host with ALLOWED_HOSTS = ['*']. A deployment that does not override these values starts with unsafe settings or fails later in confusing ways. Require explicit environment-specific values and fail startup when production settings are incomplete.

### SEC-02 — Order and stock writes are not atomic (High priority)

bot.py lines 358–379 create an order, create a line, decrement stock, and write a stock movement as separate database operations. The stock value comes from an earlier read and is clamped with max(0, ...). Failures can leave partial state, and concurrent checkouts can both pass the prior stock check. Put the invariant in one transactional service with a conditional decrement or row lock.

### SEC-03 — Checkout failures can strand pending orders and stock (High priority)

bot.py lines 708–752 creates the order and consumes stock before creating Stripe Checkout. The exception path displays a support message but does not cancel the order or restore stock. Also define recovery if Stripe creates the session but saving the Pagamento row fails.

### SEC-04 — Expired Checkout does not resolve order or inventory (High priority)

core/views.py lines 112–134 marks the Pagamento failed and writes audit, but leaves the Encomenda pending and does not restore the stock consumed at order creation. Define a single expiry/cancellation policy and make its state and stock effects atomic.

### SEC-05 — Webhook idempotency is not atomic (High priority)

core/views.py lines 45–79 checks payment state, then performs separate payment, order, audit, and notification effects. Concurrent duplicate Stripe deliveries can pass the state check before either update commits. Persist provider event IDs or use a row-locked transaction and an outbox/retry mechanism for notifications.

### SEC-06 — Cancellation behavior diverges across admin surfaces (High priority)

The Telegram admin flow permits cancelling pending or confirmed orders and restores stock (bot.py lines 409–435 and 977–986), but does not update/refund the related Pagamento. Django bulk cancellation only updates Encomenda.estado (core/admin.py lines 110–113), without stock movement, payment state, notification, or audit. Route every cancellation through the same domain operation and define refund handling.

### SEC-07 — Markdown rendering uses dynamic values without escaping (Medium priority)

The Telegram /start message formats the stored user name as Markdown (bot.py lines 522–528). sanitizar_nome_utilizador removes selected characters but does not escape Telegram Markdown metacharacters (security/sanitization.py lines 87–92). Product names/descriptions are also inserted into Markdown messages. Use context-specific escaping or avoid parse mode for dynamic content.

### SEC-08 — Personal identifiers are written to logs (Medium priority)

security/decorators.py lines 65–72 logs Telegram user IDs and usernames. bot.py and core/views.py also log Telegram identifiers during notifications and administrative actions. Minimize or pseudonymize identifiers and define log retention/access.

### SEC-09 — Rate limiting and session state are process-local (Medium priority)

security/decorators.py lines 13–43 and bot.py lines 102–140 store rate and session state in module-level dictionaries. Limits reset on restart and are independent across processes; inactive keys are not periodically evicted. Document single-process assumptions or use bounded shared state if deployment scales out.

### SEC-10 — Admin order editing can bypass domain invariants (Medium priority)

core/admin.py lines 70–84 exposes editable order-line fields while Encomenda.total is readonly. LinhaEncomenda.save recalculates its subtotal (core/models.py lines 112–132), but does not recalculate the order total or stock/payment state. Django bulk status actions also bypass notification/audit/domain operations. Make order records read-only or use shared validated domain actions.

### SEC-11 — Product images are reprocessed on ordinary model saves (Medium priority)

core/models.py lines 48–78 resizes and re-encodes the stored image whenever Produto.save() runs, including stock or active-state updates. Repeated saves can degrade image quality and rely on local filesystem paths. Process only newly uploaded images and use the configured storage interface.

### SEC-12 — Input validation allows known callback prefixes, but route names have drifted (Medium priority)

bot.py lines 641–660 emits and handles user_encomenda_<id>. security/sanitization.py lines 22–39 does not allow that prefix; it allows ver_encomenda_ instead. The validator at lines 43–54 rejects the generated order-detail callback, so users cannot open order details from their order list.

## Additional review notes

- core/tests.py is only an empty TestCase scaffold. No automated tests or CI were present in the imported snapshot.
- Stripe expiry, refund, asynchronous-payment, and retry semantics are not documented.
- The source stores Telegram identifiers and names. The phone field exists in the model; no population path was observed in the reviewed bot code.
- The project has no documented data retention, privacy notice, upload limits, or deployment threat model.
- .gitignore excludes .env and media, but the source snapshot included committed product images. These were omitted from GitHub; verify ownership and licensing before adding replacements.
- The admin_only decorator in security/decorators.py is defined but not used by bot.py; actual callback checks are repeated inline.

## Review limitations

The observations are based on source inspection and the imported archive. They have not been confirmed by running the application, simulating webhook events, testing concurrency, auditing dependencies, or reviewing production infrastructure. Do not describe these as validated runtime exploits or fixes.
