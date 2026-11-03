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
    ap.add_argument(
        "--no-browser",
        action="store_true",
        help="Do not open a browser; just print the URL and wait for a viewer.",
    )
    ap.add_argument(
        "--wait",
        type=float,
        default=20.0,
        metavar="SECONDS",
        help="How long to wait for a viewer before drawing anyway.",
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
    watch = f"{url}/?s={session.sid}"
    print(f"Watch at: {watch}", flush=True)

    if args.ops:
        if not args.no_browser:
            SERVER.open_in_browser(f"/?s={session.sid}")

        # Wait for a viewer before drawing. Sleeping a fixed interval and
        # hoping was the old behaviour, and it lost the race every time: the
        # viewer arrived after the ops and painted a finished, silent board.
        deadline = time.monotonic() + args.wait
        while not session.viewers and time.monotonic() < deadline:
            time.sleep(0.1)
        if not session.viewers:
            print(
                f"No viewer connected after {args.wait:g}s. Drawing anyway -- "
                f"open {watch} and press Replay.",
                flush=True,
            )
        else:
            time.sleep(0.4)  # let the board finish its first paint

        print(session.draw(args.ops.read_text(encoding="utf-8")), flush=True)

    print("Serving. Ctrl-C to stop.", flush=True)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        sys.exit(0)


if __name__ == "__main__":
    serve()
