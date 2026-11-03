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
    # Tile the whole board, whatever size it is.
    c.run(
        "\n".join(
            f"box {x},{y} 4,3" for x in range(0, c.grid_w - 4, 5) for y in range(0, c.grid_h - 3, 4)
        )
    )
    assert c.occupancy > 0.55
    assert "filling up" in c.state_summary()


def test_grid_size_is_per_canvas():
    small = Canvas(grid_w=40, grid_h=30)
    assert "40x30" in small.state_summary()
    assert Canvas().grid_w == 64


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


# --- agent-facing behaviour -------------------------------------------------


def test_parse_errors_name_the_correct_form():
    """An agent told only 'bad args' guesses again; one told the shape fixes it."""
    c = Canvas()
    c.run("box 1,2")
    assert "box x,y,w,h" in c.errors[0]


def test_unknown_command_lists_the_known_ones():
    c = Canvas()
    c.run("scribble 1 2")
    assert "Known commands" in c.errors[0]
    assert "ellipse" in c.errors[0]


def test_highlight_sizes_the_ring_from_the_target():
    """The agent should never have to guess a radius for a box it cannot see."""
    c = Canvas()
    c.run('box 24,15 14,6 "LOAD BALANCER"\nhighlight "LOAD BALANCER" "this one"')
    assert not c.collisions and not c.errors
    ring = next(e for e in c.live if e.kind == "highlight")
    box = next(e for e in c.live if e.kind == "box")
    assert ring.x0 < box.x0 and ring.x1 > box.x1
    assert ring.y0 < box.y0 and ring.y1 > box.y1
    assert c.narration == ["this one"]


def test_highlight_matches_on_a_substring():
    c = Canvas()
    c.run('box 2,2 10,3 "SERVER 1"\nhighlight "SERVER"')
    assert not c.errors


def test_highlight_of_a_missing_label_lists_what_exists():
    c = Canvas()
    c.run('box 2,2 6,3 "real"\nhighlight "imaginary"')
    assert "nothing labelled" in c.errors[0]
    assert "real" in c.errors[0]


def test_circle_that_cuts_across_a_box_is_flagged():
    """The screenshot bug: a guessed radius slicing through the thing it means."""
    c = Canvas()
    c.run('box 24,15 14,6 "LOAD BALANCER"\ncircle 31,18 5')
    assert c.collisions
    assert "highlight" in c.collisions[0]


def test_circle_that_properly_encloses_is_not_flagged():
    c = Canvas()
    c.run('box 28,16 4,2 "small"\ncircle 30,17 6')
    assert not c.collisions


# --- regressions found in product review ------------------------------------


def test_apostrophes_survive_parsing():
    """shlex treated a bare apostrophe as an opening quote, so natural
    narration failed in Python while the browser accepted it."""
    c = Canvas()
    c.run("say It's fine\ntext 2,2 Claude's model")
    assert not c.errors
    assert c.narration == ["It's fine"]


def test_quoted_labels_still_work():
    c = Canvas()
    c.run('box 2,2 8,3 "a quoted label"')
    assert [e.label for e in c.live] == ["a quoted label"]


def test_fully_off_board_is_an_error():
    c = Canvas()
    c.run('box 70,50 10,4 "gone"')
    assert any("off the" in e for e in c.errors)


def test_partly_off_board_is_flagged():
    c = Canvas()
    c.run('box 60,30 10,10 "half"')
    assert any("runs off" in m for m in c.collisions)


def test_erasing_past_the_edge_is_normal():
    c = Canvas()
    c.run("erase 0,30 64,10")
    assert not c.errors and not c.collisions


def test_occupancy_measures_ink_not_bounding_boxes():
    """One diagonal stroke used to report a full board."""
    c = Canvas()
    c.run("stroke 0,0 64,36")
    assert c.occupancy < 0.1
    assert "filling up" not in c.state_summary()


def test_a_repaired_collision_stops_being_reported():
    """Collisions are derived from the board, so fixing one clears it."""
    c = Canvas()
    c.run('text 11,1.5 "Title"\ntext 12,2.3 "Overlap"')
    assert c.collisions
    c.run('erase 11,1 10,3\ntext 11,1.5 "Title"\ntext 12,6 "Overlap"')
    assert not c.collisions


def test_a_lesson_can_declare_its_own_board():
    c = Canvas()
    assert c.grid_w == 64
    c.run("board 40 30\nbox 2,2 6,3 'a'")
    assert (c.grid_w, c.grid_h) == (40, 30)
    assert not c.errors


def test_board_must_come_first():
    c = Canvas()
    c.run('box 2,2 4,2 "x"\nboard 40 30')
    assert any("before anything is drawn" in e for e in c.errors)


def test_example_declares_the_board_it_was_written_for():
    """The shipped lesson must render on the board it ships with, not a flag."""
    c = Canvas()
    c.run(EXAMPLE.read_text())
    assert (c.grid_w, c.grid_h) == (40, 30)
    assert not c.errors
    assert not c.collisions
