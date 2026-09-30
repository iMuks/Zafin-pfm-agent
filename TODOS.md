# TODOS

## Infrastructure

### Audit retention and write-once protection

**What:** Append-only database role for `audit_events` (no UPDATE or DELETE grants to the application role) plus a nightly export to object storage with an object-lock retention policy and a documented retention period.

**Why:** An audit trail that can be edited is not an audit trail. R3 in the design review chose a Postgres audit table written atomically with each sync; this closes the gap that decision named.

**Context:** `docs/designs/multi-source-finance-chatbot.md`, decision ledger R3 (D5) and R13 (D15). `penny/infrastructure/observability/audit_log.py` docstring already states upstream is WORM object storage. The export must stay inside the deployment account under the enclave rule (R6). Start at the Alembic migration that creates `audit_events` and add the role grants there; the export is a worker job.

**Effort:** S
**Priority:** P3
**Depends on:** Milestone 1 ledger (penny_ledger) and the deployment's object storage.

## Design

### DESIGN.md through /design-consultation

**What:** Run /design-consultation to produce DESIGN.md: typeface, spacing scale, color roles (including the new `--attention` #b91c1c), and the component vocabulary shared by the SwiftUI app and the web demo.

**Why:** Two clients and no design system drift within a month; the tokens in the design review are a seed, not a system.

**Context:** docs/designs/multi-source-finance-chatbot.md, Design review section (D12 = 9A tokens and type scale; D13 = 10A clients). Land before the first SwiftUI view.

**Effort:** S
**Priority:** P2
**Depends on:** None.

### Milestone 2 screens: account link confirmation and reconnect flow

**What:** Design the account-link confirmation (proposed bank-to-book pairs by name and balance similarity, confirm each, unlinked accounts explained) and the reconnect flow (Reconnect tap, OAuth in a sheet, return states reconnected / cancelled / failed with retry) with the same state semantics and copy rules as Milestone 1.

**Why:** Both are trust moments in the two-source matching story and have no screen yet.

**Context:** docs/designs/multi-source-finance-chatbot.md, decisions D4 to D21 (Design review). Run /plan-design-review on the Milestone 2 slice once the accounting provider is known from The Assignment.

**Effort:** S
**Priority:** P2
**Depends on:** Milestone 1 shipped; accounting provider chosen.

### Polished connect screen (design D4-D21) delivered in Milestone 1b

**What:** Ship the connect screen as designed (anchor block, account rows, run sub-rows, mapping-confirm shapes, re-upload preview, Attention copy, report with delta line) in Milestone 1b; Milestone 1a uses a minimal connect view on the same endpoints.

**Why:** User one is the founder; the wedge metric needs the ledger and tools, not the polished screen. Deferred by the CEO review (RED-1, D2, 2026-09-30); no design decision changes.

**Context:** docs/designs/penny-revision-3-user-order.md (CEO review ledger RED-1); docs/designs/multi-source-finance-chatbot.md Design review section holds every decision.

**Effort:** M
**Priority:** P2
**Depends on:** Milestone 1a shipped.

### Response-side Guardrails notice copy

**What:** A second `notice` string for response-side Guardrails blocks ("Penny's answer didn't pass her safety check. Try asking another way."), distinct from the prompt-side "Penny can't help with that one."

**Why:** One copy for both cases misreads a legitimate question as disallowed when it was the answer that was filtered.

**Context:** docs/designs/penny-revision-3-user-order.md, design review Pass 7 (2026-09-30); REV-3 defines the single notice for 1a. Needs the Guardrails assessment to distinguish input from output intervention.

**Effort:** S
**Priority:** P3
**Depends on:** REV-3 shipped in 1a.

## Completed
