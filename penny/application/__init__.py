"""Layer 2 — application business rules.

Use cases live here, expressed entirely against the interfaces in
`penny.application.ports`. Nothing in this layer knows that transactions
arrive as JSON, that the model is Claude, or that the delivery mechanism is
HTTP — which is what makes every one of those replaceable.
"""
