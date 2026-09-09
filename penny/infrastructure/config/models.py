"""The model registry and the per-purpose resolution chain.

Two ideas worth keeping from production systems that run several models at once:

1. **Models are named registry entries, not literals at call sites.** No module
   in this codebase spells out a model id. Switching models is a config change.
2. **The model is resolved per *purpose*, through a priority chain.** Chat and
   the greeting have genuinely different requirements — one reasons over tools,
   the other rephrases numbers it was handed — so they resolve independently and
   the cheap one stays cheap.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

from penny.infrastructure.config.settings import Effort, settings

Purpose = Literal["chat", "greeting", "judge", "analyst"]


class ModelEntry(BaseModel):
    """One model this application is permitted to call."""

    # "model_" is a pydantic-protected prefix; these are domain names.
    model_config = ConfigDict(protected_namespaces=())

    model_id: str
    max_tokens: int = 8000
    effort: Effort = "medium"
    #: Not every model accepts `output_config.effort`. Haiku 4.5 rejects it with
    #: a 400, so the capability belongs to the registry entry rather than to
    #: every call site that might reach for it.
    supports_effort: bool = True


#: Anthropic first-party model ids. A bank would reach the same family through
#: Bedrock inference profiles (us.anthropic.claude-*); this prototype calls the
#: Anthropic API directly, which the assignment explicitly permits.
MODEL_REGISTRY: dict[str, ModelEntry] = {
    "opus5": ModelEntry(model_id="claude-opus-5", max_tokens=8000, effort="high"),
    "sonnet5": ModelEntry(model_id="claude-sonnet-5", max_tokens=8000, effort="medium"),
    "haiku45": ModelEntry(
        model_id="claude-haiku-4-5", max_tokens=4000, effort="low", supports_effort=False
    ),
}

DEFAULT_KEY = "sonnet5"


def resolve_model(purpose: Purpose, override: str | None = None) -> ModelEntry:
    """Pick the model for this call.

    Priority, highest first: an explicit per-request override, then the
    purpose's configured default. An unknown key degrades to the purpose default
    rather than raising — a typo in a deploy variable should change which model
    answers, not take the endpoint down.
    """
    cfg = settings()
    per_purpose: dict[str, tuple[str, Effort]] = {
        "greeting": (cfg.greeting_model_key, cfg.greeting_effort),
        "judge": (cfg.judge_model_key, cfg.judge_effort),
        "analyst": (cfg.analyst_model_key, cfg.analyst_effort),
    }
    default_key, effort = per_purpose.get(purpose, (cfg.model_key, cfg.effort))

    entry = (
        MODEL_REGISTRY.get(override or "")
        or MODEL_REGISTRY.get(default_key)
        or MODEL_REGISTRY[DEFAULT_KEY]
    )
    return entry.model_copy(update={"effort": effort})
