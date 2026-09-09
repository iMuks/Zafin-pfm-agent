"""The output boundary: the only shapes allowed to reach a client.

Penny does not stream tokens or markdown. She streams **components** — one
complete, self-describing UI object per line, tagged with a `component`
discriminator the client decodes polymorphically. A component paints the moment
it lands, so the first sentence of an answer is on screen while the chart
beneath it is still being generated.

The contract lives in the application layer, not in `presentation`, because it
is the use case's output data structure. HTTP renders it; a native iOS client
renders the same objects into SwiftUI views and Swift Charts; a test asserts on
them directly. None of those is privileged.
"""

from penny.application.components.contract import (
    CHART_COMPONENTS,
    CONTRACT,
    MODEL_COMPONENTS,
    SERVER_COMPONENTS,
    is_component_object,
    validate,
)
from penny.application.components.presenters import (
    done,
    smart_loading,
    transaction_list,
    try_again_error,
)

__all__ = [
    "CHART_COMPONENTS",
    "CONTRACT",
    "MODEL_COMPONENTS",
    "SERVER_COMPONENTS",
    "done",
    "is_component_object",
    "smart_loading",
    "transaction_list",
    "try_again_error",
    "validate",
]
