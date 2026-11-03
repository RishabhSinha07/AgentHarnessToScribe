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
    # Each viewer gets its own queue, so a slow tab cannot stall the others.
    viewers: list[asyncio.Queue[str]] = field(default_factory=list)

    # --- drawing -------------------------------------------------------------
    def draw(self, script: str) -> str:
        """Apply a script, push it to every viewer, and report what happened."""
        self.canvas.clear_feedback()
        self.canvas.run(script)

        lines = [ln for ln in script.splitlines() if ln.strip() and not ln.strip().startswith("#")]
        self.ops.extend(lines)
        for line in lines:
            self.broadcast(line)

        return self.canvas.state_summary()

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
