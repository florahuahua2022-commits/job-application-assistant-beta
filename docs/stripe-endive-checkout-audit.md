# Checkout Endive compatibility audit ¡ª 2026-10-24

Scope: `2026-09-30.endive`, `stripe==16.0.0`, hosted Checkout payment mode. Reviewed all Stripe calls in payments.py and payment_operations.py, webhook handlers, reservation SQL, and CI. No production access or Dashboard changes.

## Request and response inventory

| Used surface | Result |
| --- | --- |
| Checkout create `payment_method_types` | Removed in Endive; deleted. Dashboard dynamic payment methods now determine eligibility. No replacement filter or `automatic_payment_methods` parameter needed. |
| `mode=payment`, `line_items[].quantity` | Supported. |
| `line_items[].price_data.currency`, `unit_amount`, `product_data.name` | Supported; amounts remain integer cents, currency lower-case AUD. |
| `metadata` (user_id, package_code, credits, catalog_version, gst_enabled) | Supported; these keys are application-owned strings, not Stripe schema fields. |
| `client_reference_id`, optional `customer_email` | Supported. |
| `success_url`, `cancel_url`, `{CHECKOUT_SESSION_ID}` placeholder | Supported for hosted Checkout. Checkout handles redirects; do not add PaymentIntent server-confirmation `return_url` logic. |
| `expires_at` | Supported; currently 30 minutes from request start. Stripe requires 30 minutes¨C24 hours: very slow requests/clock drift can hit the lower bound; not a removed/renamed field. |
| SDK `options.idempotency_key`, explicit `stripe_version` | Supported; wire test verifies both headers and create endpoint. SDK 16.0.0 default version matches our pin. |
| Session retrieve / Event retrieve (ID only) | Supported; no removed request fields used. |
| Session response `id`, `url`, `livemode`, `status`, `payment_status`, `mode`, `currency`, `amount_total`, `metadata`, `payment_intent` | No Endive removal/rename found. PaymentIntent remains nullable/expandable; current retrieval does not request expansion and paid processing requires its ID. |
| Event `id`, `type`, `livemode`, `data.object` | Supported for snapshot events. Webhook endpoint version is configured separately from the SDK request version. |
| Checkout completed / async_payment_succeeded / async_payment_failed / expired event types | No rename found. Completed with unpaid status does not grant; async success grants once. |
| Refund/charge/dispute event types; object `id`, `payment_intent`, `charge`, dispute `status` | No Endive removal/rename found. These are observations; refund execution is not implemented here. |

Other Endive changes (billing-cycle anchors, PayTo-specific fields, BLIK decline codes, Elements/payment request button, Connect and Radar) do not affect fields or APIs this repository uses. New Payment Record fields are additive and do not replace our PaymentIntent ID.

Endive introduces v1 thin events as an option, not an automatic replacement for snapshot events. This handler expects `data.object`; keep the destination on **snapshot events** with Endive version. Thin-event support would require a separate retrieval/migration change. Do not switch destination format as part of this fix. Dynamic methods can include delayed methods, so subscribe to both async success and failure in addition to completed and expired. Keep card-only acceptance, if desired, in Dashboard payment-method settings; no Dashboard was changed here.

## Failed request and retry

Reservation is committed before Stripe create. An exception from Stripe sets `paymentcheckoutrate.status=released`, sets `released_at`, and commits. Purchase insertion occurs only after Stripe returns a session. Therefore this specific InvalidRequestError path creates **no purchase** and leaves a released rate reservation (assuming the cleanup commit succeeds). No Stripe session is created by a parameter-validation rejection.

A new key uses a different per-user hash. Released reservations are excluded from the 5-per-10-minute limit in both SQLite and PostgreSQL reservation paths. The rejected attempt cannot block the new key or duplicate its purchase. Repeating the successful new key retrieves the existing session and creates no second purchase. Reusing the original released key is locally allowed for the same package, but request parameters changed; use the requested new key to avoid remote idempotency ambiguity.

This is code-path analysis and a reproduced DB regression, **not a query of the historical incident database**. No incident database was positively identified/accessed. A network timeout or DB failure *after* remote creation is a different case: a fresh key can create another session; do not generalize the validation-error conclusion to ambiguous failures. Different successful keys deliberately permit separate purchases.

## CI gap and prevention

The previous tests replaced StripeGateway with a permissive mock and explicitly asserted the obsolete card parameter. SQLite/PostgreSQL ledger tests cannot validate a remote API contract. SDK dictionaries are serialized without runtime validation against generated type definitions, so pinning the SDK/version alone did not reject the bad field.

The updated tests run the real SDK with a fake key and intercepted HTTP, checking wire version, idempotency header, endpoint, serialized amounts, and all used request keys against the pinned SDK's generated parameter definitions (including nested line-item types). Separate regressions cover InvalidRequestError cleanup/new-key retry/repeat success, and signed delayed-payment snapshots with exactly one grant. Existing CI unittest discovery automatically includes these tests; no secrets or new dependencies are required.

Offline SDK checks cannot prove Dashboard eligibility or live Stripe validation. Before any future API/SDK version upgrade or payment release, require a test-mode Stripe sandbox smoke checkout and signed webhook delivery using the intended destination version/format; verify delayed success/failure subscriptions as applicable. Never use production credentials for this gate. No real Stripe request was sent during this fix.

## Primary sources

- https://docs.stripe.com/changelog/endive/2026-09-30/remove-payment-method-types-checkout-sessions
- https://docs.stripe.com/changelog/endive
- https://docs.stripe.com/api/checkout/sessions/create
- https://docs.stripe.com/api/checkout/sessions/object
- https://docs.stripe.com/webhooks/versioning
- https://support.stripe.com/questions/fix-the-payment-method-types-error

The versioned create/object reference URLs were unavailable through the documentation reader; the version-specific changelog plus installed Endive SDK definitions provided the version-bound cross-check. This is an audit of fields used by this integration, not a claim to have exercised every Stripe payment method.
