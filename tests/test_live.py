"""The websocket contract the viewer relies on."""

import json

from starlette.testclient import TestClient

from scribe.live import app
from scribe.session import REGISTRY


def _open(topic="t"):
    REGISTRY._sessions.clear()
    REGISTRY.current = None
    return REGISTRY.open(topic)


def test_hello_is_a_full_state_sync():
    """The viewer replaces on hello, so hello must carry the whole lesson.

    A reconnect that delivered only a delta -- or a viewer that appended --
    would duplicate every op and narrate the lesson twice.
    """
    session = _open()
    session.draw('box 2,2 6,3 "one"\nsay "first"')

    with TestClient(app).websocket_connect(f"/ws?s={session.sid}") as ws:
        hello = json.loads(ws.receive_text())

    assert hello["type"] == "hello"
    assert hello["ops"].splitlines() == session.ops
    assert hello["grid"] == [session.canvas.grid_w, session.canvas.grid_h]


def test_reconnect_sees_work_done_while_away():
    session = _open()
    session.draw('box 2,2 6,3 "before"')

    client = TestClient(app)
    with client.websocket_connect(f"/ws?s={session.sid}") as ws:
        ws.receive_text()
    assert session.viewers == [], "a dropped viewer must be unsubscribed"

    session.draw('box 2,8 6,3 "while away"')

    with client.websocket_connect(f"/ws?s={session.sid}") as ws:
        hello = json.loads(ws.receive_text())
    assert len(hello["ops"].splitlines()) == 2


def test_ops_are_pushed_as_they_are_drawn():
    session = _open()
    with TestClient(app).websocket_connect(f"/ws?s={session.sid}") as ws:
        ws.receive_text()  # hello
        session.draw('box 2,2 6,3 "live"\nsay "hello"')
        frame = json.loads(ws.receive_text())

    assert frame["type"] == "ops"
    assert 'box 2,2 6,3 "live"' in frame["ops"]


def test_no_open_lesson_closes_the_socket():
    REGISTRY._sessions.clear()
    REGISTRY.current = None
    with TestClient(app).websocket_connect("/ws") as ws:
        assert json.loads(ws.receive_text())["type"] == "closed"


def test_board_size_travels_to_the_viewer():
    session = _open()
    session.canvas.grid_w, session.canvas.grid_h = 40, 30
    with TestClient(app).websocket_connect(f"/ws?s={session.sid}") as ws:
        assert json.loads(ws.receive_text())["grid"] == [40, 30]
