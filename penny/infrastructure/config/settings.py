"""Environment-driven settings, as one typed object.

A single place to see every knob, with defaults and validation, rather than
`os.getenv` calls scattered across twenty modules. Anything tunable at deploy
time appears below; anything that is a design decision does not.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

Effort = Literal["low", "medium", "high", "xhigh", "max"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="PENNY_", env_file=".env", extra="ignore", protected_namespaces=()
    )

    # --- model selection -------------------------------------------------
    model_key: str = Field(default="sonnet5", description="Registry key for the chat agent.")
    greeting_model_key: str = Field(default="haiku45", description="Registry key for the opener.")
    #: Evaluation roles. A cheap judge scores every answer; a stronger analyst
    #: re-reads only the low scores, which is what separates a grader mistake
    #: from a real defect without paying for the stronger model on every case.
    judge_model_key: str = Field(default="haiku45", description="Registry key for the eval judge.")
    analyst_model_key: str = Field(
        default="opus5", description="Registry key for the eval analyst."
    )
    effort: Effort = "medium"
    # The opener only phrases numbers Python already computed, so it needs no
    # reasoning depth. Low effort keeps first paint fast and cheap.
    greeting_effort: Effort = "low"
    judge_effort: Effort = "low"
    analyst_effort: Effort = "medium"

    # --- local model endpoint (provider = "local") ------------------------
    #: OpenAI-compatible endpoint inside the deployment. Ollama's default on a
    #: laptop; a vLLM service address in the VPC. Must be loopback or private.
    local_base_url: str = Field(
        default="http://127.0.0.1:11434/v1",
        description="OpenAI-compatible endpoint for local-provider models.",
    )
    #: The client library requires a key; local servers ignore it.
    local_api_key: str = Field(default="local", description="Placeholder key for local servers.")
    local_timeout_seconds: float = Field(
        default=300.0, gt=0, description="Per-call timeout for the local endpoint."
    )
    local_allow_public_host: bool = Field(
        default=False,
        description="Permit a non-private hostname for the local endpoint (private link via DNS).",
    )

    # --- Bedrock (provider = "bedrock") ----------------------------------
    #: Claude through Amazon Bedrock inside the founder's own account. The
    #: runtime endpoint must be the VPC interface endpoint's DNS name
    #: (vpce-....bedrock-runtime.<region>.vpce.amazonaws.com); the public
    #: bedrock-runtime hostname is refused by the enclave guard.
    bedrock_region: str = Field(default="ca-central-1", description="Region of the Bedrock endpoint.")
    bedrock_endpoint_url: str = Field(
        default="", description="VPC interface endpoint URL for bedrock-runtime (required in deployment)."
    )
    bedrock_allow_public_endpoint: bool = Field(
        default=False,
        description="Permit the public bedrock-runtime hostname. Never in deployment; fixtures only.",
    )
    bedrock_guardrail_id: str = Field(default="", description="Bedrock Guardrails identifier.")
    bedrock_guardrail_version: str = Field(default="DRAFT", description="Bedrock Guardrails version.")
    bedrock_chat_model_id: str = Field(
        default="ca.anthropic.claude-sonnet-4-5-20250929-v1:0",
        description="Bedrock inference profile id for the chat and analyst roles.",
    )
    bedrock_small_model_id: str = Field(
        default="ca.anthropic.claude-haiku-4-5-20251001-v1:0",
        description="Bedrock inference profile id for the greeting and judge roles.",
    )
    bedrock_thinking_budget_tokens: int = Field(
        default=2048, ge=1024, description="Extended-thinking budget for Bedrock chat calls."
    )

    # --- authentication --------------------------------------------------
    #: "none" keeps the development identity (fixtures only). "cognito" verifies
    #: a Cognito JWT on every API request and returns 401 without one.
    auth_mode: Literal["none", "cognito"] = "none"
    cognito_region: str = Field(default="ca-central-1", description="Region of the user pool.")
    cognito_user_pool_id: str = Field(default="", description="Cognito user pool id.")
    cognito_app_client_id: str = Field(default="", description="Public PKCE app client id.")
    jwks_refresh_min_seconds: float = Field(
        default=60.0, gt=0, description="Minimum interval between JWKS refreshes on an unknown kid."
    )

    # --- guards ----------------------------------------------------------
    max_model_calls: int = Field(
        default=8, ge=1, le=25, description="Model calls per request before the agent must wrap up."
    )
    max_message_chars: int = Field(default=2000, ge=1, description="Longest accepted question.")

    # --- session ---------------------------------------------------------
    session_ttl_seconds: int = Field(
        default=1800, ge=60, description="30 minutes, fixed at creation, never refreshed."
    )
    max_history_turns: int = Field(
        default=20, ge=2, description="Turns replayed into the model on each request."
    )

    # --- feature flags ---------------------------------------------------
    enable_greeting: bool = True
    enable_audit_log: bool = True

    # --- data ------------------------------------------------------------
    enriched_path: str = Field(
        default="data/transactions_enriched.json",
        description="Enriched dataset the repository reads. Overridable so tests and "
        "the component gallery can point at a fixture.",
    )

    # --- prompts ---------------------------------------------------------
    prompt_version: str = "v1.0"

    # --- observability ---------------------------------------------------
    otel_console: bool = Field(default=False, description="Print OpenTelemetry spans to stdout.")
    audit_log_path: str = Field(default="data/audit_log.jsonl", description="Append-only trail.")
    log_level: str = "INFO"


@lru_cache(maxsize=1)
def settings() -> Settings:
    return Settings()
