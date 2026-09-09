# Task 2 — the enrichment prompt and approach

The prompt below is the one that runs. It lives in
`scripts/enrich_transactions.py` as `SYSTEM`; this document records it verbatim
plus the reasoning, so the approach is reviewable without reading the code.

Run it with:

```bash
python scripts/enrich_transactions.py            # uses the cached catalog
python scripts/enrich_transactions.py --refresh  # re-resolves every descriptor
```

## Approach: resolve descriptors, not transactions

The naive pipeline sends all 300 rows to the model. This one collapses them to
**distinct descriptors** first, resolves those, then applies the result back
across every row — 300 rows become **195 model decisions** on this file.

That is the shape the problem actually has: merchant identity is a property of
the *descriptor*, not of the transaction. `WALMART GROCERY AUSTIN TX 4471` and
`WALMART GROCERY AUSTIN TX 8123` are the same merchant, and a bank re-deriving
that on every one of billions of rows is burning money for no new information.

The grouping key collapses digit runs only:

```python
re.sub(r"\s+", " ", re.sub(r"\d+", "#", descriptor)).strip()
```

Because only digits are touched, two descriptors can merge **only** when they
differ by a store or terminal number — which means they are the same merchant.
No brand text is ever altered, so the merge is lossless.

The resolved catalog is written to `data/merchant_catalog.json` and reused, so a
re-run costs nothing. `--refresh` forces re-resolution.

## The system prompt, verbatim

> You are a transaction enrichment engine for a US retail bank, of the kind that
> processes billions of card and ACH records. For each raw bank descriptor you
> receive, produce clean, customer-facing metadata.
>
> **merchant**: the human-readable brand a customer would recognise. Strip
> processor prefixes (`SQ *`, `TST*`, `PAYPAL *`, `POS DEBIT`), store and
> terminal numbers, trailing reference digits, city/state codes and `#1234`
> suffixes. "SQ \*BLUE BOTTLE 8823" becomes "Blue Bottle Coffee". For
> non-merchant records (bank fees, transfers, payroll) use a clear descriptive
> label such as "Wire Transfer Fee".
>
> **category**: EXACTLY one of \<the closed list\>. Never invent a new one. Use
> "Other" only when genuinely unclear — an insurer is Insurance, a tax payment
> is Taxes & Government, a charity is Charity & Donations. Be consistent: two car
> insurers must not land in two different categories.
>
> **direction**: "credit" if money ENTERS the account (payroll, refunds, inbound
> transfers), otherwise "debit". This source file has no debit/credit column, so
> infer it from the descriptor.
>
> **is_recurring**: true ONLY for merchants a customer is plausibly subscribed to
> on a fixed cadence — streaming, SaaS, gym, insurance, telecom. A merchant
> simply visited often (a grocery store, a coffee shop) is NOT recurring. Bank
> fees repeat but are NOT subscriptions: set false for them.
>
> **logo_domain**: the merchant's primary web domain ("netflix.com"), or null for
> fees, transfers and unrecognisable descriptors.
>
> **city / region**: the transaction location IF the descriptor contains one.
> Many descriptors end with a city and a two-letter US state or Canadian
> province — "SHAKE SHACK ATLANTA GA" is city "Atlanta", region "GA". Use null
> for both when the descriptor carries no location. Take care: the city is only
> the trailing tokens, never part of the brand name.
>
> **confidence**: 0.0–1.0 certainty about merchant and category. Be honest — a
> low score is more useful to us than a confident guess.
>
> Return exactly one entry per input descriptor, echoing its key.

## Why the output is schema-constrained

The model replies through Anthropic structured outputs against a Pydantic
schema, so `category` is a `Literal` over the closed list and cannot be a value
the analytics layer has never heard of.

An open-ended "pick a category" prompt returns `Dining`, `Restaurants`,
`Food & Drink` and `Eating Out` across different batches, and every downstream
`GROUP BY category` silently splits. Constraining the schema makes the totals
Penny reports trustworthy rather than merely plausible.

## Why `direction` is inferred rather than read

The brief describes four columns including `type` (credit/debit). The provided
`sample_transactions.csv` has **three** — `transaction_description`, `amount`,
`date`. Rather than assume every row is a debit, direction is inferred from the
descriptor here, which is what keeps the two payroll deposits out of spending
totals.

## Fields produced

| Field | Purpose |
| --- | --- |
| `merchant` | customer-facing name |
| `category` | one of the closed list in `penny/domain/taxonomy.py` |
| `direction` | `debit` / `credit`, inferred |
| `is_recurring` | subscription candidate, confirmed later against observed cadence |
| `logo_domain` | drives the merchant logos in the chat UI (bonus track A) |
| `city` / `region` | location data, when the descriptor carries it |
| `confidence` | self-reported certainty, for spot-checking |

This mirrors what Zafin Transaction Enrichment does at scale — clean merchant
names, spending categories, logos, location data and recurring-charge flags —
with a 16-category list rather than Zafin's 70+.

## Cost control

The system prompt is identical for every batch and is sent with
`cache_control: ephemeral`, so a run of 8 batches pays for one full prefix read
and seven cache hits.
