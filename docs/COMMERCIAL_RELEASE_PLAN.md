# Commercial release plan

## Confirmed product rules

- New accounts receive 2 free generation credits.
- A standard tailored CV and cover letter pack costs 1 credit.
- A pack that includes Selection Criteria costs 2 credits.
- Editing, saving and downloading an existing pack does not consume another credit.
- The inviter earns 1 credit only after the invited user verifies their email and completes a first successful generation.
- Referral rewards are capped at 5 credits per inviter per calendar month initially.
- Base prices are in AUD. Stripe Checkout Adaptive Pricing may present a supported local currency to international customers.

## Launch prices

| Plan | Credits | AUD price |
|---|---:|---:|
| Single Pack | 1 | A$16.95 |
| Starter Pack | 8 | A$109.95 |
| Job Search Pack | 18 | A$199.00 |

## Private beta behaviour

The pricing preview is visible for feedback, but charging and commercial credit enforcement remain disabled. Existing beta generation safeguards continue to apply until the release gate is approved.

## Release gate

1. Configure a verified sending domain and custom SMTP provider in Supabase.
2. Create Stripe products and AUD prices in test mode.
3. Implement server-created Checkout Sessions and signature-verified webhooks.
4. Grant the 2-credit trial idempotently when an eligible account is activated.
5. Debit credits atomically before generating a new application pack and refund them automatically if generation fails.
6. Award referrals only after the invited account qualifies, with self-referral, duplicate-account and monthly-limit checks.
7. Test successful payment, cancellation, duplicate webhook delivery, refund and chargeback paths.
8. Confirm GST wording, refund policy, privacy policy and terms before enabling live payments.
