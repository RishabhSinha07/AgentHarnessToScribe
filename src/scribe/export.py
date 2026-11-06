"""Turn a finished lesson into something you can keep.

Until a lesson can leave the process it dies with it: nothing to re-watch,
nothing to send a colleague, and no corpus from the op-streams. Two formats,
because they answer different questions -- a PNG shows what was drawn, a
replay shows how it was taught.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from .canvas import Canvas
from .session import Session

VIEWER = Path(__file__).parent / "viewer" / "index.html"


def default_dir() -> Path:
    d = Path.home() / ".scribe" / "lessons"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _slug(text: str) -> str:
    keep = [c.lower() if c.isalnum() else "-" for c in text.strip()[:60]]
    return "".join(keep).strip("-").replace("--", "-") or "lesson"


def as_png(session: Session, path: Path | None = None) -> Path:
    """The whole board, every screen of it, as one tall image."""
    path = path or default_dir() / f"{_slug(session.topic)}.png"
    # Re-run from the op log so the image is the full board, not the last view.
    canvas = Canvas(grid_w=session.canvas.grid_w, grid_h=session.canvas.grid_h)
    canvas.run("\n".join(session.ops))
    return canvas.render(path)


def as_json(session: Session, path: Path | None = None) -> Path:
    """The op-stream itself: replayable, diffable, and training data later."""
    path = path or default_dir() / f"{_slug(session.topic)}.json"
    plan = session.plan
    path.write_text(
        json.dumps(
            {
                "topic": session.topic,
                "recorded": datetime.now(UTC).isoformat(timespec="seconds"),
                "grid": [session.canvas.grid_w, session.canvas.grid_h],
                "panels": session.canvas.panels,
                "plan": None
                if plan is None
                else {
                    "idea": plan.idea,
                    "hook": plan.hook,
                    "misconception": plan.misconception,
                    "beats": plan.beats,
                    "payoff": plan.payoff,
                },
                "ops": session.ops,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return path


def as_replay(session: Session, path: Path | None = None) -> Path:
    """A self-contained page that redraws the lesson, narration and all.

    The viewer already knows how to animate an op-stream; this bakes one in
    and cuts the live wiring, so the result opens anywhere with no server.
    """
    path = path or default_dir() / f"{_slug(session.topic)}.html"
    page = VIEWER.read_text(encoding="utf-8")

    ops = "\n".join(session.ops)
    baked = (
        f"<script>window.__OPS__ = {json.dumps(ops)};"
        f"window.__TOPIC__ = {json.dumps(session.topic)};"
        f"window.__GRID__ = {json.dumps([session.canvas.grid_w, session.canvas.grid_h])};"
        f"</script>"
    )
    # Insert before the viewer's own script so the flags are set first.
    page = page.replace("<script>\n(() => {", baked + "\n<script>\n(() => {", 1)
    path.write_text(page, encoding="utf-8")
    return path


def save_all(session: Session, out_dir: Path | None = None) -> dict[str, Path]:
    out_dir = out_dir or default_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = _slug(session.topic)
    return {
        "png": as_png(session, out_dir / f"{stem}.png"),
        "replay": as_replay(session, out_dir / f"{stem}.html"),
        "ops": as_json(session, out_dir / f"{stem}.json"),
    }
