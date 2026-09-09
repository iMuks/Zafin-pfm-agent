"""Tool definition, validation and dispatch."""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class ToolSpec:
    """One tool: how the model sees it, and what it actually runs.

    `description` is written for the model, not for a developer. It is the only
    thing standing between a good tool choice and a bad one, so it says *when*
    to reach for the tool and what the result means — not how it is implemented.
    """

    name: str
    description: str
    input_schema: Mapping[str, Any]
    handler: Callable[..., dict[str, Any]]

    @property
    def properties(self) -> Mapping[str, Any]:
        return self.input_schema.get("properties", {})

    @property
    def required(self) -> Sequence[str]:
        return self.input_schema.get("required", ())


class ToolRegistry:
    """Holds the catalog and is the only place a tool is executed.

    Validation happens here rather than inside each handler so that every tool
    gets the same treatment, and so the failure is described in the model's
    vocabulary ("that enum value isn't valid, here are the ones that are")
    rather than as a Python TypeError.
    """

    def __init__(self, specs: Sequence[ToolSpec]) -> None:
        duplicates = {s.name for s in specs if [x.name for x in specs].count(s.name) > 1}
        if duplicates:
            raise ValueError(f"Duplicate tool names in catalog: {sorted(duplicates)}")
        # Insertion order is preserved and is part of the contract: a tool list
        # that reorders between requests changes the serialised prompt prefix
        # and silently destroys prompt caching.
        self._specs: dict[str, ToolSpec] = {s.name: s for s in specs}

    def __iter__(self) -> Iterator[ToolSpec]:
        return iter(self._specs.values())

    def __len__(self) -> int:
        return len(self._specs)

    @property
    def names(self) -> list[str]:
        return list(self._specs)

    def get(self, name: str) -> ToolSpec | None:
        return self._specs.get(name)

    def schemas(self) -> list[dict[str, Any]]:
        """Anthropic-style tool definitions, in declared order."""
        return [
            {"name": s.name, "description": s.description, "input_schema": dict(s.input_schema)}
            for s in self._specs.values()
        ]

    # -- validation -------------------------------------------------------

    def validate(self, name: str, arguments: Mapping[str, Any]) -> str | None:
        """Return a human-readable reason the call is invalid, or None if it is fine.

        Deliberately returns a string instead of raising: the caller feeds it
        back to the model as a tool result, so the model can read what it did
        wrong and correct itself inside the same turn. Raising would end the
        turn and show the customer an error for a mistake the model could have
        recovered from unaided.
        """
        spec = self._specs.get(name)
        if spec is None:
            return f"Unknown tool '{name}'. Available tools: {', '.join(sorted(self._specs))}."

        unexpected = [key for key in arguments if key not in spec.properties]
        if unexpected:
            return (
                f"Tool '{name}' does not accept {unexpected}. "
                f"Accepted arguments: {', '.join(spec.properties) or 'none'}."
            )

        missing = [key for key in spec.required if key not in arguments]
        if missing:
            return f"Tool '{name}' requires {missing}."

        for key, value in arguments.items():
            rules = spec.properties.get(key, {})
            allowed = rules.get("enum")
            if allowed and value not in allowed:
                return (
                    f"'{value}' is not a valid {key} for '{name}'. "
                    f"Choose one of: {', '.join(map(str, allowed))}."
                )
            expected = rules.get("type")
            if expected and not _type_matches(expected, value):
                return f"Argument '{key}' of '{name}' must be a {expected}, got {type(value).__name__}."
        return None

    # -- execution --------------------------------------------------------

    def run(self, name: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
        """Execute one tool call. Errors are returned, never raised.

        Same reasoning as `validate`: an exception escaping here would abort a
        turn that the model could otherwise finish by trying a different tool.
        """
        reason = self.validate(name, arguments)
        if reason is not None:
            return {"error": reason}

        spec = self._specs[name]
        try:
            return spec.handler(**dict(arguments))
        except TypeError as exc:
            return {"error": f"Invalid arguments for {name}: {exc}"}
        except Exception as exc:  # surfaced to the model as an error tool_result
            return {"error": f"{name} failed: {exc}"}


_JSON_TYPES: dict[str, tuple[type, ...]] = {
    "string": (str,),
    "integer": (int,),
    "number": (int, float),
    "boolean": (bool,),
    "array": (list, tuple),
    "object": (dict,),
}


def _type_matches(expected: str, value: Any) -> bool:
    types = _JSON_TYPES.get(expected)
    if types is None:
        return True
    # bool is a subclass of int; a boolean passed where a number is expected is
    # a real mistake, not a coincidence worth tolerating.
    if expected in ("integer", "number") and isinstance(value, bool):
        return False
    return isinstance(value, types)
