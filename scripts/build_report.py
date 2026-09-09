"""Produce the self-contained build report.

`docs/build-report.source.html` is the authored file: it references the diagrams
as ordinary sibling files, so they stay editable and diffable. This script
inlines each one as a base64 data URI and writes `docs/build-report.html`, the
deliverable — a single file that renders identically wherever it is opened
(GitHub's HTML preview, Drive, an email attachment, a folder of its own).

    python scripts/build_report.py
"""

from __future__ import annotations

import base64
import mimetypes
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
SOURCE = DOCS / "build-report.source.html"
TARGET = DOCS / "build-report.html"


def data_uri(path: Path) -> str:
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    if mime == "image/svg+xml":
        mime = "image/svg+xml"
    payload = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{payload}"


def main() -> int:
    html = SOURCE.read_text(encoding="utf-8")
    inlined, missing = [], []

    def replace(match: re.Match[str]) -> str:
        src = match.group(1)
        if src.startswith(("data:", "http://", "https://")):
            return match.group(0)
        asset = DOCS / src
        if not asset.exists():
            missing.append(src)
            return match.group(0)
        inlined.append((src, asset.stat().st_size))
        return f'src="{data_uri(asset)}"'

    html = re.sub(r'src="([^"]+)"', replace, html)
    TARGET.write_text(html, encoding="utf-8")

    for src, size in inlined:
        print(f"  inlined {src} ({size / 1024:.0f} KB)")
    for src in missing:
        print(f"  MISSING {src}", file=sys.stderr)
    print(f"  wrote {TARGET.relative_to(ROOT)} ({TARGET.stat().st_size / 1024:.0f} KB)")
    return 1 if missing else 0


if __name__ == "__main__":
    raise SystemExit(main())
