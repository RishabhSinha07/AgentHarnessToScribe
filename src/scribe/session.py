"""One live lesson: a canvas, the ops that built it, and the viewers watching.

A session is the unit the MCP tools operate on. It keeps the full op log so a
viewer joining late can be brought up to date, and it hands out the state
report the agent reads after every draw.
"""

from __future__ import annotations

import asyncio
import secrets
from dataclasses import dataclass, field
from pathlib import Path

from .canvas import Canvas


@dataclass
class Plan:
    """The spine of a lesson, committed to before any ink goes down.

    Improvised lessons narrate what is being drawn rather than why it matters,
    because a line written while drawing a box describes the box. Deciding the
    arc first is what lets a scene set up a payoff instead of just adding
    another shape.
    """

    idea: str  # the one thing the learner leaves with
    hook: str  # the question the drawing answers
    beats: list[str]  # 3-5 scenes, in order
    payoff: str  # the closing insight
    misconception: str = ""  # what they probably believe that is wrong

    def budget(self, grid_w: int, grid_h: int) -> str:
        """A rough spatial plan, so the board does not fill up by accident."""
        title_h, payoff_h = 3, 4
        body = grid_h - title_h - payoff_h
        per = body / max(1, len(self.beats))
        rows = []
        y = title_h
        for i, beat in enumerate(self.beats, 1):
            rows.append(f"  beat {i}  y {y:.0f}-{y + per:.0f}   {beat}")
            y += per
        return "\n".join(
            [
                f"Board is {grid_w} wide x {grid_h} tall. A rough budget:",
                f"  title   y 0-{title_h}",
                *rows,
                f"  payoff  y {grid_h - payoff_h}-{grid_h}   {self.payoff}",
                "",
                (
                    "This is a budget, not a grid -- a beat may span the board. What "
                    "matters is that you know before you start which region you will "
                    "erase to make room, so the wipe reads as a deliberate move "
                    "rather than running out of space."
                ),
            ]
        )


# The viewer queues live on the web server's event loop, but ops are drawn from
# the MCP thread. Puts have to be handed across rather than made directly.
_LOOP: asyncio.AbstractEventLoop | None = None


def set_broadcast_loop(loop: asyncio.AbstractEventLoop | None) -> None:
    global _LOOP
    _LOOP = loop


@dataclass
class Session:
    topic: str
    sid: str = field(default_factory=lambda: secrets.token_urlsafe(6))
    canvas: Canvas = field(default_factory=Canvas)
    ops: list[str] = field(default_factory=list)
    # Index into `ops` at the start of each draw call, so a scene can be undone.
    marks: list[int] = field(default_factory=list)
    plan: Plan | None = None
    finished: bool = False
    # Each viewer gets its own queue, so a slow tab cannot stall the others.
    viewers: list[asyncio.Queue[str]] = field(default_factory=list)

    # --- drawing -------------------------------------------------------------
    def draw(self, script: str) -> str:
        """Apply a script, push it to every viewer, and report what happened."""
        self.finished = False
        self.canvas.clear_feedback()
        self.canvas.run(script)

        # Only ops the canvas accepted reach the viewer. Ask the canvas which
        # those were -- do not infer it from the error messages.
        lines = list(self.canvas.accepted)

        self.marks.append(len(self.ops))
        self.ops.extend(lines)
        for line in lines:
            self.broadcast(line)

        return self.canvas.state_summary()

    def undo(self, scenes: int = 1) -> str:
        """Roll back the last N draw calls.

        Erase destroys pixels, so there is nothing to "un-erase" -- the canvas
        is rebuilt from the surviving op log instead. Viewers are reset and
        replayed, which is why the whole log is kept.
        """
        if not self.marks:
            return "Nothing to undo."

        undone = min(scenes, len(self.marks))
        keep = self.marks[len(self.marks) - undone]
        dropped = len(self.ops) - keep
        self.ops = self.ops[:keep]
        self.marks = self.marks[: len(self.marks) - undone]

        # Keep the board the same size -- a fresh default Canvas() silently
        # resized a 40x30 lesson to 64x36, leaving agent and viewer in
        # different coordinate systems.
        self.canvas = Canvas(grid_w=self.canvas.grid_w, grid_h=self.canvas.grid_h)
        self.canvas.run("\n".join(self.ops))
        self.canvas.clear_feedback()

        self.reset_viewers()
        return (
            f"Undid {undone} scene(s), dropping {dropped} ops. "
            f"{len(self.ops)} remain.\n" + self.canvas.state_summary(verbose=False)
        )

    def finish(self, closing: str = "") -> str:
        """Mark the lesson complete and tell the viewers.

        Without this the board cannot tell a model composing the next scene
        from a lesson that ended ten minutes ago, and sits on "thinking"
        forever.
        """
        self.finished = True
        if closing:
            self.narration.append(closing)
            self.ops.append(f'say "{closing}"')
            self.broadcast(f'say "{closing}"')
        for q in list(self.viewers):
            self._offer_control(q, {"type": "finished"})
        return f"Lesson marked complete. {len(self.ops)} ops drawn."

    @property
    def narration(self) -> list[str]:
        return self.canvas.narration

    def reset_viewers(self) -> None:
        """Tell every viewer to clear and replay from scratch."""
        for q in list(self.viewers):
            self._offer_control(
                q,
                {
                    "type": "reset",
                    "grid": [self.canvas.grid_w, self.canvas.grid_h],
                    "ops": "\n".join(self.ops),
                },
            )

    def _offer_control(self, q: asyncio.Queue[str], payload: dict) -> None:
        import json

        msg = "\x00" + json.dumps(payload)  # NUL prefix marks a control frame
        if _LOOP is not None and _LOOP.is_running():
            _LOOP.call_soon_threadsafe(self._offer, q, msg)
        else:
            self._offer(q, msg)

    def broadcast(self, line: str) -> None:
        for q in list(self.viewers):
            if _LOOP is not None and _LOOP.is_running():
                _LOOP.call_soon_threadsafe(self._offer, q, line)
            else:
                self._offer(q, line)

    def _offer(self, q: asyncio.Queue[str], line: str) -> None:
        """Put on the viewer's queue, dropping a viewer that has stopped draining."""
        try:
            q.put_nowait(line)
        except asyncio.QueueFull:
            if q in self.viewers:
                self.viewers.remove(q)

    # --- viewers -------------------------------------------------------------
    def subscribe(self) -> asyncio.Queue[str]:
        """Attach a viewer, primed with everything drawn so far."""
        q: asyncio.Queue[str] = asyncio.Queue(maxsize=4096)
        for line in self.ops:
            q.put_nowait(line)
        self.viewers.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue[str]) -> None:
        if q in self.viewers:
            self.viewers.remove(q)

    # --- snapshots -----------------------------------------------------------
    def snapshot(self, path: Path) -> Path:
        return self.canvas.render(path)


class Registry:
    """All sessions in this process. Small enough to keep in memory."""

    def __init__(self) -> None:
        self._sessions: dict[str, Session] = {}
        self.current: Session | None = None

    def open(self, topic: str, grid_w: int | None = None, grid_h: int | None = None) -> Session:
        canvas = Canvas(grid_w=grid_w, grid_h=grid_h) if grid_w and grid_h else Canvas()
        s = Session(topic=topic, canvas=canvas)
        self._sessions[s.sid] = s
        self.current = s
        return s

    def get(self, sid: str) -> Session | None:
        return self._sessions.get(sid)

    def require_current(self) -> Session:
        if self.current is None:
            raise LookupError("No open lesson. Call scribe_open first.")
        return self.current

    def close(self, sid: str) -> None:
        s = self._sessions.pop(sid, None)
        if s and self.current is s:
            self.current = None


REGISTRY = Registry()
