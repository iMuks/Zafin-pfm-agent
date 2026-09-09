"""Tolerant JSONL extractor.

The model emits one JSON object per line, but tokens arrive a few characters at
a time, so the buffer holds a partial object almost always. This pulls out every
*complete* object and hands back the unconsumed tail.

"Tolerant" is not a nicety. Across a long run, models will occasionally emit
stray prose between objects, drop the newline separator, leave a raw newline
inside a string value, append an end-of-stream marker, or simply get cut off
mid-object when a turn ends. A naive `line.split("\\n")` + `json.loads` loses the
entire turn to any one of those. Brace counting plus targeted repair loses only
the malformed object, and everything around it still renders.
"""

from __future__ import annotations

import json
import re

#: End-of-stream markers some models emit inside the text channel.
_EOS = re.compile(r"<\|(?:end|eot|eom)[^|]*\|>")

#: Control characters that are legal in a stream but illegal inside a JSON
#: string literal. Re-escaping them is the single most common repair needed.
_CONTROL = re.compile(r"(?<!\\)[\n\r\t]")
_ESCAPES = {"\n": "\\n", "\r": "\\r", "\t": "\\t"}


def _find_object_end(text: str, start: int) -> int | None:
    """Index just past the object that opens at `start`, or None if incomplete.

    Brace counting rather than splitting on newlines, so two objects that arrive
    without a separator between them still yield two objects. String state is
    tracked so that a brace inside a merchant name cannot end the object early.
    """
    depth = 0
    in_string = False
    escaped = False

    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return index + 1
    return None


def _loads_tolerant(chunk: str) -> dict | None:
    try:
        value = json.loads(chunk)
    except json.JSONDecodeError:
        repaired = _CONTROL.sub(lambda m: _ESCAPES[m.group()], chunk)
        try:
            value = json.loads(repaired)
        except json.JSONDecodeError:
            return None
    return value if isinstance(value, dict) else None


def extract_json_objects(buffer: str) -> tuple[list[dict], str]:
    """Pull every complete JSON object out of `buffer`.

    Returns `(objects, remaining)`. Bytes before the first "{" are discarded —
    that is where stray prose and EOS markers end up — and an incomplete tail is
    returned so the next delta can complete it.
    """
    buffer = _EOS.sub("", buffer)
    objects: list[dict] = []
    cursor = 0

    while True:
        start = buffer.find("{", cursor)
        if start == -1:
            return objects, ""  # nothing left that could become an object

        end = _find_object_end(buffer, start)
        if end is None:
            return objects, buffer[start:]  # incomplete — wait for more tokens

        parsed = _loads_tolerant(buffer[start:end])
        if parsed is not None:
            objects.append(parsed)
        cursor = end
