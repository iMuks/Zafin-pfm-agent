"""Deterministic analytics — every number Penny is allowed to state.

The division of labour in this agent is strict and it is the reason its
arithmetic can be trusted: **the model decides which question to ask; Python
answers it.** Claude chooses a tool, a month and a category, then phrases the
result. It never sums an amount, computes a percentage or ranks a list, because
a language model doing arithmetic token-by-token is precisely where financial
assistants produce confidently wrong totals.

Each module here is one family of questions, and each is a plain class over a
`TransactionRepository`. They are unit-testable with a list of transactions and
no framework at all.
"""

from penny.application.insights.service import InsightService

__all__ = ["InsightService"]
