"""The tool surface Claude is allowed to call.

Two properties matter more than anything else in this package:

* **One definition per tool.** A tool's JSON schema and the function it invokes
  are the same object (`ToolSpec`). In the previous design they were a list and
  a dict in separate places, which is a drift bug waiting to happen — a renamed
  parameter passes review in one file and breaks silently in the other.

* **There is no tool that changes anything.** The assignment scopes this agent
  to insights: no payments, no card management, no account actions. That is
  enforced here, structurally, by never defining such a tool. A prompt can be
  argued with; an absent capability cannot.
"""

from penny.application.tools.catalog import build_catalog
from penny.application.tools.registry import ToolRegistry, ToolSpec

__all__ = ["ToolRegistry", "ToolSpec", "build_catalog"]
