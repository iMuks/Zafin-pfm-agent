"""Task 2 — enrich raw bank transactions with Claude.

    python scripts/enrich_transactions.py            # uses the cached catalog
    python scripts/enrich_transactions.py --refresh  # re-resolves every descriptor

Reads   data/sample_transactions.csv
Writes  data/merchant_catalog.json        (descriptor -> enrichment, the AI output)
        data/transactions_enriched.json   (the catalog applied to every row)

Runs once, offline. The agent never enriches at request time — it reads the JSON
produced here — so chat latency is unaffected by this pipeline.

# Why two stages

The naive pipeline sends all 300 rows to the model. This one first collapses
them to **distinct descriptors**, resolves those, then applies the result back
across every row. On this file that is 300 rows -> 195 model decisions, and a
re-run costs nothing at all because the catalog is cached.

That is not a micro-optimisation; it is the shape the problem actually has.
Merchant resolution is a property of the *descriptor*, not of the transaction,
and a bank re-deriving "SQ *BLUE BOTTLE" on every one of billions of rows is
paying repeatedly for an answer it already has. The cache is the product.

# Why the dedupe key only masks digits

An earlier attempt normalised harder — stripping city/state suffixes and store
numbers with regexes — and produced keys like `DAVE &` and `SHAKE`, because
"BUSTERS MIAMI FL" and "SHACK ATLANTA GA" both look exactly like a city and a
state. Masking digits is *lossless*: it can only merge descriptors that differ
by a store or terminal number, which are by definition the same merchant.
Everything genuinely ambiguous is left for the model, which is the thing that
is actually good at it.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Literal

import anthropic
from dotenv import load_dotenv
from pydantic import BaseModel, Field

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from penny.domain.models import Transaction  # noqa: E402
from penny.domain.taxonomy import CATEGORIES  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
RAW_CSV = ROOT / "data" / "sample_transactions.csv"
CATALOG_JSON = ROOT / "data" / "merchant_catalog.json"
OUT_JSON = ROOT / "data" / "transactions_enriched.json"

BATCH_SIZE = 25
DEFAULT_MODEL = "claude-sonnet-5"

SYSTEM = f"""You are a transaction enrichment engine for a US retail bank, of the \
kind that processes billions of card and ACH records. For each raw bank \
descriptor you receive, produce clean, customer-facing metadata.

merchant: the human-readable brand a customer would recognise. Strip processor \
prefixes (SQ *, TST*, PAYPAL *, POS DEBIT), store and terminal numbers, trailing \
reference digits, city/state codes and #1234 suffixes. "SQ *BLUE BOTTLE 8823" \
becomes "Blue Bottle Coffee". For non-merchant records (bank fees, transfers, \
payroll) use a clear descriptive label such as "Wire Transfer Fee".

category: EXACTLY one of {list(CATEGORIES)}. Never invent a new one. Use "Other" \
only when genuinely unclear — an insurer is Insurance, a tax payment is Taxes & \
Government, a charity is Charity & Donations. Be consistent: two car insurers \
must not land in two different categories.

direction: "credit" if money ENTERS the account (payroll, refunds, inbound \
transfers), otherwise "debit". This source file has no debit/credit column, so \
infer it from the descriptor.

is_recurring: true ONLY for merchants a customer is plausibly subscribed to on a \
fixed cadence — streaming, SaaS, gym, insurance, telecom. A merchant simply \
visited often (a grocery store, a coffee shop) is NOT recurring. Bank fees repeat \
but are NOT subscriptions: set false for them.

logo_domain: the merchant's primary web domain ("netflix.com"), or null for fees, \
transfers and unrecognisable descriptors.

city / region: the transaction location IF the descriptor contains one. Many \
descriptors end with a city and a two-letter US state or Canadian province — \
"SHAKE SHACK ATLANTA GA" is city "Atlanta", region "GA". Use null for both when \
the descriptor carries no location. Take care: the city is only the trailing \
tokens, never part of the brand name.

confidence: 0.0-1.0 certainty about merchant and category. Be honest — a low \
score is more useful to us than a confident guess.

Return exactly one entry per input descriptor, echoing its key."""


class Enrichment(BaseModel):
    key: str = Field(description="Echo the key from the input descriptor.")
    merchant: str
    category: Literal[tuple(CATEGORIES)]  # type: ignore[valid-type]
    direction: Literal["debit", "credit"]
    is_recurring: bool
    logo_domain: str | None = None
    city: str | None = None
    region: str | None = None
    confidence: float


class EnrichmentBatch(BaseModel):
    descriptors: list[Enrichment]


def dedupe_key(descriptor: str) -> str:
    """Lossless grouping key: collapse digit runs, normalise whitespace.

    Only digits are touched, so two descriptors can merge only when they differ
    by a store or terminal number — which means they are the same merchant.
    """
    return re.sub(r"\s+", " ", re.sub(r"\d+", "#", descriptor)).strip()


def load_raw() -> list[dict]:
    """Read the CSV.

    The assignment brief describes four columns including `type` (credit/debit).
    The supplied file has three — there is no `type` column. Rather than assume
    the brief or the file is authoritative, this reads `type` when it is present
    and lets the model infer direction when it is not, so the pipeline is
    correct against both.
    """
    with RAW_CSV.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))

    if not rows:
        sys.exit(f"{RAW_CSV} is empty.")

    has_type = "type" in rows[0]
    if has_type:
        print("  note: CSV carries a 'type' column — using it instead of inferring direction.")

    parsed = []
    for index, row in enumerate(rows, start=1):
        descriptor = row["transaction_description"].strip()
        parsed.append(
            {
                "id": f"txn_{index:04d}",
                "date": row["date"].strip(),
                "description": descriptor,
                "amount": round(float(row["amount"]), 2),
                "key": dedupe_key(descriptor),
                "declared_direction": (row.get("type") or "").strip().lower() or None,
            }
        )
    return parsed


def resolve_batch(client: anthropic.Anthropic, model: str, keys: list[str]) -> list[Enrichment]:
    payload = [{"key": key} for key in keys]
    response = client.messages.parse(
        model=model,
        max_tokens=16000,
        # The system prompt is identical across batches, so caching it turns
        # 8 batches into 1 full read and 7 cache hits.
        system=[{"type": "text", "text": SYSTEM, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": json.dumps(payload, indent=2)}],
        output_format=EnrichmentBatch,
    )
    return response.parsed_output.descriptors


def _brand_stem(key: str) -> str:
    """A conservative grouping stem: text before the first digit or separator."""
    return re.split(r"[#*]| \d", key)[0].strip().upper()[:14]


def _prefix_compatible(a: str, b: str) -> bool:
    """True when one merchant name is a prefix of the other, ignoring case/spaces.

    "Shell" and "Shell Oil" are the same brand; "Metro" and "MetroPCS" differ by
    a suffix the model added. "Walmart" and "Costco" are not related at all, and
    this returns False for them, which is what stops the pass over-merging.
    """
    x, y = a.replace(" ", "").casefold(), b.replace(" ", "").casefold()
    return x.startswith(y) or y.startswith(x)


def reconcile_entities(catalog: dict[str, dict], weights: Counter[str]) -> list[str]:
    """Collapse one brand that resolved to several names or categories.

    Resolving each descriptor independently is what makes the dedupe cache work,
    but it lets the same brand come back twice: this dataset produced both
    "Shell" and "Shell Oil", and — worse — "Metro" as Groceries alongside
    "MetroPCS" as Utilities & Telecom. Either one silently splits a `GROUP BY
    merchant`, so `get_top_merchants` under-reports a merchant the customer
    actually visits often.

    The winner is the variant carrying the most transactions, and a group is
    only merged when every name in it is prefix-compatible with that winner.
    Anything else is left alone and reported, because a wrong merge is a worse
    outcome than a split one.

    This is a miniature of what an entity-resolution stage does in a real
    enrichment platform.
    """
    groups: dict[str, list[str]] = {}
    for key in catalog:
        groups.setdefault(_brand_stem(key), []).append(key)

    notes: list[str] = []
    for stem, keys in sorted(groups.items()):
        variants = {(catalog[k]["merchant"], catalog[k]["category"]) for k in keys}
        if len(variants) < 2:
            continue

        tally: Counter[tuple[str, str]] = Counter()
        for key in keys:
            tally[(catalog[key]["merchant"], catalog[key]["category"])] += weights[key]
        (winner_merchant, winner_category), _ = tally.most_common(1)[0]

        names = {merchant for merchant, _ in variants}
        if not all(_prefix_compatible(name, winner_merchant) for name in names):
            notes.append(f"  ! {stem}: left unmerged, names are unrelated: {sorted(names)}")
            continue

        for key in keys:
            catalog[key]["merchant"] = winner_merchant
            catalog[key]["category"] = winner_category
        notes.append(f"  merged {sorted(names)} -> {winner_merchant} ({winner_category})")

    return notes


def load_catalog() -> dict[str, dict]:
    if not CATALOG_JSON.exists():
        return {}
    try:
        return json.loads(CATALOG_JSON.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def build_catalog(keys: list[str], refresh: bool, model: str) -> dict[str, dict]:
    catalog = {} if refresh else load_catalog()
    missing = [key for key in keys if key not in catalog]

    if not missing:
        print(f"  catalog already covers all {len(keys)} descriptors — no model calls needed.")
        return catalog

    if not os.getenv("ANTHROPIC_API_KEY"):
        sys.exit("ANTHROPIC_API_KEY is not set. Copy .env.example to .env and add your key.")

    client = anthropic.Anthropic()
    print(f"  resolving {len(missing)} new descriptors with {model}, batches of {BATCH_SIZE}...")

    for start in range(0, len(missing), BATCH_SIZE):
        batch = missing[start : start + BATCH_SIZE]
        for item in resolve_batch(client, model, batch):
            catalog[item.key] = item.model_dump(exclude={"key"})
        print(f"    {min(start + BATCH_SIZE, len(missing))}/{len(missing)}")

    CATALOG_JSON.write_text(json.dumps(catalog, indent=2, sort_keys=True), encoding="utf-8")
    return catalog


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Ignore the cached catalog and re-resolve everything.",
    )
    parser.add_argument("--model", default=os.getenv("PENNY_ENRICHMENT_MODEL", DEFAULT_MODEL))
    args = parser.parse_args()

    load_dotenv()
    raw = load_raw()
    keys = sorted({row["key"] for row in raw})
    print(f"Enriching {len(raw)} transactions ({len(keys)} distinct descriptors).")

    catalog = build_catalog(keys, refresh=args.refresh, model=args.model)

    unresolved = [key for key in keys if key not in catalog]
    if unresolved:
        sys.exit(f"The model did not return {len(unresolved)} descriptors: {unresolved[:5]}")

    notes = reconcile_entities(catalog, Counter(row["key"] for row in raw))
    if notes:
        print("\n  entity reconciliation:")
        print("\n".join(notes))
        CATALOG_JSON.write_text(json.dumps(catalog, indent=2, sort_keys=True), encoding="utf-8")

    enriched = []
    for row in raw:
        resolved = dict(catalog[row["key"]])
        # A declared `type` column, if the file ever has one, outranks inference.
        if row["declared_direction"] in ("debit", "credit"):
            resolved["direction"] = row["declared_direction"]
        enriched.append(
            {
                "id": row["id"],
                "date": row["date"],
                "description": row["description"],
                "amount": row["amount"],
                **resolved,
            }
        )
    enriched.sort(key=lambda t: (t["date"], t["id"]))

    # Validate through the domain model before writing. A record that cannot
    # become a Transaction must fail here, in the pipeline, rather than at the
    # first customer question that happens to touch it.
    for record in enriched:
        Transaction.from_record(record)

    OUT_JSON.write_text(json.dumps(enriched, indent=2), encoding="utf-8")

    debits = [t for t in enriched if t["direction"] == "debit"]
    categories = Counter(t["category"] for t in enriched)
    print(f"\nWrote {OUT_JSON.relative_to(ROOT)}")
    print(f"  {len(enriched)} transactions, {enriched[0]['date']} to {enriched[-1]['date']}")
    print(
        f"  {len(enriched) - len(debits)} credits, ${sum(t['amount'] for t in debits):,.2f} debits"
    )
    print(f"  {len({t['merchant'] for t in enriched})} distinct merchants")
    print(f"  {sum(1 for t in enriched if t.get('logo_domain'))} with a logo domain")
    print(f"  {sum(1 for t in enriched if t.get('city'))} with a resolved location")
    print(f"  {sum(1 for t in enriched if t['is_recurring'])} flagged recurring")
    low = [t for t in enriched if t.get("confidence", 1) < 0.8]
    print(f"  {len(low)} below 0.8 confidence")
    print("\n  categories: " + ", ".join(f"{c}={n}" for c, n in categories.most_common()))


if __name__ == "__main__":
    main()
