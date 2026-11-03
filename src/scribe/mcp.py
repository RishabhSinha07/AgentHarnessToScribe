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

INSTRUCTIONS = """\
Scribe is a whiteboard you draw on *while you explain*. It is not a diagram
generator -- the point is that the picture builds up as you talk, and that you
can point at things and rub things out.

How to teach with it:

1. Call scribe_open(topic) once. Give the user the URL it returns.
2. Draw in SCENES of roughly 5-15 ops, not one big dump. A finished diagram
   that appears all at once is the failure mode this tool exists to fix.
3. Interleave `say` with the drawing. Draw a thing, say what it is, draw the
   next thing. That ordering IS the teaching.
4. Point. When you refer to something, `circle` it -- the note on the circle is
   spoken aloud as the gesture happens.
5. READ THE REPORT from every scribe_draw. It names collisions and ops that
   failed. A collision means something is now unreadable: erase and redraw it
   before continuing. Do not push on and hope.
6. Only call scribe_look when the report says something is wrong, or at the end
   to check the whole board reads well. It costs an image; the report is free.
7. When the report says the page is filling up, erase a region you have
   finished with, or start a new `page`.

Call scribe_reference for the command list before your first scribe_draw.
"""

mcp = MCPServer("scribe", instructions=INSTRUCTIONS)

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
def scribe_open(topic: str, open_browser: bool = True) -> str:
    """Open a new lesson, start the viewer, and show it to the user.

    Args:
        topic: What the lesson is about, shown above the board.
        open_browser: Pop the board up automatically. Set False on a headless
            machine, or when the user already has the board open.
    """
    try:
        base = SERVER.start()
    except RuntimeError as exc:
        return f"Could not start the viewer: {exc}"

    session = REGISTRY.open(topic)
    url = f"{base}/?s={session.sid}"
    opened = SERVER.open_in_browser(f"/?s={session.sid}") if open_browser else False

    head = f"Lesson open: {topic!r}\nViewer: {url}\n" + (
        "The board should have opened in the user's browser.\n"
        if opened
        else f"Ask the user to open {url}\n"
    )
    return (
        f"{head}\nDraw in scenes of 5-15 ops, interleaving `say` with the drawing, "
        f"and read the report after each one.\n\n{DSL_REFERENCE}"
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
def scribe_undo(scenes: int = 1) -> str:
    """Roll back the last N scribe_draw calls and replay the board without them.

    Use when a scene came out wrong and erasing would be messier than starting
    it again. Erase destroys pixels; this rebuilds from the op log instead.

    Args:
        scenes: How many draw calls to undo. Defaults to the last one.
    """
    try:
        session = REGISTRY.require_current()
    except LookupError as exc:
        return str(exc)
    return session.undo(max(1, scenes))


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
