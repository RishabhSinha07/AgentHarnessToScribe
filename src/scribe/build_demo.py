"""Rebuild the published demo at docs/ from an example lesson.

docs/index.html is the first thing anyone sees, and it is a *baked copy* of
the viewer -- so it goes stale silently every time the viewer is fixed. This
regenerates it from the current code, which is why it is a script and not a
file someone remembers to update.

    uv run python -m scribe.build_demo
"""

from __future__ import annotations

from pathlib import Path

from . import export
from .canvas import Canvas
from .session import Plan, Session

ROOT = Path(__file__).resolve().parents[2]
LESSON = ROOT / "examples" / "first_load.ops"
DOCS = ROOT / "docs"

PLAN = Plan(
    idea="Latency is distance, not bandwidth -- so you move the files closer.",
    hook="Why is the first visit always the slow one?",
    misconception="That a faster connection fixes a slow first load.",
    beats=[
        "the request chain, and what each hop costs",
        "it is not one trip -- each file waits for the last",
        "the obvious fix, and why it does nothing",
        "move the destination instead of speeding up the journey",
    ],
    payoff="Second visit is fast because nothing has to travel.",
)


def build() -> dict[str, Path]:
    script = LESSON.read_text(encoding="utf-8")

    canvas = Canvas()
    canvas.run(script)
    if canvas.errors:
        raise SystemExit("demo lesson has errors:\n  " + "\n  ".join(canvas.errors))

    session = Session(topic="Why your first page load is slow")
    session.plan = PLAN
    session.draw(script)
    session.finish()

    DOCS.mkdir(exist_ok=True)
    paths = {
        "index.html": export.as_replay(session, DOCS / "index.html"),
        "lesson.json": export.as_json(session, DOCS / "lesson.json"),
        "preview.png": export.as_png(session, DOCS / "preview.png"),
    }
    return paths


if __name__ == "__main__":
    for name, path in build().items():
        print(f"  {name:<12} {path.stat().st_size:>9,} bytes")
