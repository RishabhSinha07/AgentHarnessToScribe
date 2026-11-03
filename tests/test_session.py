"""Session behaviour the agent depends on: op log, undo, viewer fan-out."""

import asyncio
import json

import pytest

from scribe import Canvas
from scribe.session import Registry, Session


def test_draw_logs_ops_and_skips_comments():
    s = Session("t")
    s.draw('# a comment\nbox 2,2 4,2 "one"\n\nsay "hello"')
    assert s.ops == ['box 2,2 4,2 "one"', 'say "hello"']


def test_undo_rolls_back_one_scene():
    s = Session("t")
    s.draw('box 2,2 4,2 "one"')
    s.draw('box 2,8 4,2 "two"')
    s.undo()
    assert [e.label for e in s.canvas.live] == ["one"]
    assert s.ops == ['box 2,2 4,2 "one"']


def test_undo_clamps_past_the_beginning():
    s = Session("t")
    s.draw('box 2,2 4,2 "one"')
    assert "Undid 1 scene" in s.undo(10)
    assert s.undo() == "Nothing to undo."


def test_undo_rebuilds_rather_than_un_erasing():
    """Erase destroys pixels, so undo replays the surviving log instead."""
    s = Session("t")
    s.draw('box 2,2 4,2 "keep"\nbox 2,8 4,2 "doomed"')
    s.draw("erase 0,6 10,6")
    assert any("doomed" in w for w in s.canvas.wiped)
    s.undo()
    assert sorted(e.label for e in s.canvas.live) == ["doomed", "keep"]


def test_viewer_gets_the_backlog_on_subscribe():
    s = Session("t")
    s.draw('box 2,2 4,2 "before"')
    q = s.subscribe()
    assert q.get_nowait() == 'box 2,2 4,2 "before"'


def test_undo_sends_a_reset_to_viewers():
    s = Session("t")
    q = s.subscribe()
    s.draw('box 2,2 4,2 "one"')
    while not q.empty():
        q.get_nowait()
    s.undo()
    msg = q.get_nowait()
    assert msg.startswith("\x00")  # control frame, not an op
    assert '"reset"' in msg


def test_registry_requires_an_open_lesson():
    r = Registry()
    with pytest.raises(LookupError):
        r.require_current()
    r.open("topic")
    assert r.require_current().topic == "topic"


def test_slow_viewer_is_dropped_not_blocking():
    s = Session("t")
    q = asyncio.Queue(maxsize=1)
    s.viewers.append(q)
    s.draw('box 1,1 2,2 "a"\nbox 4,4 2,2 "b"\nbox 8,8 2,2 "c"')
    assert q not in s.viewers


def test_ops_that_failed_to_parse_are_not_broadcast():
    """The student must not hear a line the canvas never recorded."""
    s = Session("t")
    s.draw('box 2,2 6,3 "good"\nbox 1,2\nsay "fine"')
    assert s.ops == ['box 2,2 6,3 "good"', 'say "fine"']


def test_undo_keeps_the_board_size():
    s = Session("t")
    s.canvas = Canvas(grid_w=40, grid_h=30)
    s.draw('box 2,2 4,2 "a"')
    s.draw('box 2,8 4,2 "b"')
    s.undo()
    assert (s.canvas.grid_w, s.canvas.grid_h) == (40, 30)


def test_reset_frame_carries_the_grid():
    s = Session("t")
    s.canvas = Canvas(grid_w=40, grid_h=30)
    q = s.subscribe()
    s.draw('box 2,2 4,2 "a"')
    while not q.empty():
        q.get_nowait()
    s.undo()
    msg = json.loads(q.get_nowait()[1:])
    assert msg["grid"] == [40, 30]
