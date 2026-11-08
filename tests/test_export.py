"""A lesson has to be able to leave the process."""

import json

import pytest

from scribe import export
from scribe.session import Plan, Session


@pytest.fixture
def lesson(tmp_path):
    s = Session("How a cache miss is served")
    s.plan = Plan(
        idea="A miss costs a round trip",
        hook="Why is the first load slow?",
        beats=["ask the cache", "miss"],
        payoff="Warm it first.",
    )
    s.draw('box 4,8 14,3 "client"\nbox 26,8 14,3 "cache"\nsay "Asks the cache first."')
    s.draw('pan 36\nbox 4,44 14,3 "origin"\nsay "Then the full trip."')
    return s, tmp_path


def test_png_covers_every_screen_not_just_the_last(lesson):
    """The board grew to two screens; the image must contain both."""
    from PIL import Image

    s, tmp = lesson
    img = Image.open(export.as_png(s, tmp / "board.png"))
    assert img.height == s.canvas.grid_h * 24 * 2


def test_replay_is_self_contained(lesson):
    import re

    s, tmp = lesson
    html = export.as_replay(s, tmp / "r.html").read_text()
    assert "__OPS__" in html and "bootStandalone" in html
    assert s.topic in html
    hosts = set(re.findall(r"https?://([a-z0-9.-]+)", html))
    assert hosts <= {"fonts.googleapis.com", "fonts.gstatic.com"}, hosts
    assert '<meta charset="utf-8">' in html, "static hosts send no charset"


def test_ops_json_carries_the_plan(lesson):
    s, tmp = lesson
    data = json.loads(export.as_json(s, tmp / "o.json").read_text())
    assert data["ops"] == s.ops
    assert data["plan"]["hook"] == "Why is the first load slow?"
    assert data["panels"] == 2


def test_save_all_writes_three_files(lesson):
    s, tmp = lesson
    paths = export.save_all(s, tmp)
    assert set(paths) == {"png", "replay", "ops"}
    assert all(p.exists() and p.stat().st_size > 0 for p in paths.values())


def test_the_published_demo_is_current_and_self_contained():
    """docs/index.html is a baked copy of the viewer, so it goes stale in
    silence. Pin the features it must have picked up."""
    import re
    from pathlib import Path

    docs = Path(__file__).resolve().parents[1] / "docs" / "index.html"
    html = docs.read_text()
    for needle in [
        '<meta charset="utf-8">',
        'id="gate"',
        'class="narration"',
        "cameraAt",
        "drawIcon",
        "bootStandalone",
        "__OPS__",
    ]:
        assert needle in html, f"demo is stale: missing {needle}"
    hosts = set(re.findall(r"https?://([a-z0-9.-]+)", html))
    assert hosts <= {"fonts.googleapis.com", "fonts.gstatic.com"}, hosts


def test_the_demo_lesson_draws_clean():
    from pathlib import Path

    from scribe import Canvas

    ops = Path(__file__).resolve().parents[1] / "examples" / "first_load.ops"
    c = Canvas()
    c.run(ops.read_text())
    assert not c.errors
    assert not c.collisions
