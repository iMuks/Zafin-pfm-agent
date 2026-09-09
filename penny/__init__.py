"""Penny — a conversational Personal Finance Management agent.

The package is organised as concentric layers. The dependency rule is absolute
and enforced by `tests/architecture/test_dependency_rule.py`: **source code
dependencies point inward only.**

    presentation ─┐
    infrastructure┼─► application ─► domain
    composition ──┘

  domain          Enterprise rules. What a transaction is, what counts as
                  spending, the category vocabulary. No I/O, no frameworks,
                  no imports outside the standard library.
  application     Use cases and the ports they need. Every insight Penny can
                  state lives here, expressed against interfaces.
  infrastructure  Adapters that satisfy those ports: the JSON repository, the
                  Anthropic client, LangGraph, OpenTelemetry.
  presentation    Delivery. FastAPI routes and the streamed component contract.
  composition     The composition root — the only module allowed to name a
                  concrete implementation.
"""

__version__ = "1.0.0"
