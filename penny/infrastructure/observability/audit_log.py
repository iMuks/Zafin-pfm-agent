"""`AuditSink` writing an append-only JSONL file.

Upstream this is object storage with WORM retention. The guarantee that matters
for the demo is the same one that matters in production: **the audit write can
never delay or fail the customer's answer.** It is scheduled on the event loop
and its failures are logged, never raised.

The trade-off is stated rather than hidden: fire-and-forget means a crash
between response and flush loses the record. A regulated deployment closes that
gap with a durable queue, not by making the customer wait for fsync.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

from penny.application.ports.audit import AuditRecord, AuditSink
from penny.infrastructure.observability.telemetry import log


class FileAuditSink(AuditSink):
    def __init__(self, path: Path, enabled: bool = True) -> None:
        self._path = path
        self._enabled = enabled

    def _write(self, payload: dict[str, Any]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, default=str) + "\n")

    async def _write_async(self, payload: dict[str, Any]) -> None:
        try:
            await asyncio.to_thread(self._write, payload)
        except Exception as exc:  # must never surface to the customer
            log("audit.write_failed", error=str(exc))

    def record(self, entry: AuditRecord) -> None:
        if not self._enabled:
            return

        payload = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            **asdict(entry),
        }
        try:
            asyncio.get_running_loop().create_task(self._write_async(payload))
        except RuntimeError:
            self._write(payload)  # no running loop: a sync context, e.g. a test


class NullAuditSink(AuditSink):
    """Discards everything. Used by the gallery and by tests."""

    def record(self, entry: AuditRecord) -> None:  # noqa: D102
        return None
