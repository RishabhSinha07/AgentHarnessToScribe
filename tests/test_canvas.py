"""Tests for the behaviours the agent depends on: collisions, erase, pens, pages."""

from pathlib import Path

import pytest

from scribe.canvas import Canvas

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "neural_network.ops"


def test_parses_both_coordinate_forms():
    c = Canvas()
    c.run('box 2 5 9 4 "A"\nbox 15,5 9,4 "B"')
    assert not c.errors
    # A box's centred label belongs to the box; it is not a separate element.
    assert [e.label for e in c.live] == ["A", "B"]
    assert c.live[0].x1 == 11 and c.live[1].x0 == 15


def test_collision_is_reported():
    c = Canvas()
    c.run('text 11,1.5 "How a neural network learns"\ntext 16.5,2.5 "Hidden"')
    assert c.collisions, "overlapping text should be flagged"
    assert "Hidden" in c.collisions[0]


def test_highlights_do_not_collide():
    """A circle is meant to land on top of the thing it points at."""
    c = Canvas()
    c.run('box 17,10 3,2.5 "h2"\ncircle 18.5,11.25 3 "this one"')
    assert not c.collisions


def test_label_too_wide_for_its_box_warns():
    c = Canvas()
    c.run('box 2,2 2,2 "a very long label indeed"')
    assert any("needs" in m for m in c.collisions)


def test_erase_preview_has_no_side_effects():
    c = Canvas()
    c.run('box 17,5 3,2.5 "h1"')
    before = len(c.elements)
    hit = c.erase_preview(15, -0.8, 21, 5.2)
    assert any(e.label == "h1" for e in hit)
    assert len(c.elements) == before
    assert not c.wiped


def test_erase_distinguishes_clipped_from_removed():
    c = Canvas()
    c.run('box 17,5 3,2.5 "h1"\ntext 2,20 "keep me"')
    c.run("erase 15,4 6,2")  # rectangle clipping the top of h1 only
    assert any("CLIPPED" in w for w in c.wiped)
    assert not any("keep me" in w for w in c.wiped)


def test_clipped_element_does_not_block_its_own_redraw():
    c = Canvas()
    c.run('text 11,1.5 "Title"')
    c.run("erase 12,1 3,1")
    c.clear_feedback()
    c.run('text 11,1.5 "Title"')  # the repair
    assert not c.collisions


def test_arrow_label_dodges_an_occupied_slot():
    c = Canvas()
    c.run('box 10,8 8,3 "blocker"')
    c.run('arrow 2,2 26,18 "label"')
    assert not c.collisions


def test_pen_changes_colour_and_rejects_unknown_names():
    c = Canvas()
    c.run("pen blue")
    assert c.pen == (42, 86, 168)
    c.run("pen chartreuse")
    assert any("unknown pen" in e for e in c.errors)


def test_page_starts_clean_but_keeps_history():
    c = Canvas()
    c.run('box 2,2 4,2 "one"')
    c.run("page Second")
    assert c.page == 1
    assert len(c.live) == 1  # just the new page title
    assert len(c.elements) > 1  # page 0 is still in history


def test_occupancy_grows_and_warns():
    c = Canvas()
    assert c.occupancy == 0
    c.run("\n".join(f"box {x},{y} 4,3" for x in range(0, 36, 5) for y in range(0, 27, 4)))
    assert c.occupancy > 0.55
    assert "filling up" in c.state_summary()


def test_gesture_notes_become_narration():
    c = Canvas()
    c.run('circle 10,10 3 "pointing here"\nerase 30,30 1 "wiping"')
    assert c.narration == ["pointing here", "wiping"]


def test_example_lesson_runs_clean():
    c = Canvas()
    c.run(EXAMPLE.read_text())
    assert not c.errors
    assert c.narration


@pytest.mark.parametrize("bad", ["stroke 1,1", "box 1,2", "nonsense 1 2"])
def test_bad_ops_are_collected_not_raised(bad):
    c = Canvas()
    c.run(bad)
    assert c.errors
