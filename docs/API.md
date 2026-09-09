# Penny HTTP API

Everything a client needs to consume Penny. The interactive equivalents are
served by the running app:

| | |
| --- | --- |
| Swagger UI | `http://127.0.0.1:8000/docs` |
| ReDoc | `http://127.0.0.1:8000/redoc` |
| OpenAPI 3.1 | `http://127.0.0.1:8000/openapi.json` (also committed as [`openapi.json`](openapi.json)) |

Start the server with:

```bash
uvicorn penny.presentation.http.app:app --reload
```

---

## The one thing to get right

Both agent endpoints return **newline-delimited JSON over HTTP chunked
transfer** — `Content-Type: text/plain`, one complete UI component per line.

**Do not buffer the body and parse it once.** Read it line by line and dispatch
on each line's `component` key. That is the whole point: a component renders the
moment it arrives, so the first sentence of an answer is on screen while the
chart beneath it is still being generated.

Why NDJSON rather than SSE or a WebSocket: plain chunked HTTP, no long-lived
connection, firewall-friendly, and one line maps cleanly onto one enum case in a
typed client.

---

## Endpoints

### `POST /agent/chat`

Ask a question. Streams components.

```jsonc
// Request — the production envelope
{"input": {"content": {"body": "How much did I spend on Dining in June?"}}}
```

Flatter shapes are accepted so a `curl` one-liner is not a puzzle:
`{"query": "..."}`, `{"message": "..."}`, `{"body": "..."}`, and
`{"input": {"content": [{"body": "..."}]}}` all work.

**There is deliberately no conversation history in the request body.** History
is held server-side and keyed by session, so a client cannot replay or rewrite
earlier turns — which means it cannot forge a turn in which Penny appeared to
agree to something.

### `GET /agent/greeting`

Penny's unprompted opener: a real figure from recent spending plus follow-up
intents. Same component stream. A live model call every time, never cached.
Returns `404` when disabled via `PENNY_ENABLE_GREETING=false`.

### `GET /api/health`

Readiness plus the dataset summary. Typed as `HealthResponse` in the spec.

```jsonc
{
  "status": "ok",
  "model": "claude-opus-5",
  "effort": "medium",
  "prompt_version": "v1.0",
  "api_key_configured": true,
  "greeting_enabled": true,
  "tools": ["get_data_coverage", "get_spending_by_category", "..."],
  "data": {
    "first_date": "2026-01-28",
    "latest_date": "2026-07-28",
    "last_complete_month": "2026-06",
    "partial_month": "2026-07",
    "transaction_count": 300,
    "note": "This dataset is transaction history only. It contains no account balance..."
  }
}
```

`last_complete_month` and `partial_month` matter: the final month of data may be
incomplete, so a total for it understates and a month-over-month comparison
against it is wrong.

---

## Headers

| Header | Direction | Purpose |
| --- | --- | --- |
| `x-session-id` | response, then request | Server-side conversation id. **Echo it back** to continue the conversation. Omit to start fresh. |
| `x-request-id` | response | Correlation id, also written to the server logs. Send your own to propagate a trace id. |
| `Authorization: Bearer <JWT>` | request | Optional. A subject claim is read for session scoping. |
| `ext-user-profile-compressed` | request | Optional. Base64 JSON; a `poid` claim wins over the JWT subject. |

> **The prototype does not verify either token.** There is no identity provider
> behind it. The header shape and the pseudonymous-id plumbing are real, and a
> session is scoped to its owner — an id belonging to another customer is
> treated as absent. The cryptography is what a real deployment must add.

---

## The component contract

Every line is one of these. Each is independently renderable and independently
validated server-side.

| `component` | Fields | Meaning |
| --- | --- | --- |
| `chat-response` | `body: string` | Prose answer. Usually first. |
| `table` | `headers: string[]`, `data: string[][]` | Every row has exactly `headers.length` cells. |
| `ordered-list` / `unordered-list` | `items: string[]` | Ranked / unranked points. |
| `bar-chart` / `line-chart` | `valueTitle: string`, `labels: string[]`, `data: {label, values[]}[]` | `labels` names the series; one `values` entry per series. |
| `pie-chart` | same | Exactly one value per slice, a percentage `0–100`. |
| `suggested-user-intents` | `intents: string[]` | Tappable follow-ups. Ends most turns. |
| `transaction-list` | `transactions: {merchant, amount, subtitle, logo_domain}[]` | Merchant rows. Server-generated from tool output. |
| `smart-loading` | `body: string`, `tool?: string` | Work in progress; replace on the next one, clear on first real component. |
| `try-again-error` | `body: string` | Turn failed. Offer a retry. |
| `feedback` | — | Prompt for 👍 / 👎. |
| `done` | `telemetry?: {ttft_ms, ttfc_ms, components, tool_calls}` | **Terminates the stream.** |
| `unsupported` | `reason?: string`, `raw?: object` | Failed validation, or newer than your client. |

### Two rules for forward compatibility

1. **Never crash on an unknown `component`.** Render a placeholder, as
   `unsupported` does. A newer server must be able to add components without
   breaking older clients.
2. **A malformed line is dropped, not fatal.** The server already degrades
   invalid components to `unsupported`; if a line still fails to parse on your
   side, skip it and keep reading.

The machine-readable version of this table is published in the spec at
`paths./agent/chat.post.responses.200.content.text/plain.schema.x-ndjson-line-schema`
— a discriminated union with local `$defs`, so it can be lifted out and fed to a
code generator on its own.

---

## Examples

### curl

```bash
# One turn, streamed as it arrives (-N disables curl's buffering)
curl -N -X POST http://127.0.0.1:8000/agent/chat \
  -H 'content-type: application/json' \
  -d '{"input":{"content":{"body":"How much did I spend on Dining in June?"}}}'

# Continue the same conversation
curl -N -X POST http://127.0.0.1:8000/agent/chat \
  -H 'content-type: application/json' \
  -H 'x-session-id: sess_01a0871a3307de2b4eb6' \
  -d '{"input":{"content":{"body":"And how does that compare to May?"}}}'
```

### Python

```python
import json
import httpx

def ask(question: str, session_id: str | None = None):
    headers = {"content-type": "application/json"}
    if session_id:
        headers["x-session-id"] = session_id
    body = {"input": {"content": {"body": question}}}

    with httpx.stream("POST", "http://127.0.0.1:8000/agent/chat",
                      headers=headers, json=body, timeout=180) as response:
        response.raise_for_status()
        session_id = response.headers.get("x-session-id", session_id)
        for line in response.iter_lines():          # already split on newlines
            if not line.strip():
                continue
            try:
                component = json.loads(line)
            except json.JSONDecodeError:
                continue                             # drop, keep reading
            if component["component"] == "done":
                break
            render(component)
    return session_id
```

### TypeScript / browser

```ts
const response = await fetch("/agent/chat", {
  method: "POST",
  headers: { "Content-Type": "application/json", ...(sid && { "x-session-id": sid }) },
  body: JSON.stringify({ input: { content: { body: question } } }),
});
sid = response.headers.get("x-session-id") ?? sid;

const reader = response.body!.getReader();
const decoder = new TextDecoder();
let buffer = "";

while (true) {
  const { value, done } = await reader.read();
  if (done) break;
  buffer += decoder.decode(value, { stream: true });
  const lines = buffer.split("\n");
  buffer = lines.pop()!;                 // last piece may be incomplete
  for (const line of lines) {
    if (!line.trim()) continue;
    try { render(JSON.parse(line)); } catch { /* drop, keep reading */ }
  }
}
```

The buffer matters: a chunk boundary can land mid-object, so hold the trailing
fragment until the next read completes it.

### Swift

```swift
var request = URLRequest(url: url)
request.httpMethod = "POST"
request.setValue("application/json", forHTTPHeaderField: "Content-Type")
sessionID.map { request.setValue($0, forHTTPHeaderField: "x-session-id") }
request.httpBody = try JSONEncoder().encode(ChatRequest(input: .init(content: .init(body: question))))

let (stream, response) = try await URLSession.shared.bytes(for: request)
sessionID = (response as? HTTPURLResponse)?.value(forHTTPHeaderField: "x-session-id") ?? sessionID

for try await line in stream.lines {                 // splits on newlines for you
    guard !line.isEmpty, let data = line.data(using: .utf8) else { continue }
    // Decode into an enum keyed on `component`, with an `.unsupported(raw)`
    // case so an unrecognised component renders as a placeholder.
    if let component = try? JSONDecoder().decode(ComponentLine.self, from: data) {
        await MainActor.run { render(component) }
    }
}
```

---

## Errors

| Status | When | Body |
| --- | --- | --- |
| `422` | Empty message, or longer than `PENNY_MAX_MESSAGE_CHARS` | FastAPI validation detail |
| `404` | `GET /agent/greeting` while disabled | `{"detail": "..."}` |
| `503` | Enriched dataset missing, or no model credentials | `{"detail": "..."}` — the message says how to fix it |

**A failure after the stream has started arrives as a component, not a status
code.** The headers are already sent by then, so the server emits
`try-again-error` and closes rather than truncating the body with no
explanation. Handle that case in your renderer, not in your HTTP error path.

## Scope

Penny answers questions about spending. She cannot move money, pay bills,
cancel a subscription, freeze a card or change any account setting — there is no
tool for any of it, so the limit is structural rather than a prompt
instruction. Requests of that kind get a declining `chat-response`.
