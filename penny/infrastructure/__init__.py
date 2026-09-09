"""Layer 3 — adapters.

Everything the application layer declared as a port is implemented here: the
JSON transaction repository, the in-memory session store, the Anthropic client,
the LangGraph runtime, OpenTelemetry, the audit file. Each is replaceable
without touching a use case, which is the whole return on the port abstraction.
"""
