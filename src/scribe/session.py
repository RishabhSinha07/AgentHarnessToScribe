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
    # Each viewer gets its own queue, so a slow tab cannot stall the others.
    viewers: list[asyncio.Queue[str]] = field(default_factory=list)

    # --- drawing -------------------------------------------------------------
    def draw(self, script: str) -> str:
        """Apply a script, push it to every viewer, and report what happened."""
        self.canvas.clear_feedback()
        self.canvas.run(script)

        lines = [ln for ln in script.splitlines() if ln.strip() and not ln.strip().startswith("#")]
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

        self.canvas = Canvas()
        self.canvas.run("\n".join(self.ops))
        self.canvas.clear_feedback()

        self.reset_viewers()
        return (
            f"Undid {undone} scene(s), dropping {dropped} ops. "
            f"{len(self.ops)} remain.\n" + self.canvas.state_summary(verbose=False)
        )

    def reset_viewers(self) -> None:
        """Tell every viewer to clear and replay from scratch."""
        for q in list(self.viewers):
            self._offer_control(q, {"type": "reset", "ops": "\n".join(self.ops)})

    def _offer_control(self, q: asyncio.Queue[str], payload: dict) -> None:
        import json

        msg = "\x00" + json.dumps(payload)  # NUL prefix marks a control frame
        if _LOOP is not None and not _LOOP.is_closed():
            _LOOP.call_soon_threadsafe(self._offer, q, msg)
        else:
            self._offer(q, msg)

    def broadcast(self, line: str) -> None:
        for q in list(self.viewers):
            if _LOOP is not None and not _LOOP.is_closed():
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

    def open(self, topic: str) -> Session:
        s = Session(topic=topic)
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
