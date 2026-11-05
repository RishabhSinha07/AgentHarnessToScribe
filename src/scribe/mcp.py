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
from .session import REGISTRY, Plan

INSTRUCTIONS = """\
Scribe is a whiteboard you teach at. It is not a diagram generator. The point
is that the picture builds while you talk, that you can point at what you mean,
and that you can rub things out.

A lesson, not a tour of a diagram:

1. scribe_open(topic). Give the user the URL it returns.
2. scribe_plan BEFORE drawing anything -- scribe_draw will refuse until you
   have. Decide the one idea, the question you open on, 3-5 beats, and the
   payoff. A lesson invented scene by scene ends up narrating what is on the
   board instead of why it matters, and fills the space before it reaches the
   point.
3. Then draw the beats, 5-15 ops at a time, interleaving `say` with the
   drawing. Draw a thing, say why it matters, draw the next thing.
4. Point. `highlight "<label>"` rings the element and speaks its note as the
   ring is drawn.
5. READ THE REPORT from every scribe_draw. It names collisions and failed ops.
   A collision means something is now unreadable: erase and redraw before
   continuing.
6. scribe_look only when the report says something is wrong, or at the end to
   check the whole board reads. It costs an image; the report is free.

Structure, which is what makes it a lesson:
- Open on a question, not an answer. Put the hook on the board first and leave
  it there -- the payoff lands when the learner can still see what was asked.
- Argue, do not enumerate. problem -> obvious fix -> why it fails -> the real
  idea beats four boxes in a row.
- Name the misconception and draw it. A model you deliberately draw wrong, then
  correct, teaches more than a correct model drawn once. This is the one thing
  a whiteboard can do that a finished diagram cannot -- use it.
- Give the key moment room. Not every beat deserves equal weight.

Call scribe_reference for the command list before your first scribe_draw.
"""


mcp = MCPServer("scribe", instructions=INSTRUCTIONS)

NARRATION_RULES = """\
Writing the narration:
- Say WHY, not WHAT. The label already says what the box is; a `say` that
  reads "on the left, the inputs" is a caption, and the picture did not need
  it. Spend the line on what the learner cannot see.
- Open on the hook, before the answer is on the board. A question the drawing
  then answers is worth more than the same fact stated first.
- Earn the erase. Wiping a region should be a move you planned -- correcting a
  model you deliberately drew wrong, or clearing space you knew you would need.
- Never narrate your own mistakes. "Oops, I rubbed that out" is tooling noise,
  not teaching. Fix it silently.
- One idea per line, and keep lines short. Long sentences stall the drawing
  while the voice catches up.
"""

DSL_REFERENCE = f"""\
The whiteboard is a {GRID_W} wide x {GRID_H} tall grid. (0,0) is top-left.
One command per line. Coordinates accept "3,4" or "3 4" interchangeably.

  text    x,y <words>            write words, left-aligned at x,y
  box     x,y,w,h <label>        rectangle, label centred inside
  ellipse x,y,rx,ry <label>      oval in the current pen
  arrow   x1,y1 x2,y2 <label>    arrow with a head at x2,y2
  highlight "<label>" <note>     RED ring around the element with that label,
                                 sized from its own box. THIS is how you point.
                                 The note is spoken as the ring is drawn.
  circle  x,y,r <spoken note>    RED ring at a position you choose. Only for
                                 pointing at empty space -- for anything with
                                 a label, use `highlight` so the ring fits.
  stroke  x1,y1 x2,y2 ...        freehand polyline (underlines, ticks)

  Addressed by LABEL, so you never compute coordinates for these:
  connect "<from>" "<to>" <label>  elbow-routed arrow between two elements.
                                 Prefer this over `arrow`: straight diagonals
                                 are what make a board look sketched.
  fill    "<label>" <tint>        tint a shape (blue green grey ink none red yellow)
  strike  "<label>" <note>        cross it out. Pairs with drawing a wrong
                                 model on purpose, then retiring it.

  Structure and emphasis:
  icon    <name> x,y <size> <label>   pictogram. A cylinder is a database; a
                                 box labelled "Database" is just a box.
                                 (db server user cloud queue file clock lock globe gear warning check cross box)
  group   x,y,w,h <label>        dashed container with a label tab
  note    x,y,w <text>           sticky note; wraps its own text
  brace   x,y,h <label>          vertical brace grouping a range
  dash    on|off                 line style for everything after it
  erase   x,y,w,h <spoken note>  erase a rectangle
  erase   x,y,r <spoken note>    erase a square of radius r
  pen     <{"|".join(sorted(set(PENS)))}>
  board   W H                    declare the board size. First line only.
  page    <title>                wipe to a clean page
  say     <text>                 narration spoken at this moment
  # ...                          comment

Writing a good lesson:
- Interleave `say` with drawing so the picture builds as you talk. Draw, speak,
  point, speak -- that ordering IS the teaching.
- Say WHY, not WHAT. The label already says what the box is.
- Text is about 0.5 grid units per character. Make boxes wide enough for their
  labels; you will be told if one does not fit.
- `connect`, `fill` and `strike` take LABELS, not coordinates. Use them.
- Reach for `icon` over a labelled box wherever one fits. It is the single
  biggest difference between a board that looks drawn and one that looks like
  a wireframe.
- `highlight "<label>"` is how you point. Never guess a radius with `circle`
  for something that has a label -- you cannot see how wide the box is, and the
  ring will either cut through the text or swallow its neighbours.
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
def scribe_open(
    topic: str,
    open_browser: bool = True,
    grid_w: int = 0,
    grid_h: int = 0,
) -> str:
    """Open a new lesson, start the viewer, and show it to the user.

    Args:
        topic: What the lesson is about, shown above the board.
        open_browser: Pop the board up automatically. Set False on a headless
            machine, or when the user already has the board open.
        grid_w: Board width in grid units. Leave at 0 for the default 64.
        grid_h: Board height. Leave at 0 for the default 36. A taller board
            suits a deep hierarchy; a wider one suits a long pipeline.
    """
    try:
        base = SERVER.start()
    except RuntimeError as exc:
        return f"Could not start the viewer: {exc}"

    session = REGISTRY.open(topic, grid_w or None, grid_h or None)
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
def scribe_plan(
    idea: str,
    hook: str,
    beats: list[str],
    payoff: str,
    misconception: str = "",
) -> str:
    """Commit to the shape of the lesson before drawing any of it. Required.

    A lesson invented scene by scene narrates what is being drawn rather than
    why it matters, and fills the board before it reaches the point. Decide
    the arc first; then the drawing executes a plan instead of inventing one.

    Args:
        idea: The ONE thing the learner should leave with. One sentence.
        hook: The question the drawing answers, asked before you answer it.
            Open with this on the board.
        beats: 3-5 scenes in order. A beat is a step in the argument, not a
            list of shapes: "one server falls over under load", not "draw
            three boxes". Good lessons go problem -> obvious fix -> why it
            fails -> the real idea.
        payoff: The closing insight the hook has been waiting for.
        misconception: What the learner probably believes that is wrong. If
            you can name it, the lesson has something to push against.
    """
    try:
        session = REGISTRY.require_current()
    except LookupError as exc:
        return str(exc)

    if not beats:
        return "A plan needs at least one beat. Three to five is usually right."

    session.plan = Plan(
        idea=idea.strip(),
        hook=hook.strip(),
        beats=[b.strip() for b in beats],
        payoff=payoff.strip(),
        misconception=misconception.strip(),
    )
    return (
        f"Plan set for {session.topic!r}.\n\n"
        f"{session.plan.budget(session.canvas.grid_w, session.canvas.grid_h)}\n\n"
        f"{NARRATION_RULES}"
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

    if session.plan is None:
        return (
            "No plan yet -- nothing was drawn.\n\n"
            "Call scribe_plan first. Lessons improvised scene by scene end up "
            "narrating what is on the board instead of why it matters, and run "
            "out of room before they reach the point. Decide the idea, the "
            "hook, the beats and the payoff, then draw them."
        )
    return session.draw(ops)


@mcp.tool()
def scribe_look() -> Image | str:
    """Look at the board. Returns a PNG of the canvas as it stands.

    Use after scribe_draw reports a collision, or when checking that a layout
    reads well. Costs an image, so prefer the text report when it is clean.
    """
    try:
        session = REGISTRY.require_current()
    except LookupError as exc:
        return str(exc)
    path = Path(tempfile.gettempdir()) / f"scribe_{session.sid}.png"
    session.snapshot(path)
    return Image(data=path.read_bytes(), format="png")


@mcp.tool()
def scribe_erase_preview(x: float, y: float, w: float, h: float) -> str:
    """Check what an erase rectangle would destroy, without erasing anything.

    Args:
        x: Left edge. y: Top edge. w: Width. h: Height.
    """
    try:
        session = REGISTRY.require_current()
    except LookupError as exc:
        return str(exc)
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
    lines = [
        f"Topic: {session.topic}",
        f"Viewer: {SERVER.url}/?s={session.sid}",
        f"Ops drawn: {len(session.ops)}",
        f"Viewers connected: {len(session.viewers)}",
    ]
    if session.plan is None:
        lines.append("No plan yet -- scribe_draw will refuse until scribe_plan is called.")
    else:
        pl = session.plan
        lines += [
            "",
            f"Idea:   {pl.idea}",
            f"Hook:   {pl.hook}",
            *([f"Myth:   {pl.misconception}"] if pl.misconception else []),
            "Beats:",
            *[f"  {i}. {b}" for i, b in enumerate(pl.beats, 1)],
            f"Payoff: {pl.payoff}",
        ]
    lines += ["", session.canvas.state_summary(verbose=False)]
    return "\n".join(lines)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
