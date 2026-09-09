"""Observability: OpenTelemetry spans, structured logs, and stream metrics.

Two layers, because they answer different questions:

* **Tracing** — real OTel spans. No collector runs in a prototype, so the
  exporter is off unless `PENNY_OTEL_CONSOLE=true`. The instrumentation is
  genuine either way; only the exporter changes, which is exactly what
  deploying this behind a real collector would involve.

* **Stream telemetry** — the numbers that explain a streaming agent and that a
  generic HTTP latency metric cannot see:

      TTFT   time to the model's first token
      TTFC   time to the first *complete component* — what the customer waits for
      tool durations, per tool
      dropped lines — model output that failed the contract
      empty response — a turn that produced nothing renderable, which is a
                       silent failure unless something explicitly looks for it
"""

from __future__ import annotations

import json
import logging
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import ConsoleSpanExporter, SimpleSpanProcessor

from penny.infrastructure.config.settings import settings
from penny.infrastructure.observability.context import get_context

_LOGGER_NAME = "penny"
_ready = False


class JsonFormatter(logging.Formatter):
    """Structured, PII-free log lines — request-correlated and greppable.

    Note what is *not* logged: no merchant names, no amounts, no question text.
    Those live in the audit trail, which has access controls; the operational
    log is read by engineers debugging latency and must not become a second,
    unregulated copy of customer financial data.
    """

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created)),
            "level": record.levelname,
            "event": record.getMessage(),
        }
        ctx = get_context()
        if ctx:
            payload["request_id"] = ctx.request_id
            payload["poid"] = ctx.poid
            if ctx.session_id:
                payload["session_id"] = ctx.session_id
        extra = getattr(record, "fields", None)
        if extra:
            payload.update(extra)
        return json.dumps(payload, default=str)


def setup() -> None:
    """Idempotent: configure the logger and tracer provider exactly once."""
    global _ready
    if _ready:
        return

    cfg = settings()
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    logger = logging.getLogger(_LOGGER_NAME)
    logger.handlers = [handler]
    logger.setLevel(cfg.log_level)
    logger.propagate = False

    provider = TracerProvider()
    if cfg.otel_console:
        provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))
    trace.set_tracer_provider(provider)
    _ready = True


def log(event: str, **fields: Any) -> None:
    # Self-initialising: a log line emitted before the app has configured
    # logging would otherwise be dropped by the root logger's default level,
    # which hides exactly the startup and script paths worth seeing.
    setup()
    logging.getLogger(_LOGGER_NAME).info(event, extra={"fields": fields})


def tracer() -> trace.Tracer:
    setup()
    return trace.get_tracer("penny")


@contextmanager
def span(name: str, **attributes: Any) -> Iterator[trace.Span]:
    ctx = get_context()
    with tracer().start_as_current_span(name) as current:
        if ctx:
            current.set_attribute("penny.request_id", ctx.request_id)
            if ctx.session_id:
                current.set_attribute("penny.session_id", ctx.session_id)
        for key, value in attributes.items():
            if value is not None:
                current.set_attribute(f"penny.{key}", value)
        yield current


@dataclass
class StreamTelemetry:
    """One turn's stream measurements, emitted once when the turn ends."""

    started: float = field(default_factory=time.perf_counter)
    ttft_ms: float | None = None
    ttfc_ms: float | None = None
    components: int = 0
    component_types: dict[str, int] = field(default_factory=dict)
    tool_calls: int = 0
    tool_ms: dict[str, float] = field(default_factory=dict)
    dropped_lines: int = 0
    model_id: str | None = None

    def _elapsed(self) -> float:
        return (time.perf_counter() - self.started) * 1000

    def first_token(self) -> None:
        if self.ttft_ms is None:
            self.ttft_ms = self._elapsed()

    def component(self, name: str) -> None:
        if self.ttfc_ms is None:
            self.ttfc_ms = self._elapsed()
        self.components += 1
        self.component_types[name] = self.component_types.get(name, 0) + 1

    def tool(self, name: str, duration_ms: float) -> None:
        self.tool_calls += 1
        self.tool_ms[name] = round(self.tool_ms.get(name, 0.0) + duration_ms, 2)

    @property
    def empty_response(self) -> bool:
        """No renderable component reached the client — otherwise a silent failure."""
        return self.components == 0

    def summary(self) -> dict[str, Any]:
        return {
            "ttft_ms": round(self.ttft_ms, 1) if self.ttft_ms else None,
            "ttfc_ms": round(self.ttfc_ms, 1) if self.ttfc_ms else None,
            "components": self.components,
            "tool_calls": self.tool_calls,
        }

    def emit(self, outcome: str) -> None:
        log(
            "stream.complete",
            outcome=outcome,
            model_id=self.model_id,
            total_ms=round(self._elapsed(), 1),
            component_types=self.component_types,
            tool_ms=self.tool_ms,
            dropped_lines=self.dropped_lines,
            empty_response=self.empty_response,
            **self.summary(),
        )
