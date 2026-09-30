"""The one place a chat-model object is constructed.

Callers ask for a *purpose* — "chat", "greeting" — and the registry decides
which model and what reasoning effort. No call site anywhere in this codebase
names a model id, so changing models is a config change rather than a code
change.

Two providers exist. `anthropic` calls the Anthropic API. `local` calls an
OpenAI-compatible endpoint that runs inside the deployment (Ollama on a laptop,
vLLM in the VPC); the enclave guard refuses any configuration that would send a
request outside that boundary.
"""

from __future__ import annotations

from langchain_anthropic import ChatAnthropic
from langchain_core.language_models import BaseChatModel
from langchain_openai import ChatOpenAI

from penny.infrastructure.config.models import ModelEntry, Purpose, enforce_enclave, resolve_model
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
