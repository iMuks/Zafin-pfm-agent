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

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict

from penny.infrastructure.config.settings import Effort, settings

Purpose = Literal["chat", "greeting", "judge", "analyst"]

#: Where a model runs. `anthropic` calls the Anthropic API. `local` calls an
#: OpenAI-compatible endpoint inside the deployment (Ollama on a laptop, vLLM in
#: the VPC); nothing leaves the boundary, which is the product's privacy rule.
#: `bedrock` calls Claude through Amazon Bedrock inside the founder's own AWS
#: account, reached only through a VPC interface endpoint.
Provider = Literal["anthropic", "local", "bedrock"]


class ModelEntry(BaseModel):
    """One model this application is permitted to call."""

    # "model_" is a pydantic-protected prefix; these are domain names.
    model_config = ConfigDict(protected_namespaces=())

    model_id: str
    provider: Provider = "anthropic"
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
    # Open-weights models served locally. Ids are Ollama tags; the same entries
    # work against vLLM when the served model is registered under the same name.
    # Effort is not a parameter these endpoints accept; reasoning is a property
    # of the model (qwen3 thinks by default and returns it separately).
    "local-qwen3-4b": ModelEntry(
        model_id="qwen3:4b", provider="local", max_tokens=4000, effort="low", supports_effort=False
    ),
    "local-qwen3-8b": ModelEntry(
        model_id="qwen3:8b", provider="local", max_tokens=4000, effort="low", supports_effort=False
    ),
    "local-qwen25-7b": ModelEntry(
        model_id="qwen2.5:7b",
        provider="local",
        max_tokens=4000,
        effort="low",
        supports_effort=False,
    ),
    # Claude on Bedrock in the founder's account. The inference profile ids come
    # from settings because they are fixed by the region and the model-access
    # grant, not by this code. Effort is not a Converse parameter.
    "bedrock-sonnet": ModelEntry(
        model_id=settings().bedrock_chat_model_id,
        provider="bedrock",
        max_tokens=8000,
        effort="medium",
        supports_effort=False,
    ),
    "bedrock-haiku": ModelEntry(
        model_id=settings().bedrock_small_model_id,
        provider="bedrock",
        max_tokens=4000,
        effort="low",
        supports_effort=False,
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


# -- enclave guard ----------------------------------------------------------

_PRIVATE_SUFFIXES = (".local", ".internal", ".svc", ".cluster.local")


def _host_is_private(url: str) -> bool:
    """True when the URL points at this machine or a private network.

    Loopback, RFC 1918 and link-local addresses, `localhost`, and the DNS
    suffixes cloud providers use for in-VPC services. Anything else is treated
    as public and refused unless the operator overrides it explicitly.
    """
    import ipaddress
    from urllib.parse import urlparse

    host = (urlparse(url).hostname or "").lower()
    if not host:
        return False
    if host == "localhost" or host.endswith(_PRIVATE_SUFFIXES):
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return address.is_loopback or address.is_private or address.is_link_local


_BEDROCK_VPCE = re.compile(r"^vpce-[a-z0-9-]+\.bedrock-runtime\.(?P<region>[a-z0-9-]+)\.vpce\.amazonaws\.com$")


def enforce_bedrock_enclave(
    endpoint_url: str, region: str, *, allow_public_endpoint: bool = False
) -> None:
    """Refuse a Bedrock configuration that would leave the VPC.

    The runtime endpoint must be the interface endpoint's own DNS name for the
    configured region. The public `bedrock-runtime.<region>.amazonaws.com` name
    is refused even though private DNS can resolve it inside the VPC: the guard
    must not depend on DNS state to keep customer data on the private path.
    """
    from urllib.parse import urlparse

    from penny.domain.errors import RemoteModelForbiddenError

    if allow_public_endpoint:
        return
    host = (urlparse(endpoint_url).hostname or "").lower()
    if not host:
        raise RemoteModelForbiddenError(
            "PENNY_BEDROCK_ENDPOINT_URL is not set; Bedrock must be reached through the VPC "
            "interface endpoint (vpce-....bedrock-runtime.<region>.vpce.amazonaws.com)"
        )
    match = _BEDROCK_VPCE.match(host)
    if match is None:
        raise RemoteModelForbiddenError(
            f"Bedrock endpoint {host!r} is not a VPC interface endpoint DNS name"
        )
    if match.group("region") != region:
        raise RemoteModelForbiddenError(
            f"Bedrock endpoint {host!r} is in region {match.group('region')!r}, "
            f"not the configured {region!r}"
        )


def enforce_enclave(model_id: str, base_url: str, *, allow_public_host: bool = False) -> None:
    """Refuse any local-provider configuration that would leave the boundary."""
    from penny.domain.errors import RemoteModelForbiddenError

    if ":cloud" in model_id or model_id.endswith("-cloud"):
        raise RemoteModelForbiddenError(
            f"{model_id!r} is a hosted model that runs on the vendor's servers"
        )
    if not allow_public_host and not _host_is_private(base_url):
        raise RemoteModelForbiddenError(
            f"endpoint {base_url!r} is not a loopback or private-network host "
            "(set PENNY_LOCAL_ALLOW_PUBLIC_HOST=true only for a private endpoint "
            "reached through a public hostname)"
        )
