"""Structural checks on the viewer page.

The viewer is browser JS and is verified by driving a real browser. These
guard the wiring that is easy to delete by accident and silent when missing.
"""

from pathlib import Path

import pytest

VIEWER = (Path(__file__).resolve().parents[1] / "src/scribe/viewer/index.html").read_text()


@pytest.mark.parametrize(
    "needle, why",
    [
        ('id="thinking"', "the waiting-for-the-agent indicator"),
        ("tickPresence", "presence runs on its own timer, not inside paint()"),
        ("setInterval(tickPresence", "paint() stops at the live head, so it cannot drive this"),
        ("speech.busy()", "never claim 'thinking' while a voice is still talking"),
        ("warmFrom", "audio is fetched ahead of playback, not when it is reached"),
        ('id="gate"', "browsers refuse speech without a user gesture"),
        ("replaceOps", "hello is a full state sync"),
    ],
)
def test_viewer_wiring_present(needle, why):
    assert needle in VIEWER, f"missing: {why}"


def test_audio_is_warmed_from_three_places():
    """On connect, on the gate opening, and while playing."""
    assert VIEWER.count("warmFrom(") >= 4  # definition + three call sites


def test_narration_does_not_overlay_the_board():
    """It used to float over the canvas and cover the lesson's payoff."""
    assert 'class="narration"' in VIEWER
    # The strip lives after the board element, not inside it.
    board_end = VIEWER.index("</div>", VIEWER.index('class="board"'))
    assert VIEWER.index('class="narration"') > board_end
    assert (
        "position:absolute"
        not in VIEWER[VIEWER.index(".narration {") : VIEWER.index(".narration-text {")]
    )


def test_lesson_can_be_explicitly_finished():
    """Otherwise the board cannot tell thinking from finished."""
    assert '"finished"' in VIEWER
    assert "IDLE_GIVE_UP" in VIEWER, "and a timeout for agents that never say so"


def test_viewer_has_no_external_hosts_but_fonts():
    import re

    hosts = set(re.findall(r"https?://([a-z0-9.-]+)", VIEWER))
    assert hosts <= {"fonts.googleapis.com", "fonts.gstatic.com"}, hosts


@pytest.mark.parametrize(
    "selector",
    [".gate {", ".gate-icon", ".gate-label", ".narration {", ".thinking {", ".stage:fullscreen"],
)
def test_component_styles_survive(selector):
    """Styles have been lost twice to block replacements; pin the load-bearing ones."""
    assert selector in VIEWER


def test_page_declares_its_charset():
    """An exported replay is served by whatever static host the user has, and
    most send no charset -- the arrows and dashes render as mojibake."""
    assert '<meta charset="utf-8">' in VIEWER


def test_timeline_recompute_reanchors_the_clock():
    """Real audio is shorter than the estimate, so later starts shift earlier.
    Leaving the clock at its millisecond value slides it past whole ops, which
    are then never drawn and never spoken."""
    block = VIEWER[VIEWER.index("function recomputeTimeline") :]
    block = block[: block.index("\n  }")]
    assert "curIdx" in block and "curFrac" in block
    assert "oldDur" in block, "the old duration must come from starts[], not duration()"


# --- video recording --------------------------------------------------------


@pytest.mark.parametrize(
    "needle, why",
    [
        ('id="record"', "the record button"),
        ('id="saveVideo"', "saving needs a real click; a timer-fired a.click() is ignored"),
        ("captureStream", "video comes from the canvas"),
        ("createMediaElementSource", "narration is tapped into the recording"),
        ("createMediaStreamDestination", "and mixed into an audio track"),
        ("video/mp4", "prefer mp4 so the file plays anywhere"),
        ("document.body.appendChild(a)", "Firefox ignores a detached anchor"),
    ],
)
def test_recorder_wiring(needle, why):
    assert needle in VIEWER, f"missing: {why}"


def test_recording_requires_a_real_voice():
    """speechSynthesis exposes no stream, so a browser-voice recording would
    be silent. Say so rather than handing over a mute file."""
    assert 'speech.engine === "browser"' in VIEWER
    assert "cannot be recorded" in VIEWER


def test_recording_is_locked_to_real_speed():
    """Capture is real-time; 4x playback would desynchronise the audio."""
    assert "if (recorder.active) return;" in VIEWER
    assert "setSpeed(1)" in VIEWER


def test_captions_are_burned_into_recorded_frames():
    """captureStream() takes the canvas only, so the narration strip below the
    board never reaches the video -- and social feeds autoplay muted."""
    assert "drawCaptionOverlay" in VIEWER
    assert "if (recorder.active) drawCaptionOverlay" in VIEWER


# --- narration and drawing share the clock ----------------------------------


def test_a_spoken_line_does_not_own_the_clock():
    """`say` used to get a slot as long as its audio, so nothing was drawn
    while a line played and nothing was said while the ink went down. A 58s
    recording was a frozen board with a voiceover for half its length."""
    block = VIEWER[VIEWER.index("function duration(op)") :]
    block = block[: block.index("\n  }")]
    assert 'case "say":    return SAY_LEAD;' in block
    assert "spokenOp" not in VIEWER, "speech must not stretch a visual slot any more"
    # A gesture is as long as the hand takes to draw the ring. Its note keeps
    # being spoken over whatever comes next, like any other line.
    for cmd, ms in (("highlight", 820), ("circle", 780), ("strike", 560), ("erase", 430)):
        assert f'case "{cmd}":' in block
        assert f"return {ms};" in block, f"{cmd} is still stretched by its note"


def test_layout_holds_a_line_only_for_the_voice():
    """The one hard constraint is that two lines must not overlap. Everything
    else keeps drawing underneath."""
    block = VIEWER[VIEWER.index("function layout()") :]
    block = block[: block.index("\n  }")]
    assert "voiceFree" in block
    assert "Math.max(cursor, voiceFree)" in block
    assert "VOICE_GAP" in block
    assert "Math.max(cursor, voiceFree ? voiceFree - VOICE_GAP : 0)" in block, (
        "the last line has to be allowed to finish after the ink stops"
    )


def test_the_spoken_line_is_read_off_the_timeline_not_the_renderer():
    """It was taken from whichever op was mid-draw, which cannot be right once
    a line outlives its own op -- the caption vanished and the voice stopped
    being tracked while the drawing carried on."""
    assert "function spokenAt(t)" in VIEWER
    assert "const speaking = spokenAt(time);" in VIEWER
    render = VIEWER[VIEWER.index("function renderAt(time)") : VIEWER.index("function spokenAt")]
    assert "speaking" not in render, "renderAt must not decide what is being said"
