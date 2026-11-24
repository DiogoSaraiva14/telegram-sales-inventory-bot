# Telegram interaction design

Status: draft. The imported application currently has a Telegram inline-keyboard interface; there is no web frontend design system.

## Current interface

- Primary language: Portuguese (Portugal), with euro prices.
- Main navigation: catalog, customer orders, help, and an admin entry visible only to configured Telegram administrators.
- Customer paths: category → product → order confirmation → Stripe Checkout; customers can also open their order list.
- Admin paths: products, stock, orders, users, and audit log.
- Controls are Telegram inline keyboards. Callback values are used as route identifiers.
- Messages use Telegram Markdown formatting and status emoji. This is current implementation detail, not a finalized visual standard.

## Draft interaction rules

- Keep each screen focused on one decision and use short, action-oriented labels.
- Every callback emitted by a keyboard must be represented in the central callback allowlist and handled by exactly one route.
- Provide a clear path back to the prior level and to the main menu.
- Show price, quantity, stock, and order state before a purchase or state-changing action.
- Confirm irreversible or consequential actions such as cancellation and stock adjustment.
- Keep customer and admin screens distinct. Check admin identity on every admin mutation.
- Escape dynamic text for the selected Telegram parse mode. Do not treat name sanitization as Markdown escaping.
- Avoid unbounded keyboards. Paginate product, user, and order lists as they grow.
- Display payment state separately from fulfillment state. Do not imply payment success before a verified provider event.
- When a notification fails after a database update, show that state clearly and retain a retry path.

## Status vocabulary

The current order states are pendente, confirmada, enviada, entregue, and cancelada. Payment states are pendente, pago, falhado, and reembolsado. Define allowed transitions and customer-facing copy for each state before changing the state machine.

## Review items

Fixed (SEC-12): the order list emits ver_encomenda_<id>, which is on the callback allowlist and routed to the customer order-detail screen.
