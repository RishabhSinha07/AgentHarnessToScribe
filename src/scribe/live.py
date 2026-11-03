"""The web server the viewer talks to.

Runs in a background thread alongside the MCP server, so one process owns the
canvas state, serves the viewer, and answers the agent. Ops are pushed to each
connected browser over a WebSocket as they are drawn.
"""

from __future__ import annotations

import asyncio
import json
import socket
import threading
import webbrowser
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
            if line.startswith("\x00"):
                # A control frame (reset, …) -- forward it as-is.
                await websocket.send_text(line[1:])
                continue
            # Coalesce whatever else is waiting, so a burst arrives as one frame.
            batch = [line]
            while not queue.empty() and len(batch) < 200:
                nxt = queue.get_nowait()
                if nxt.startswith("\x00"):
                    await websocket.send_text(json.dumps({"type": "ops", "ops": "\n".join(batch)}))
                    await websocket.send_text(nxt[1:])
                    batch = []
                    break
                batch.append(nxt)
            if batch:
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
        self._ready = threading.Event()
        self._error: BaseException | None = None

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}"

    def _claim_port(self) -> socket.socket:
        """Bind a port up front, so a clash fails here rather than in the thread.

        Without this, a busy port kills the server thread silently and the
        agent hands the user a URL that never loads.
        """
        last: OSError | None = None
        for candidate in [self.port, *range(self.port + 1, self.port + 20), 0]:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind((self.host, candidate))
            except OSError as exc:
                last = exc
                sock.close()
                continue
            sock.listen(128)
            self.port = sock.getsockname()[1]
            return sock
        raise RuntimeError(f"No free port near {self.port}: {last}")

    def start(self, timeout: float = 10.0) -> str:
        """Start the server and block until it is genuinely serving."""
        if self._thread and self._thread.is_alive():
            return self.url

        sock = self._claim_port()

        def run() -> None:
            try:
                self._loop = asyncio.new_event_loop()
                asyncio.set_event_loop(self._loop)
                set_broadcast_loop(self._loop)
                server = uvicorn.Server(uvicorn.Config(app, log_level="warning", loop="asyncio"))

                async def serve() -> None:
                    # Hand uvicorn the socket we already bound.
                    task = asyncio.create_task(server.serve(sockets=[sock]))
                    while not server.started and not task.done():
                        await asyncio.sleep(0.02)
                    self._ready.set()
                    await task

                self._loop.run_until_complete(serve())
            except BaseException as exc:  # noqa: BLE001 - surfaced to the caller
                self._error = exc
                self._ready.set()

        self._thread = threading.Thread(target=run, daemon=True, name="scribe-live")
        self._thread.start()

        if not self._ready.wait(timeout):
            raise RuntimeError(f"Viewer server did not start within {timeout}s")
        if self._error is not None:
            raise RuntimeError(f"Viewer server failed to start: {self._error}")
        return self.url

    def open_in_browser(self, path: str = "/") -> bool:
        """Pop the board up for the user. False if no browser could be opened."""
        try:
            return webbrowser.open(f"{self.url}{path}")
        except Exception:  # noqa: BLE001 - headless environments are fine
            return False

    def submit(self, coro) -> None:
        """Run a coroutine on the server loop from the MCP thread."""
        if self._loop is not None:
            asyncio.run_coroutine_threadsafe(coro, self._loop)


SERVER = LiveServer()
