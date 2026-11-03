"""The web server the viewer talks to.

Runs in a background thread alongside the MCP server, so one process owns the
canvas state, serves the viewer, and answers the agent. Ops are pushed to each
connected browser over a WebSocket as they are drawn.
"""

from __future__ import annotations

import asyncio
import json
import threading
from pathlib import Path

import uvicorn
from starlette.applications import Starlette
from starlette.responses import HTMLResponse, JSONResponse
from starlette.routing import Route, WebSocketRoute
from starlette.websockets import WebSocket, WebSocketDisconnect

from .session import REGISTRY, set_broadcast_loop

VIEWER = Path(__file__).parent / "viewer" / "index.html"


async def _index(request):
    return HTMLResponse(VIEWER.read_text(encoding="utf-8"))


async def _health(request):
    cur = REGISTRY.current
    return JSONResponse(
        {
            "ok": True,
            "session": cur.sid if cur else None,
            "topic": cur.topic if cur else None,
            "ops": len(cur.ops) if cur else 0,
        }
    )


async def _ws(websocket: WebSocket) -> None:
    await websocket.accept()

    sid = websocket.query_params.get("s") or ""
    session = REGISTRY.get(sid) if sid else REGISTRY.current
    if session is None:
        await websocket.send_text(json.dumps({"type": "closed", "reason": "no open lesson"}))
        await websocket.close()
        return

    # Prime the viewer with everything drawn so far, then stream the rest.
    await websocket.send_text(
        json.dumps(
            {
                "type": "hello",
                "topic": session.topic,
                "ops": "\n".join(session.ops),
            }
        )
    )

    queue = session.subscribe()
    # The backlog is already in the hello frame; drop it from this queue.
    while not queue.empty():
        queue.get_nowait()

    try:
        while True:
            line = await queue.get()
            # Coalesce whatever else is waiting, so a burst arrives as one frame.
            batch = [line]
            while not queue.empty() and len(batch) < 200:
                batch.append(queue.get_nowait())
            await websocket.send_text(json.dumps({"type": "ops", "ops": "\n".join(batch)}))
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        session.unsubscribe(queue)


app = Starlette(
    routes=[
        Route("/", _index),
        Route("/health", _health),
        WebSocketRoute("/ws", _ws),
    ]
)


class LiveServer:
    """Owns the uvicorn loop on its own thread, so MCP keeps stdio to itself."""

    def __init__(self, host: str = "127.0.0.1", port: int = 8765) -> None:
        self.host, self.port = host, port
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}"

    def start(self) -> str:
        if self._thread and self._thread.is_alive():
            return self.url

        def run() -> None:
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
            set_broadcast_loop(self._loop)
            config = uvicorn.Config(
                app, host=self.host, port=self.port, log_level="warning", loop="asyncio"
            )
            self._loop.run_until_complete(uvicorn.Server(config).serve())

        self._thread = threading.Thread(target=run, daemon=True, name="scribe-live")
        self._thread.start()
        return self.url

    def submit(self, coro) -> None:
        """Run a coroutine on the server loop from the MCP thread."""
        if self._loop is not None:
            asyncio.run_coroutine_threadsafe(coro, self._loop)


SERVER = LiveServer()
