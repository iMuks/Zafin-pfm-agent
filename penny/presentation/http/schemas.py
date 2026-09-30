"""Request and response schemas.

The production contract nests the message as `{"input": {"content": {"body": …}}}`.
Flatter shapes are accepted too, so that a `curl` one-liner is not a puzzle —
but the nested form is what the real client sends, so it is what the validator
is built around.

There is deliberately **no conversation history in the request body.** History
lives server-side, keyed by session, so a client cannot rewrite it — which means
it cannot forge an earlier turn in which Penny appeared to agree to something.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from penny.infrastructure.config.settings import settings


class ChatRequest(BaseModel):
    message: str = Field(default="")
    session_id: str | None = None

    @model_validator(mode="before")
    @classmethod
    def unwrap(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data

        payload = dict(data)
        raw_input = payload.get("input")
        content = raw_input.get("content") if isinstance(raw_input, dict) else None

        if isinstance(content, dict):
            payload["message"] = content.get("body", "")
        elif isinstance(content, list) and content:
            first = content[0]
            payload["message"] = first.get("body", "") if isinstance(first, dict) else str(first)

        payload["message"] = (
            payload.get("message") or payload.get("query") or payload.get("body") or ""
        )
        return payload

    @model_validator(mode="after")
    def check_message(self) -> ChatRequest:
        text = self.message.strip()
        if not text:
            raise ValueError("message must not be empty")

        limit = settings().max_message_chars
        if len(text) > limit:
            raise ValueError(f"message must be {limit} characters or fewer")

        object.__setattr__(self, "message", text)
        return self


# ---------------------------------------------------------------- responses
#
# The models below exist so `/openapi.json` carries a real schema for every
# line a client can receive, which is what makes the API generatable into a
# typed client. They mirror `application/components/contract.py`; the two are
# kept from drifting by a test that compares the discriminators against
# CONTRACT, rather than by anyone remembering to update both.


class CoverageSummary(BaseModel):
    """What the dataset contains — and, in `note`, what it does not."""

    # Permissive on purpose: a new insight field should extend the payload, not
    # break readiness with a validation error.
    model_config = ConfigDict(extra="allow")

    first_date: str
    latest_date: str
    treat_as_today: str
    months: list[str]
    current_month: str
    last_complete_month: str | None = None
    partial_month: str | None = None
    transaction_count: int
    categories_present: list[str]
    merchants_present: int
    total_spend: float
    note: str


class HealthResponse(BaseModel):
    status: Literal["ok"]
    model: str = Field(description="Resolved model id for the chat agent.")
    provider: Literal["anthropic", "local", "bedrock"] = Field(
        description="Where the chat model runs: the Anthropic API, an endpoint inside the "
        "deployment, or Claude on Bedrock in the operator's own AWS account."
    )
    endpoint: str | None = Field(
        default=None, description="The in-deployment endpoint in use for local and bedrock."
    )
    auth_mode: Literal["none", "cognito"] = Field(description="How requests are authenticated.")
    jwks: str = Field(description="Sign-in key set: ok, unavailable, or n/a without a verifier.")
    effort: str = Field(description="Reasoning effort in force for chat.")
    prompt_version: str = Field(description="Stamped into every audit record.")
    api_key_configured: bool
    greeting_enabled: bool
    tools: list[str] = Field(description="Tool names the agent may call.")
    data: CoverageSummary

    model_config = ConfigDict(protected_namespaces=())


class MerchantCard(BaseModel):
    merchant: str
    amount: float
    subtitle: str
    logo_domain: str | None = None


class ChartPoint(BaseModel):
    label: str = Field(description="X-axis label for this point.")
    values: list[float] = Field(
        description="One value per series in `labels`. A pie slice carries exactly one, 0-100."
    )


class ChatResponseLine(BaseModel):
    component: Literal["chat-response"]
    body: str


class TableLine(BaseModel):
    component: Literal["table"]
    headers: list[str]
    data: list[list[str]] = Field(description="Every row has exactly as many cells as `headers`.")


class OrderedListLine(BaseModel):
    component: Literal["ordered-list"]
    items: list[str]


class UnorderedListLine(BaseModel):
    component: Literal["unordered-list"]
    items: list[str]


class LineChartLine(BaseModel):
    component: Literal["line-chart"]
    valueTitle: str  # noqa: N815 — wire field name, fixed by the contract
    labels: list[str]
    data: list[ChartPoint]


class BarChartLine(BaseModel):
    component: Literal["bar-chart"]
    valueTitle: str  # noqa: N815
    labels: list[str]
    data: list[ChartPoint]


class PieChartLine(BaseModel):
    component: Literal["pie-chart"]
    valueTitle: str  # noqa: N815
    labels: list[str]
    data: list[ChartPoint] = Field(description="One value per slice, each a percentage 0-100.")


class SuggestedIntentsLine(BaseModel):
    component: Literal["suggested-user-intents"]
    intents: list[str] = Field(description="Short follow-ups the customer could tap next.")


class TransactionListLine(BaseModel):
    component: Literal["transaction-list"]
    transactions: list[MerchantCard]


class SmartLoadingLine(BaseModel):
    component: Literal["smart-loading"]
    body: str = Field(description="Human-readable label for the work in progress.")
    tool: str | None = Field(default=None, description="Tool being executed, when applicable.")


class TryAgainErrorLine(BaseModel):
    component: Literal["try-again-error"]
    body: str


class NoticeLine(BaseModel):
    """A fixed server line when a safety filter stopped the turn. Not an error."""

    component: Literal["notice"]
    body: str


class FeedbackLine(BaseModel):
    component: Literal["feedback"]


class DoneLine(BaseModel):
    component: Literal["done"]
    telemetry: dict[str, Any] | None = Field(
        default=None, description="Timings for this turn: ttft_ms, ttfc_ms, components, tool_calls."
    )


class UnsupportedLine(BaseModel):
    """A line that failed contract validation, or a component this client is too old to know.

    Rendered as a visible placeholder rather than dropped: a silently discarded
    component would hide a client/server version mismatch.
    """

    component: Literal["unsupported"]
    reason: str | None = None
    raw: dict[str, Any] | None = None


#: One line of the streamed body. Clients dispatch on `component`.
ComponentLine = Annotated[
    ChatResponseLine
    | TableLine
    | OrderedListLine
    | UnorderedListLine
    | LineChartLine
    | BarChartLine
    | PieChartLine
    | SuggestedIntentsLine
    | TransactionListLine
    | SmartLoadingLine
    | TryAgainErrorLine
    | NoticeLine
    | FeedbackLine
    | DoneLine
    | UnsupportedLine,
    Field(discriminator="component"),
]
