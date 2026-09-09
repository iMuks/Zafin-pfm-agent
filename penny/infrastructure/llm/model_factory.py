"""The one place a chat-model object is constructed.

Callers ask for a *purpose* — "chat", "greeting" — and the registry decides
which model and what reasoning effort. No call site anywhere in this codebase
names a model id, so changing models is a config change rather than a code
change.
"""

from __future__ import annotations

from langchain_anthropic import ChatAnthropic

from penny.infrastructure.config.models import Purpose, resolve_model


def chat_model(
    purpose: Purpose, override: str | None = None, *, thinking: bool = True
) -> ChatAnthropic:
    """Build the model for a purpose.

    `thinking=False` is required whenever the caller wraps this in
    `with_structured_output`: LangChain implements structured output as forced
    tool calling, which is not guaranteed while thinking is enabled — the call
    then raises instead of returning a parsed object. Current models think by
    default, so "off" has to be stated explicitly rather than omitted.
    """
    entry = resolve_model(purpose, override)
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
