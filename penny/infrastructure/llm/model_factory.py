"""The one place a chat-model object is constructed.

Callers ask for a *purpose* — "chat", "greeting" — and the registry decides
which model and what reasoning effort. No call site anywhere in this codebase
names a model id, so changing models is a config change rather than a code
change.

Three providers exist. `anthropic` calls the Anthropic API. `local` calls an
OpenAI-compatible endpoint that runs inside the deployment (Ollama on a laptop,
vLLM in the VPC). `bedrock` calls Claude through Amazon Bedrock in the founder's
own AWS account, through a VPC interface endpoint, with Bedrock Guardrails on
every prompt and response. The enclave guards refuse any configuration that
would send a request outside that boundary.
"""

from __future__ import annotations

from langchain_anthropic import ChatAnthropic
from langchain_core.language_models import BaseChatModel
from langchain_openai import ChatOpenAI

from penny.infrastructure.config.models import (
    ModelEntry,
    Purpose,
    enforce_bedrock_enclave,
    enforce_enclave,
    resolve_model,
)
from penny.infrastructure.config.settings import settings


def chat_model(
    purpose: Purpose, override: str | None = None, *, thinking: bool = True
) -> BaseChatModel:
    """Build the model for a purpose.

    `thinking=False` is required whenever the caller wraps this in
    `with_structured_output`: LangChain implements structured output as forced
    tool calling, which is not guaranteed while thinking is enabled — the call
    then raises instead of returning a parsed object. Current models think by
    default, so "off" has to be stated explicitly rather than omitted.

    For the local provider the flag is accepted and ignored: reasoning is a
    property of the served model, and Ollama returns it in a separate field, so
    it never reaches the JSONL buffer either way.
    """
    entry = resolve_model(purpose, override)
    if entry.provider == "local":
        return _local_model(entry)
    if entry.provider == "bedrock":
        return _bedrock_model(entry, thinking=thinking)
    return ChatAnthropic(
        model=entry.model_id,
        max_tokens=entry.max_tokens,
        # Note the absence of `temperature` — current Anthropic models reject
        # sampling parameters, and ChatAnthropic omits it when unset.
        thinking={"type": "adaptive"} if thinking else {"type": "disabled"},
        # `output_config` has no first-class field on ChatAnthropic yet, so it
        # travels via model_kwargs. Sent only to models that accept it.
        model_kwargs=({"output_config": {"effort": entry.effort}} if entry.supports_effort else {}),
        streaming=True,
        stream_usage=True,
    )


def _bedrock_model(entry: ModelEntry, *, thinking: bool) -> BaseChatModel:
    """Claude on Bedrock, Converse API, inside the VPC, Guardrails on.

    Credentials come from the ECS task role (or a local AWS profile for
    fixtures), never from an API key. Extended thinking is a model request
    field on Converse rather than a first-class parameter, and structured
    output (`thinking=False`) must not enable it, for the same reason as the
    Anthropic branch.
    """
    # Imported lazily: boto3 and langchain-aws are deployment dependencies and
    # importing the package must not require them for the local providers.
    from langchain_aws import ChatBedrockConverse

    cfg = settings()
    enforce_bedrock_enclave(
        cfg.bedrock_endpoint_url,
        cfg.bedrock_region,
        allow_public_endpoint=cfg.bedrock_allow_public_endpoint,
    )
    guardrail = (
        {
            "guardrailIdentifier": cfg.bedrock_guardrail_id,
            "guardrailVersion": cfg.bedrock_guardrail_version,
            "trace": "enabled",
        }
        if cfg.bedrock_guardrail_id
        else None
    )
    extra = (
        {"thinking": {"type": "enabled", "budget_tokens": cfg.bedrock_thinking_budget_tokens}}
        if thinking
        else None
    )
    return ChatBedrockConverse(
        model=entry.model_id,
        region_name=cfg.bedrock_region,
        endpoint_url=cfg.bedrock_endpoint_url or None,
        max_tokens=entry.max_tokens,
        guardrail_config=guardrail,
        additional_model_request_fields=extra,
    )


def _local_model(entry: ModelEntry) -> ChatOpenAI:
    cfg = settings()
    enforce_enclave(
        entry.model_id, cfg.local_base_url, allow_public_host=cfg.local_allow_public_host
    )
    return ChatOpenAI(
        model=entry.model_id,
        base_url=cfg.local_base_url,
        api_key=cfg.local_api_key,
        max_tokens=entry.max_tokens,
        # Deterministic sampling: every number Penny quotes is a tool result,
        # and the model's only job is to phrase and to pick tools consistently.
        temperature=0,
        # A stuck local server must fail fast and visibly. The OpenAI client's
        # defaults (10-minute timeout, two retries) turn one bad call into half
        # an hour of silence, which is how an eval run lost an hour of work.
        timeout=cfg.local_timeout_seconds,
        max_retries=1,
        streaming=True,
        stream_usage=True,
    )
