"""Who Penny is, and what she is allowed to say.

The prompt is assembled from parts rather than stored as one blob, and the
parts are ordered by how often they change:

    behavioral (stable) -> format (stable) -> data context (per dataset)

The stable prefix is large and byte-identical between requests, which is what
makes prompt caching work. The data context is computed from the repository, so
it can never drift from the data it describes.
"""

from penny.application.conversation.prompts import PromptAssembler

__all__ = ["PromptAssembler"]
