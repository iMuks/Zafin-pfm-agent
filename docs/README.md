# Deliverables 3 & 4 — what still needs producing

- `architecture.png` — architecture diagram (Deliverable 3). The layer diagram
  in the root README is the source; render it in Excalidraw or Mermaid.
- `build-report.html` — interactive build report (bonus Deliverable).
- `screenshot.png` — grab it from `python scripts/preview_components.py`, which
  renders every component with no API key.

## Build-report section map

Most sections can be written directly from what exists in the repo:

| Report section | Where the material already is |
| --- | --- |
| Executive summary | root README intro + Design decisions |
| User stories & coverage | README "User stories" (11 answered, 1 deliberately declined) |
| Tech stack | README "Architecture" layer diagram |
| Backend components | README "Repository layout" |
| System architecture | the 9-layer diagram |
| End-to-end flow | `START → initializer → react_agent → save_chat_state → END`, plus the JSONL component stream |
| Architecture components | the component contract table |
| Design principles | README "Design decisions" |
| Production hardening | README "What is deliberately not built" + the notes below |
| Quality | Layer 9 eval — paste the numbers from `eval/out/report.html` |

## Production hardening — notes to expand

**Mobile integration (native iOS).** The phone frame here is a CSS mock, but the
wire contract is already the one a native client wants: newline-delimited JSON
where each line is a complete UI component. A SwiftUI client consumes the chunked
body, buffers it in an actor-isolated tokenizer that splits on `\n`, and decodes
each line polymorphically on the `component` discriminator into an enum of
payload cases — one per row of the contract table — with an `.unsupported(raw)`
case so an unrecognised component from a newer backend renders as a placeholder
instead of dropping the turn.

- Actor-isolated buffer so stream parsing never races the UI; view models on the
  main actor; payload models `Sendable` for a safe cross-actor boundary.
- The component enum makes rich rendering native: tables, Swift Charts for
  bar/line/pie, merchant rows with cached logos — no markdown parsing on-device.
- `smart-loading` covers the tool-call gap; a stream that dies mid-object
  surfaces as a retryable error rather than a half-drawn answer.
- Session token in the Keychain; the model API key never leaves the backend.
- Dynamic Type and VoiceOver on every component, including chart summaries.
- Cancellation must reset the tokenizer buffer, or the next turn starts
  mid-object.

**Storage.** Sessions and the audit trail already sit behind interfaces
(`SessionRepository`, `penny/infrastructure/observability/audit_log.py`). Production swaps them for DynamoDB
single-table (PK=`USER#{poid}`, SK=`SESSION#{id}` / `#TURN#{ts}`, TTL attribute)
and S3 with WORM retention. Turn-per-item avoids the 400KB item ceiling; the
zero-padded sort key already in `sessions.py` is what makes a session read one
round trip.

**Scale.** Enrichment moves to a streaming pipeline over the bank's ledger —
Batch API for backfill, per-transaction on the authorisation feed. Analytics
tools become queries against a columnar store rather than an in-process list.
The app holds per-process session state, so scale is container count, not
worker count.

**Security.** No PAN or account identifier in a prompt — tokenize before the LLM
boundary. Real JWT + EUP verification replaces the header-shape mirror in
`penny/presentation/http/middleware.py`. The tool layer already binds every query to the
authenticated poid, so a prompt-injected request cannot cross accounts. Add
content guardrails inline with inference.

**Observability.** OTel spans already exist; point the exporter at a collector.
Ship TTFT/TTFC and tool durations as SLIs with an error budget; alert on
`empty_response` rate and on turns hitting the model-call cap.

**Safety.** Insights-only is enforced by the tool surface, not just the prompt —
there is no payment tool to call. The Layer 9 eval gates prompt changes: run it
before and after, and treat a Risk regression as a release blocker.
