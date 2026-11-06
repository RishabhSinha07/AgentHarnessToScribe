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


def test_subtitle_and_thinking_never_both_shown():
    """They share an anchor at the bottom of the board."""
    assert 'capEl.classList.remove("on")' in VIEWER
    assert 'thinkingEl.classList.remove("on")' in VIEWER


def test_viewer_has_no_external_hosts_but_fonts():
    import re

    hosts = set(re.findall(r"https?://([a-z0-9.-]+)", VIEWER))
    assert hosts <= {"fonts.googleapis.com", "fonts.gstatic.com"}, hosts
