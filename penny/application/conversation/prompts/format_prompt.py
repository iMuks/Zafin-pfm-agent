"""Part 2 — the wire format and the component library.

Shared verbatim by the chat agent and the greeting, which is the point: one
definition of the component contract for every caller. Byte-stable, so it stays
inside the cached prefix.

The assertion at the bottom is a guard against the failure mode this file is
most prone to: the prompt advertising a component the validator would reject,
which shows up as a mysterious `unsupported` placeholder in the UI rather than
as an error anyone can trace.
"""

from penny.application.components.contract import MODEL_COMPONENTS

FORMAT = """# Output format — THIS IS STRICT
Reply with newline-delimited JSON: one complete JSON object per line, and nothing \
else. No markdown, no code fences, no prose outside the objects. Every object has \
a "component" key. Emit them in reading order; each is rendered the instant it \
arrives.

{"component":"chat-response","body":"You spent $412.80 on dining in June, up 18% \
from May."}
{"component":"table","headers":["Merchant","Visits","Total"],"data":[["Shake \
Shack","4","$88.20"],["Starbucks","3","$21.40"]]}
{"component":"bar-chart","valueTitle":"Spend ($)","labels":["Dining"],"data":\
[{"label":"Apr","values":[318.4]},{"label":"May","values":[349.1]},\
{"label":"Jun","values":[412.8]}]}
{"component":"pie-chart","valueTitle":"Share of spend (%)","labels":["Share"],\
"data":[{"label":"Groceries","values":[34.2]},{"label":"Dining","values":[21.8]}]}
{"component":"line-chart","valueTitle":"Spend ($)","labels":["Total"],"data":\
[{"label":"Feb","values":[2210.5]},{"label":"Mar","values":[1980.2]}]}
{"component":"ordered-list","items":["Groceries — $1,204.60","Dining — $412.80"]}
{"component":"unordered-list","items":["Netflix charged twice within three days"]}
{"component":"suggested-user-intents","intents":["Break that down by merchant",\
"Compare to May","Show me the transactions"]}

Rules:
- Escape every double quote and newline inside a string value. A malformed object \
is dropped, and the customer loses that part of your answer.
- Start with a `chat-response` carrying the actual answer.
- Add a visual ONLY when it earns its place: `bar-chart` to compare a handful of \
periods or categories, `line-chart` for a trend across three or more months, \
`pie-chart` for shares of one total (values are percentages 0-100, exactly one per \
slice), `table` for two or more columns of detail, a list for ranked points. One or \
two visuals per answer at most. A simple question deserves a single \
`chat-response` and nothing else.
- Charts: `labels` names the series (usually one); each `data` entry is one point, \
{"label": <x-axis label>, "values": [<number>]}. Numbers, not strings, and never a \
currency symbol inside a chart value.
- Tables: every row must have exactly as many cells as there are headers.
- End every turn with `suggested-user-intents` — two or three short follow-ups the \
customer could tap, phrased the way they would say them.
- Do NOT emit a component listing individual transactions or subscriptions. The app \
renders those as merchant cards with logos automatically, straight from the tool \
result. Summarise them in your `chat-response` instead of retyping the rows."""

# The examples above must only use components the validator accepts from the
# model. This fails at import time rather than silently at render time.
_ADVERTISED = frozenset(
    {
        "chat-response",
        "table",
        "bar-chart",
        "pie-chart",
        "line-chart",
        "ordered-list",
        "unordered-list",
        "suggested-user-intents",
    }
)
assert _ADVERTISED <= MODEL_COMPONENTS, (
    f"The format prompt advertises components the contract rejects: "
    f"{sorted(_ADVERTISED - MODEL_COMPONENTS)}"
)
