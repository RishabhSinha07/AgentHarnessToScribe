"""MCP adapter: lets any MCP client drive the whiteboard.

Design note -- `scribe_draw` returns errors, collisions and occupancy as text
on every call. That is deliberate: the agent learns about an overlap cheaply,
in words, and only needs `scribe_look` (which costs an image) when the text
report says something is actually wrong.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from mcp.server.mcpserver import Image, MCPServer

from .canvas import GRID_H, GRID_W, PENS
from .live import SERVER
from .session import REGISTRY

mcp = MCPServer("scribe")

DSL_REFERENCE = f"""\
The whiteboard is a {GRID_W} wide x {GRID_H} tall grid. (0,0) is top-left.
One command per line. Coordinates accept "3,4" or "3 4" interchangeably.

  text    x,y <words>            write words, left-aligned at x,y
  box     x,y,w,h <label>        rectangle, label centred inside
  ellipse x,y,rx,ry <label>      oval in the current pen
  arrow   x1,y1 x2,y2 <label>    arrow with a head at x2,y2
  circle  x,y,r <spoken note>    RED highlight -- the pointer. The note is
                                 spoken aloud as the circle is drawn.
  stroke  x1,y1 x2,y2 ...        freehand polyline (underlines, braces, ticks)
  erase   x,y,w,h <spoken note>  erase a rectangle
  erase   x,y,r <spoken note>    erase a square of radius r
  pen     <{"|".join(sorted(set(PENS)))}>
  page    <title>                wipe to a clean page
  say     <text>                 narration spoken at this moment
  # ...                          comment

Writing a good lesson:
- Interleave `say` with drawing so the picture builds as you talk. Draw, speak,
  point, speak -- that ordering IS the teaching.
- Text is about 0.5 grid units per character. Make boxes wide enough for their
  labels; you will be told if one does not fit.
- `circle` is how you point at something. Use it when you refer to a thing.
- Nothing solid may overlap anything else solid. Highlights and freehand are
  exempt -- they are meant to cross things.
- When the page gets past ~55% full you will be warned. Erase a region you have
  finished with, or start a new `page`.
"""


@mcp.tool()
def scribe_reference() -> str:
    """The drawing command reference. Read this before drawing for the first time."""
    return DSL_REFERENCE


@mcp.tool()
def scribe_open(topic: str) -> str:
    """Open a new lesson and start the viewer. Returns the URL to watch it at.

    Args:
        topic: What the lesson is about, shown above the board.
    """
    base = SERVER.start()
    session = REGISTRY.open(topic)
    return (
        f"Lesson open: {topic!r}\n"
        f"Watch it at: {base}/?s={session.sid}\n"
        f"Tell the user to open that URL, then draw with scribe_draw.\n\n"
        f"{DSL_REFERENCE}"
    )


@mcp.tool()
def scribe_draw(ops: str) -> str:
    """Draw on the board. Ops stream to the viewer live, one line at a time.

    Args:
        ops: Drawing commands, one per line. See scribe_reference.

    Returns a report of errors, collisions, erased elements and how full the
    page is. Read it -- a collision means something is now unreadable.
    """
    try:
        session = REGISTRY.require_current()
    except LookupError as exc:
        return str(exc)
    return session.draw(ops)


@mcp.tool()
def scribe_look() -> Image:
    """Look at the board. Returns a PNG of the canvas as it stands.

    Use after scribe_draw reports a collision, or when checking that a layout
    reads well. Costs an image, so prefer the text report when it is clean.
    """
    session = REGISTRY.require_current()
    path = Path(tempfile.gettempdir()) / f"scribe_{session.sid}.png"
    session.snapshot(path)
    return Image(data=path.read_bytes(), format="png")


@mcp.tool()
def scribe_erase_preview(x: float, y: float, w: float, h: float) -> str:
    """Check what an erase rectangle would destroy, without erasing anything.

    Args:
        x: Left edge. y: Top edge. w: Width. h: Height.
    """
    session = REGISTRY.require_current()
    hit = session.canvas.erase_preview(x, y, x + w, y + h)
    if not hit:
        return f"Erasing ({x},{y}) {w}x{h} would not touch anything."
    lines = [f"Erasing ({x},{y}) {w}x{h} would touch {len(hit)} element(s):"]
    for e in hit:
        fully = x <= e.x0 and x + w >= e.x1 and y <= e.y0 and y + h >= e.y1
        lines.append(f"  {'remove' if fully else 'CLIP'} {e.describe()}")
    return "\n".join(lines)


@mcp.tool()
def scribe_status() -> str:
    """Where the current lesson stands: topic, ops drawn, viewer URL."""
    session = REGISTRY.current
    if session is None:
        return "No lesson open."
    return (
        f"Topic: {session.topic}\n"
        f"Viewer: {SERVER.url}/?s={session.sid}\n"
        f"Ops drawn: {len(session.ops)}\n"
        f"Viewers connected: {len(session.viewers)}\n\n"
        f"{session.canvas.state_summary(verbose=False)}"
    )


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
