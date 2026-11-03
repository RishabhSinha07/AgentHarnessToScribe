"""Run the board without an MCP client -- useful for demos and debugging."""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from .live import SERVER
from .session import REGISTRY


def serve() -> None:
    ap = argparse.ArgumentParser(description="Serve a Scribe lesson in the browser.")
    ap.add_argument("ops", nargs="?", type=Path, help="An .ops file to play.")
    ap.add_argument("--topic", default="Scribe demo")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument(
        "--grid", default="", metavar="WxH", help="Board size, e.g. 40x30. Defaults to 64x36."
    )
    args = ap.parse_args()

    grid: tuple[int, int] | None = None
    if args.grid:
        try:
            w, h = (int(v) for v in args.grid.lower().split("x"))
            grid = (w, h)
        except ValueError:
            ap.error(f"--grid wants WxH, e.g. 40x30 (got {args.grid!r})")

    SERVER.port = args.port
    url = SERVER.start()
    session = REGISTRY.open(args.topic, *(grid or (None, None)))
    print(f"Watch at: {url}/?s={session.sid}", flush=True)

    if args.ops:
        # Give a browser a moment to connect before the drawing starts.
        time.sleep(1.5)
        print(session.draw(args.ops.read_text(encoding="utf-8")), flush=True)

    print("Serving. Ctrl-C to stop.", flush=True)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        sys.exit(0)


if __name__ == "__main__":
    serve()
