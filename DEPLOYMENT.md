# Deploying a shop

This guide is for the reseller who installs the bot for a client shop. It follows the cart design in [docs/designs/cart_resellable_shop.md](docs/designs/cart_resellable_shop.md).

**Status:** written from the code and its tests. No copy has been deployed yet, and nothing has run against live Telegram or Stripe. Run the Stripe test-mode checklist below before taking real payments.

Invoicing, tax documents, shipping costs, withdrawal handling, terms and a privacy notice are outside the bot. Each shop arranges them itself, and the reseller contract should say so. This is not legal advice.

## One copy per shop

Each shop gets its own copy of the project, with its own:

- PostgreSQL database;
- Telegram bot (token from BotFather);
- Stripe account and keys;
- `.env` file;
- bot process, Django web process and cron entry.

Nothing is shared between shops, so one shop's data can't reach another.

## What runs

| Process | Command | Notes |
| --- | --- | --- |
| Bot | `python bot.py` | Long polling. Run exactly one per bot token (Telegram refuses a second one). Keep it running with a service manager such as systemd. |
| Django web | a WSGI server serving `config.wsgi` | Serves the Django admin at `/admin/` and the Stripe webhook at `/webhook/stripe/`. It must be reachable over HTTPS. The project pins no WSGI server; choose one for the host. It logs to the console (the app at INFO, everything else at WARNING); keep that output, for example in the service manager's journal. |
| Cleanup | cron, every 5 minutes | See "Scheduled cleanup". |

The webhook runs in the Django web process, so payments are still confirmed while the bot is stopped.

The tested versions are Python 3.13 and PostgreSQL 17.

## 1. Telegram bot

1. In BotFather, create the bot with `/newbot` and keep the token.
2. Set the shop's profile photo with `/setuserpic`, and its description with `/setdescription`. The bot can't set its own photo.

## 2. Server and database

1. Copy the project to the server, for example `/srv/loja`, then:
   ```bash
   python3.13 -m venv .venv
   .venv/bin/pip install -r requirements.txt
   ```
2. Create a PostgreSQL role with `LOGIN` only (no superuser), and a database it owns.
3. Restrict the log folder to the account that runs the bot: `mkdir -p logs && chmod 700 logs`.

## 3. `.env`

Copy `.env.example` to `.env` and fill it in. `.env` must never be committed or shared.

- `SECRET_KEY`: a long random string, different for each shop.
- `DEBUG=False`.
- `ALLOWED_HOSTS`: the shop's host name, for example `loja.example.com`.
- `DJANGO_HTTPS=True`. This turns on the HTTPS redirect, secure cookies and HSTS for one year, so only set it once HTTPS works.
- `DJANGO_BEHIND_PROXY=True` only when a reverse proxy terminates HTTPS and sets `X-Forwarded-Proto`.
- `CSRF_TRUSTED_ORIGINS`: for example `https://loja.example.com`.
- `DB_*`: the database from step 2.
- `TELEGRAM_TOKEN`: from BotFather.
- `ADMIN_TELEGRAM_IDS`: your own Telegram user id, so you can reach the admin panel before handover.
- `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`, `STRIPE_SUCCESS_URL`, `STRIPE_CANCEL_URL`: start with test-mode keys.

## 4. Stripe webhook

In the Stripe dashboard (test mode first), add a webhook endpoint `https://<host>/webhook/stripe/` with the events `checkout.session.completed` and `checkout.session.expired`. Put its signing secret in `STRIPE_WEBHOOK_SECRET`.

Payments are card-only. Each payment link expires 60 minutes after the order was created.

## 5. Database setup

```bash
.venv/bin/python manage.py migrate
.venv/bin/python manage.py createsuperuser
```

The superuser is the reseller's account. It has every permission, including cancelling and refunding orders in Django admin.

## 6. The shop in Django admin

Sign in at `https://<host>/admin/` with the superuser.

1. **Loja:** the shop name and welcome text shown by `/start` and on the admin panel, and an optional welcome image sent above the `/start` message (any common image format; stored as JPEG with its long side at most 1280 px, shape kept). The "Dono da loja" group can change all three. There is one row; the list opens it.
2. **Categorias:** add the shop's categories. Use the order field to sort them. Deactivate a category instead of deleting it; deleting is disabled.
3. **Produtos:** add the products, each in a category, with price, stock and an optional image. Deactivate products instead of deleting them.

## 7. The owner's accounts

**Django admin account:**

1. Create a staff user for the owner (Users → Add, then tick "Staff status").
2. Add it to the group **Dono da loja**. Don't make it a superuser.

The owner can then add, edit and deactivate products and categories, edit the shop settings, and read orders, order lines and payments. The owner can't delete anything, see customers or staff accounts, or ship, deliver, cancel or refund orders in Django admin. Django refuses those actions on the server, not only by hiding them.

**Telegram admin rights:**

1. Ask the owner to send `/start` to the bot once, so their Telegram user exists.
2. In Django admin, open Utilizadores, find the owner and tick **e_admin**. Only a superuser can change this field. It takes effect on the owner's next tap, with no restart.

The owner then manages orders, stock and cancellations from the bot's admin panel (`/admin` in Telegram). Cancelling a paid order there refunds it in full.

## 8. Handover

Once the owner's Telegram user has **e_admin** ticked:

1. Set `ADMIN_TELEGRAM_IDS` to your own id only, or leave it empty.
2. Restart the bot once.

That restart is the only way to remove an id from `ADMIN_TELEGRAM_IDS`. Every other admin change is made with **e_admin** and applies at once.

## 9. Scheduled cleanup

Install the cron entry from [deploy/crontab.example](deploy/crontab.example), adjusting the folder. Every 5 minutes it runs:

```bash
.venv/bin/python manage.py cancelar_encomendas_abandonadas --horas 1
```

Each run:

- **reconciles every pending order older than 60 minutes:**
  - no payment link: the order is cancelled and its stock returned;
  - the payment link was paid: the order is confirmed, as the webhook would;
  - an open or expired link: the link is expired, and the order is cancelled with its stock returned;
  - a Stripe or database error: the order is left as it is and retried on the next run;
- deletes confirmation screens that never became an order, after 45 minutes;
- deletes carts with no change for 30 days;
- deletes sent or given-up customer messages after 30 days;
- resends customer messages that Telegram didn't accept yet. After 24 hours, or at once if Telegram refuses the chat (the customer blocked the bot), a message is given up and a "Notificação falhada" row is added to the audit log;
- prints one summary line: confirmed, cancelled, errors, the screens and carts deleted, and the messages resent and given up.

**Setup check:** run the command once by hand, then confirm that a second summary line appears in `logs/limpeza.log` within 5 minutes.

**Watching it:** check the log daily. Investigate when the error count stays above zero across runs, or when summary lines stop appearing. Until a run succeeds, an affected order keeps holding its stock, so the 65-minute limit on held stock applies only while the cleanup, Stripe and the database are working. There is no automatic alert.

## Personal data: what is kept, and who sees it

Each shop is the controller of its customers' data and writes its own privacy notice. This section describes what the software stores; it is not legal advice.

| Data | Where | Kept |
| --- | --- | --- |
| Telegram id, name, username, block reason | `Utilizador` | until the customer is anonymised (below) |
| Cart | `Carrinho` | deleted 30 days after the last change |
| Confirmation screens without an order | `ConfirmacaoCarrinho` | deleted after 45 minutes |
| Orders, order lines, payments, the screens they came from | `Encomenda`, `LinhaEncomenda`, `Pagamento`, `ConfirmacaoCarrinho` | kept: each shop sets its own period from its invoicing and tax duties |
| Audit log | `RegistoAuditoria` | kept, as above. Descriptions name customers only by internal id ("Cliente #12"). Order and payment rows link to the customer; product, stock and block rows link to the Telegram admin who acted |
| Customer messages (Telegram chat id, order number, total) | `Notificacao` | deleted 30 days after being sent or given up |
| Bot log | `logs/bot.log` | 14 daily files. Customers appear only as a keyed pseudonym (SEC-08) |
| Database backups | the shop's backup location | as long as the shop keeps them; an anonymised customer stays in older backups |

**Deleting old orders:** the software never deletes orders on its own. If a shop's retention period requires it, the reseller deletes them by date from the database, after a backup, and records it.

**A customer asks to have their data removed:** in Django admin, the reseller's superuser selects the customer and runs **Anonimizar (pedido de remoção de dados)**. It replaces the name, username and Telegram id, clears the block reason, blocks the row, deletes the cart, unused confirmation screens and stored messages, and adds an audit row. Orders and payments stay for the accounts, linked to "Cliente removido". It refuses a customer with an order still pending, confirmed or shipped: finish or cancel that order first. If the person writes to the bot again later, they start as a new customer.

**Who sees what:**

| Who | Sees |
| --- | --- |
| Reseller's superuser (Django admin) | everything, and is the only one who can anonymise customers or grant Telegram admin rights |
| Shop owner ("Dono da loja" group, Django admin) | products, categories, shop settings, and orders and payments read-only (an order shows the customer's name and Telegram id). Not the customer list or the audit log |
| Telegram admins (`/admin`) | customer names and usernames, order details including the customer's Telegram id, and the audit log |
| Server access | the database, backups and `logs/` |

## 10. Stripe test-mode checklist

Before switching to live keys, in test mode with the real webhook:

1. Add two products to the cart, change a quantity, and pay with a Stripe test card. The order becomes confirmed and the customer gets "Pagamento confirmado".
2. Change a product's price while it is in a cart. The cart and the confirmation screen mark the new price.
3. Cancel a paid order from the Telegram admin panel. Stripe shows a full refund, and the customer gets the refund message.
4. Leave an order unpaid for 60 minutes. It is cancelled by Stripe's expiry webhook or by the cleanup, and its stock is returned. "Repetir encomenda" then puts its products back in the cart.
5. Check that stock = initial stock − quantities of paid orders.

Then replace the Stripe keys and webhook secret with live ones, and restart both processes.

## Backups and upgrades

Migrations only move forward. Some can't be undone (for example `0010`, which turns the old category codes into rows), so a backup is the only way back.

**Before every `migrate`:**

```bash
pg_dump --format=custom --file=backup-$(date +%F-%H%M).dump <database>
```

To restore, stop both processes and run `pg_restore --clean --dbname=<database> <file>`. Then deploy the code version that matches the backup.

**Upgrading an existing copy:**

1. Take a backup.
2. Stop the bot.
3. Update the code, then run `.venv/bin/pip install -r requirements.txt` and `.venv/bin/python manage.py migrate`.
4. Restart the Django web process and start the bot.

Orders created before the cart are reconciled by the next cleanup runs.
